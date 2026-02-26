"""
Hand gesture detection from ESP32-CAM stream for LED strip control.

Uses MediaPipe Hands for landmark detection and classifies gestures
based on finger extension states. LED strip actions (when --led-url set):
  OPEN_HAND     → ON           OK            → OFF
  HANG_LOOSE    → rainbow     PEACE         → ripple (position-driven 1D water ripples)
  ROCK          → strobe      THREE         → breathe (pulse)
  THUMB_UP/DOWN → BRIGHT+/-   POINTER       → fun   PINCH → explosion
  One hand: whole strip follows that gesture. Two hands (mode gestures): left half = hand0, right half = hand1.

Gesture definitions (tuned for comfort):
  Thumb up/down: Thumb is "extended" when its TIP is not past the index-root line (wrist to INDEX_MCP).
                 Only the thumb tip is used (not the IP joint) so the pose is easier to hold.
  PEACE: index + middle extended, ring and pinky folded, thumb folded (not thumb).
  THREE: index + middle + thumb extended, ring and pinky folded.
  ROCK: index + pinky extended, middle and ring folded (thumb optional).
  POINTER: index only (other fingers folded). L: thumb + index. HANG_LOOSE: thumb + pinky.

Controls: q = quit, m = toggle mirror.
Source: --url for ESP32-CAM stream (default) or --webcam for local webcam.
"""

import sys
import os

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

print("hand_gesture: loading...", flush=True)

import argparse
import math
import queue
import threading
import time

import cv2
import mediapipe as mp
import numpy as np

try:
    import requests
except ImportError:
    requests = None

from stream_reader import add_stream_url_arg, read_frames, read_frames_webcam

# MediaPipe 0.10+ tasks API
BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

# Connections for drawing hand skeleton (21-point MediaPipe hand)
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),   # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),   # index
    (0, 9), (9, 10), (10, 11), (11, 12),   # middle
    (0, 13), (13, 14), (14, 15), (15, 16),   # ring
    (0, 17), (17, 18), (18, 19), (19, 20),   # pinky
    (5, 9), (9, 13), (13, 17),   # palm
)

# Landmark indices
WRIST = 0
THUMB_CMC, THUMB_MCP, THUMB_IP, THUMB_TIP = 1, 2, 3, 4
INDEX_MCP, INDEX_PIP, INDEX_DIP, INDEX_TIP = 5, 6, 7, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_DIP, MIDDLE_TIP = 9, 10, 11, 12
RING_MCP, RING_PIP, RING_DIP, RING_TIP = 13, 14, 15, 16
PINKY_MCP, PINKY_PIP, PINKY_DIP, PINKY_TIP = 17, 18, 19, 20

GESTURE_ACTIONS = {
    "OPEN_HAND": "ON",
    "OK": "OFF",
    "HANG_LOOSE": "RAINBOW",   # thumb + pinky = groovy/chill = rainbow mode
    "PEACE": "RIPPLE",         # index + middle = one-shot wave along strip
    "ROCK": "STROBE",          # index + pinky = strobe/party mode
    "THREE": "BREATHE",        # index + middle + ring = breathe/pulse mode
    "THUMB_UP": "BRIGHT+",
    "THUMB_DOWN": "BRIGHT-",
    "POINTER": "SELECT",
    "PINCH": "EXPLOSION",
}

COLOR_CYAN = (255, 255, 0)
COLOR_YELLOW = (0, 255, 255)
COLOR_GREEN = (0, 255, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_BG = (40, 40, 40)

DEBOUNCE_FRAMES = 3
LED_DEBOUNCE_FRAMES = 5  # require this many same gesture before changing strip (reduces flicker)
FUN_THROTTLE_SEC = 1.0 / 15.0  # max 15 Hz for /fun position updates
BRIGHTNESS_THROTTLE_SEC = 0.25  # min interval between brightness up/down steps
EXPLOSION_THROTTLE_SEC = 0.5   # min interval between pinch-triggered explosions
RIPPLE_THROTTLE_SEC = 1.0 / 15.0  # ~15 Hz for PEACE ripple position updates (like fun)
RIPPLE_MIN_VELOCITY = 3  # minimum movement to create a ripple; holding still sends nothing (existing ripples fade)

# Hand landmarker model (downloaded on first run)
HAND_LANDMARKER_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)


def _get_model_path():
    """Return path to hand_landmarker.task, downloading if needed."""
    # When packaged as .exe (PyInstaller), look in bundle then next to executable
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        path = os.path.join(base, "hand_landmarker.task")
        if os.path.isfile(path):
            return path
        path = os.path.join(os.path.dirname(sys.executable), "hand_landmarker.task")
        if os.path.isfile(path):
            return path
        download_dir = os.path.dirname(sys.executable)
        path = os.path.join(download_dir, "hand_landmarker.task")
        try:
            import urllib.request
            print("Downloading hand_landmarker.model (one-time)...", flush=True)
            urllib.request.urlretrieve(HAND_LANDMARKER_MODEL_URL, path)
            return path
        except Exception as e:
            raise RuntimeError(
                f"Could not download hand_landmarker.task: {e}. "
                "Download manually from MediaPipe and place hand_landmarker.task next to this executable."
            ) from e
    path = os.path.join(_script_dir, "hand_landmarker.task")
    if os.path.isfile(path):
        return path
    try:
        import urllib.request
        print("Downloading hand_landmarker.model (one-time)...", flush=True)
        urllib.request.urlretrieve(HAND_LANDMARKER_MODEL_URL, path)
        return path
    except Exception as e:
        raise FileNotFoundError(
            f"Could not download hand_landmarker.task: {e}. "
            "Download manually from the MediaPipe site and place hand_landmarker.task in the script directory."
        ) from e


def _draw_hand_landmarks(frame, landmarks_list, height, width, color=(0, 255, 0), thickness=2, mirror=False):
    """Draw hand skeleton and points on frame. landmarks_list: list of 21 normalized (x,y,z) landmarks. If mirror, flip x so text stays readable."""
    if not landmarks_list or len(landmarks_list) < 21:
        return
    pts = []
    for lm in landmarks_list:
        x = int((1 - lm.x) * width if mirror else lm.x * width)
        y = int(lm.y * height)
        pts.append((x, y))
    for (i, j) in HAND_CONNECTIONS:
        if i < len(pts) and j < len(pts):
            cv2.line(frame, pts[i], pts[j], color, thickness)
    for (x, y) in pts:
        cv2.circle(frame, (x, y), thickness + 1, color, -1)


def _dist(a, b):
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _side_of_line(ax, ay, bx, by, px, py):
    """Signed side of point (px,py) relative to line A->B. Positive = one side, negative = other."""
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def _past_index_root_line(lm, px, py):
    """True if (px, py) is on the palm side of the line from wrist to index root (INDEX_MCP).
    Palm side = same side as MIDDLE_MCP. More sensitive than middle-root line (thumb down earlier)."""
    ax, ay = lm[WRIST].x, lm[WRIST].y
    bx, by = lm[INDEX_MCP].x, lm[INDEX_MCP].y
    ref = _side_of_line(ax, ay, bx, by, lm[MIDDLE_MCP].x, lm[MIDDLE_MCP].y)  # palm side sign
    pt = _side_of_line(ax, ay, bx, by, px, py)
    return (ref >= 0 and pt >= 0) or (ref <= 0 and pt <= 0)


def _landmarks_wrapper(landmarks_list):
    """Wrap list of 21 landmarks so they have .landmark for legacy-style access."""
    class Wrapper:
        landmark = landmarks_list
    return Wrapper()


def get_finger_states(hand_landmarks, handedness_label):
    """Return [thumb, index, middle, ring, pinky] booleans (True = extended).
    Thumb: extended when thumb TIP is not past the index-root line (wrist->INDEX_MCP); only tip is used.
    Other fingers: extended if tip above PIP or tip farther from wrist than PIP (works at different angles).
    """
    lm = hand_landmarks.landmark if hasattr(hand_landmarks, "landmark") else hand_landmarks
    wrist = lm[WRIST]

    # Thumb: "down" when thumb tip is past the index-root line (wrist->INDEX_MCP); else extended (tip only, for comfort)
    tip_past_index_line = _past_index_root_line(lm, lm[THUMB_TIP].x, lm[THUMB_TIP].y)
    thumb = not tip_past_index_line

    # Index, middle, ring, pinky: extended if tip is above PIP (finger up) OR tip farther from wrist than PIP (works sideways/pointing)
    def finger_extended(tip_idx, pip_idx):
        tip_above_pip = lm[tip_idx].y < lm[pip_idx].y
        tip_farther = _dist(wrist, lm[tip_idx]) > _dist(wrist, lm[pip_idx]) * 1.05
        return tip_above_pip or tip_farther

    index = finger_extended(INDEX_TIP, INDEX_PIP)
    middle = finger_extended(MIDDLE_TIP, MIDDLE_PIP)
    ring = finger_extended(RING_TIP, RING_PIP)
    pinky = finger_extended(PINKY_TIP, PINKY_PIP)

    return [thumb, index, middle, ring, pinky]


def classify_gesture(finger_states, hand_landmarks):
    """Map finger extension pattern to a named gesture.
    Order: pinch/OK first, then FIST, OPEN_HAND, thumb-only, POINTER, PEACE, THREE, ROCK, L, HANG_LOOSE.
    PEACE = index+middle, thumb folded. THREE = index+middle+thumb. ROCK = index+pinky (thumb optional)."""
    thumb, index, middle, ring, pinky = finger_states
    n_up = sum(finger_states)
    lm = hand_landmarks.landmark if hasattr(hand_landmarks, "landmark") else hand_landmarks

    # Pinch: thumb tip and index tip very close together
    pinch_dist = _dist(lm[THUMB_TIP], lm[INDEX_TIP])
    hand_size = _dist(lm[WRIST], lm[MIDDLE_MCP])

    if pinch_dist < hand_size * 0.3 and not middle and not ring and not pinky:
        return "PINCH"

    # OK sign: thumb+index form circle, other fingers extended
    if pinch_dist < hand_size * 0.3 and middle and ring and pinky:
        return "OK"

    if n_up == 0:
        return "FIST"
    # 4 or 5 fingers up = open hand (thumb often missed when palm faces camera)
    if n_up >= 4:
        return "OPEN_HAND"
    if thumb and not any([index, middle, ring, pinky]):
        # Thumb down = tip has higher y than IP (image y increases downward)
        if lm[THUMB_TIP].y > lm[THUMB_IP].y:
            return "THUMB_DOWN"
        return "THUMB_UP"
    # POINTER = index up, other fingers (except thumb) down; thumb can be up (avoids L when pointing)
    if index and not middle and not ring and not pinky:
        return "POINTER"
    # PEACE = index + middle extended, ring and pinky folded
    if index and middle and not ring and not pinky and not thumb:
        return "PEACE"
    # THREE = middle ring and pinky
    if index and middle and thumb and not pinky and not ring:
        return "THREE"
    # ROCK = index + pinky extended, middle and ring folded 
    if index and pinky and not middle and not ring:
        return "ROCK"
    if thumb and index and not any([middle, ring, pinky]):
        return "L"
    if thumb and pinky and not any([index, middle, ring]):
        return "HANG_LOOSE"

    return "UNKNOWN"


def _draw_hud(frame, gesture, action, fingers, hand_label, confidence, y_offset=0):
    """Draw gesture info overlay with drop shadow for readability.
    Uses ASCII for finger state so it renders on all systems (Unicode arrows often show as '-' in OpenCV)."""
    finger_labels = ["T", "I", "M", "R", "P"]
    # Ensure fingers is a list of 5 bools; use ASCII ^/v so OpenCV putText displays correctly
    if not isinstance(fingers, (list, tuple)) or len(fingers) != 5:
        finger_str = "?"
    else:
        finger_str = " ".join(
            f"{n}{'^' if up else 'v'}" for n, up in zip(finger_labels, fingers)
        )

    lines = [
        (f"{hand_label} hand ({confidence:.0%})", COLOR_WHITE),
        (f"Gesture: {gesture}", COLOR_CYAN),
        (f"Action:  {action}", COLOR_YELLOW),
        (f"Fingers: {finger_str}", COLOR_GREEN),
    ]

    x0, y0, line_h = 10, 30 + y_offset, 28
    for i, (text, color) in enumerate(lines):
        y = y0 + i * line_h
        cv2.putText(frame, text, (x0 + 1, y + 1), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
        cv2.putText(frame, text, (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)


def _placeholder_frame(width=640, height=480, text="Connecting..."):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = COLOR_BG
    cv2.putText(
        img, text, (width // 4, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (100, 200, 100), 2,
    )
    return img


def parse_args():
    p = argparse.ArgumentParser(description="Hand gesture detection from ESP32-CAM stream.")
    add_stream_url_arg(p)
    p.add_argument("--webcam", action="store_true",
                   help="Use local webcam instead of ESP32-CAM stream.")
    p.add_argument("--camera-index", type=int, default=0,
                   help="Webcam device index (default: 0). Only used with --webcam.")
    p.add_argument("--scale", type=float, default=2.0, help="Display scale (default: 2.0)")
    p.add_argument("--max-hands", type=int, default=2, help="Max hands to track (default: 2)")
    p.add_argument("--min-confidence", type=float, default=0.5,
                   help="Min detection confidence 0-1 (default: 0.5)")
    p.add_argument("--no-window", action="store_true", help="Headless: print gestures only")
    p.add_argument("--mirror", action="store_true", help="Mirror display horizontally")
    p.add_argument("--led-url", type=str, default="",
                   help="ESP32 LED strip base URL. ON (open hand)=lights on, OFF (fist)=lights off.")
    return p.parse_args()


def _send_led_command(base_url, on: bool, timeout=2.0):
    """Send GET request to ESP32 LED strip: /on or /off. Returns True if request succeeded."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    url = base_url + "/on" if on else base_url + "/off"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        print(f"[led] {('on' if on else 'off')} -> {url}", flush=True)
        return True
    except requests.RequestException as e:
        print(f"[led] {url}: {e}", flush=True)
        return False


def _gesture_to_mode(gesture):
    """Map gesture to strip mode string for /mode and /split: 'on', 'off', 'rainbow', 'strobe', 'breathe'."""
    if gesture == "OPEN_HAND":
        return "on"
    if gesture == "OK":
        return "off"
    if gesture == "HANG_LOOSE":
        return "rainbow"
    if gesture == "ROCK":
        return "strobe"
    if gesture == "THREE":
        return "breathe"
    return None


def _send_led_mode(base_url, mode: str, timeout=2.0):
    """Send GET /mode?m=on|off|rainbow|strobe|breathe to set whole strip mode. Returns True if request succeeded."""
    if not base_url or not requests or mode not in ("on", "off", "rainbow", "strobe", "breathe"):
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    try:
        r = requests.get(f"{base_url}/mode?m={mode}", timeout=timeout)
        r.raise_for_status()
        print(f"[led] mode {mode} -> {base_url}/mode", flush=True)
        return True
    except requests.RequestException as e:
        print(f"[led] mode {base_url}: {e}", flush=True)
        return False


def _send_led_split(base_url, left_mode: str, right_mode: str, timeout=2.0):
    """Send GET /split?left=...&right=... to set left half and right half modes. Returns True if request succeeded."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    for m in (left_mode, right_mode):
        if m not in ("on", "off", "rainbow", "strobe", "breathe"):
            return False
    try:
        r = requests.get(f"{base_url}/split?left={left_mode}&right={right_mode}", timeout=timeout)
        r.raise_for_status()
        print(f"[led] split left={left_mode} right={right_mode} -> {base_url}/split", flush=True)
        return True
    except requests.RequestException as e:
        print(f"[led] split {base_url}: {e}", flush=True)
        return False


def _send_led_rainbow(base_url, timeout=2.0):
    """Send GET /rainbow to set whole strip to rainbow (groovy) mode. Returns True if request succeeded."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    try:
        r = requests.get(f"{base_url}/rainbow", timeout=timeout)
        r.raise_for_status()
        print(f"[led] rainbow -> {base_url}/rainbow", flush=True)
        return True
    except requests.RequestException as e:
        print(f"[led] rainbow {base_url}: {e}", flush=True)
        return False


def _send_led_ripple(base_url, hue: int = 0, position_pct: int = None, direction: int = 0, velocity: int = 0, timeout=1.0):
    """Send GET /ripple?h=&p=&dir=&v= to add a position-driven ripple (PEACE gesture).
    position_pct 0-100 = hand x; direction 1=right -1=left; velocity 0-50 = movement speed (larger reach)."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    hue = max(0, min(360, hue))
    if position_pct is not None:
        p = max(0, min(100, position_pct))
        dir_val = 1 if direction > 0 else (-1 if direction < 0 else 1)
        v = max(0, min(50, velocity))
        url = f"{base_url}/ripple?p={p}&h={hue}&dir={dir_val}&v={v}"
    else:
        url = f"{base_url}/ripple?h={hue}"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] ripple {base_url}: {e}", flush=True)
        return False


def _send_led_fun(base_url, position_pct: int, hue: int, timeout=1.0):
    """Send GET /fun?p=position_pct&h=hue for one moving cluster (fun mode)."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    position_pct = max(0, min(100, position_pct))
    hue = max(0, min(360, hue))
    url = f"{base_url}/fun?p={position_pct}&h={hue}"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] fun {url}: {e}", flush=True)
        return False


def _send_led_fun_two(base_url, p1: int, h1: int, p2: int, h2: int, timeout=1.0):
    """Send GET /fun?p=&h=&p2=&h2= for two islands of light (two hands POINTER)."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    p1, p2 = max(0, min(100, p1)), max(0, min(100, p2))
    h1, h2 = max(0, min(360, h1)), max(0, min(360, h2))
    url = f"{base_url}/fun?p={p1}&h={h1}&p2={p2}&h2={h2}"
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] fun two {url}: {e}", flush=True)
        return False


def _send_led_brightness_up(base_url, timeout=1.0):
    """Send GET /brightness/up to increase strip brightness."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    try:
        r = requests.get(base_url + "/brightness/up", timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] brightness/up: {e}", flush=True)
        return False


def _send_led_brightness_down(base_url, timeout=1.0):
    """Send GET /brightness/down to decrease strip brightness (ESP32 enforces minimum)."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    try:
        r = requests.get(base_url + "/brightness/down", timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] brightness/down: {e}", flush=True)
        return False


def _send_led_explosion(base_url, p: int, h: int, timeout=1.0):
    """Send GET /explosion?p=&h= to trigger explosion from hand 0 position."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    p, h = max(0, min(100, p)), max(0, min(360, h))
    try:
        r = requests.get(f"{base_url}/explosion?p={p}&h={h}", timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] explosion: {e}", flush=True)
        return False


def _send_led_explosion_hand1(base_url, p2: int, h2: int, timeout=1.0):
    """Send GET /explosion?p2=&h2= to trigger explosion from hand 1 position only."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    p2, h2 = max(0, min(100, p2)), max(0, min(360, h2))
    try:
        r = requests.get(f"{base_url}/explosion?p2={p2}&h2={h2}", timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] explosion hand1: {e}", flush=True)
        return False


def _send_led_explosion_two(base_url, p1: int, h1: int, p2: int, h2: int, timeout=1.0):
    """Send GET /explosion?p1=&h1=&p2=&h2= to trigger explosions from both hands."""
    if not base_url or not requests:
        return False
    base_url = base_url.strip().rstrip("/")
    if not base_url:
        return False
    p1, p2 = max(0, min(100, p1)), max(0, min(100, p2))
    h1, h2 = max(0, min(360, h1)), max(0, min(360, h2))
    try:
        r = requests.get(f"{base_url}/explosion?p1={p1}&h1={h1}&p2={p2}&h2={h2}", timeout=timeout)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"[led] explosion two: {e}", flush=True)
        return False


def _worker(args, frame_queue, stop_event, mirror_ref=None):
    """Read stream, detect hands, classify gestures, push annotated frames."""
    model_path = _get_model_path()
    options = HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=VisionRunningMode.VIDEO,
        num_hands=args.max_hands,
        min_hand_detection_confidence=args.min_confidence,
    )
    landmarker = HandLandmarker.create_from_options(options)

    debounce_hist = {}
    stable = {}
    frame_timestamp_ms = 0
    last_led_state = None  # "on", "off", "rainbow", or None (legacy for fun/explosion invalidation)
    last_whole_mode = None  # "on", "off", "rainbow" when one hand sets whole strip
    last_split = None  # (left_mode, right_mode) when two hands set split
    led_gesture_hist = {}  # per-hand: recent gestures (longer debounce for strip)
    last_fun_sent = 0.0
    last_brightness_sent = 0.0
    last_explosion_sent = 0.0
    last_ripple_sent = 0.0
    ripple_hue = 0
    last_peace_x = {}  # hand index -> last x (0-100) for ripple direction/velocity

    try:
        for frame in _get_frame_source(args):
            if stop_event.is_set():
                break

            mirror = mirror_ref[0] if mirror_ref else False
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            result = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
            frame_timestamp_ms += 33  # ~30 fps

            out = frame.copy()
            if mirror:
                out = cv2.flip(out, 1)
            hand_data = []
            first_hand_landmarks = None
            second_hand_landmarks = None

            if result.hand_landmarks:
                for i, (landmarks_list, handedness_list) in enumerate(zip(result.hand_landmarks, result.handedness)):
                    if i == 0:
                        first_hand_landmarks = landmarks_list
                    elif i == 1:
                        second_hand_landmarks = landmarks_list
                    _draw_hand_landmarks(out, landmarks_list, h, w, mirror=mirror)

                    label = handedness_list[0].display_name if handedness_list else "Unknown"
                    score = handedness_list[0].score if handedness_list else 0.0

                    hand_lm = _landmarks_wrapper(landmarks_list)
                    fingers = get_finger_states(hand_lm, label)
                    gesture = classify_gesture(fingers, hand_lm)

                    hist = debounce_hist.get(i, [])
                    hist.append(gesture)
                    if len(hist) > DEBOUNCE_FRAMES:
                        hist = hist[-DEBOUNCE_FRAMES:]
                    debounce_hist[i] = hist

                    if len(hist) == DEBOUNCE_FRAMES and len(set(hist)) == 1:
                        stable[i] = gesture

                    display_gesture = stable.get(i, gesture)
                    action = GESTURE_ACTIONS.get(display_gesture, "-")
                    hand_data.append((display_gesture, action, fingers, label, score))

                # LED strip: open palm = on, OK = off; thumbs = brightness; POINTER = fun (1 or 2 islands)
                if args.led_url and hand_data:
                    for i in range(len(hand_data)):
                        g = hand_data[i][0]
                        hist = led_gesture_hist.get(i, [])
                        hist.append(g)
                        if len(hist) > LED_DEBOUNCE_FRAMES:
                            hist = hist[-LED_DEBOUNCE_FRAMES:]
                        led_gesture_hist[i] = hist
                    for k in list(led_gesture_hist):
                        if k >= len(hand_data):
                            del led_gesture_hist[k]

                    g0_stable = None
                    if len(led_gesture_hist.get(0, [])) == LED_DEBOUNCE_FRAMES and len(set(led_gesture_hist[0])) == 1:
                        g0_stable = led_gesture_hist[0][0]
                    g1_stable = None
                    if len(led_gesture_hist.get(1, [])) == LED_DEBOUNCE_FRAMES and len(set(led_gesture_hist[1])) == 1:
                        g1_stable = led_gesture_hist[1][0]

                    # Mode gestures: OPEN_HAND, OK, HANG_LOOSE, ROCK, THREE → on/off/rainbow/strobe/breathe. One hand = whole strip; two hands = split.
                    m0 = _gesture_to_mode(g0_stable) if g0_stable else None
                    m1 = _gesture_to_mode(g1_stable) if g1_stable else None
                    if g0_stable and g1_stable and m0 is not None and m1 is not None:
                        if last_split != (m0, m1):
                            if _send_led_split(args.led_url, m0, m1):
                                last_split = (m0, m1)
                                last_whole_mode = None
                                last_led_state = None
                    elif m0 is not None:
                        if last_whole_mode != m0:
                            if _send_led_mode(args.led_url, m0):
                                last_whole_mode = m0
                                last_split = None
                                last_led_state = None
                    else:
                        now = time.time()
                        if g0_stable == "THUMB_UP" and now - last_brightness_sent >= BRIGHTNESS_THROTTLE_SEC:
                            if _send_led_brightness_up(args.led_url):
                                last_brightness_sent = now
                        elif g0_stable == "THUMB_DOWN" and now - last_brightness_sent >= BRIGHTNESS_THROTTLE_SEC:
                            if _send_led_brightness_down(args.led_url):
                                last_brightness_sent = now
                        if now - last_explosion_sent >= EXPLOSION_THROTTLE_SEC:
                            pinch0 = g0_stable == "PINCH" and first_hand_landmarks and len(first_hand_landmarks) > INDEX_TIP
                            pinch1 = g1_stable == "PINCH" and second_hand_landmarks and len(second_hand_landmarks) > INDEX_TIP
                            if pinch0 and pinch1:
                                lm0 = first_hand_landmarks[INDEX_TIP]
                                lm1 = second_hand_landmarks[INDEX_TIP]
                                p1 = max(0, min(100, int(lm0.x * 100)))
                                h1 = max(0, min(360, int(lm0.y * 360) % 360))
                                p2 = max(0, min(100, int(lm1.x * 100)))
                                h2 = max(0, min(360, int(lm1.y * 360) % 360))
                                if _send_led_explosion_two(args.led_url, p1, h1, p2, h2):
                                    last_explosion_sent = now
                                    last_led_state = last_whole_mode = last_split = None
                            elif pinch0:
                                lm = first_hand_landmarks[INDEX_TIP]
                                p0 = max(0, min(100, int(lm.x * 100)))
                                h0 = max(0, min(360, int(lm.y * 360) % 360))
                                if _send_led_explosion(args.led_url, p0, h0):
                                    last_explosion_sent = now
                                    last_led_state = last_whole_mode = last_split = None
                            elif pinch1:
                                lm = second_hand_landmarks[INDEX_TIP]
                                p1 = max(0, min(100, int(lm.x * 100)))
                                h1 = max(0, min(360, int(lm.y * 360) % 360))
                                if _send_led_explosion_hand1(args.led_url, p1, h1):
                                    last_explosion_sent = now
                                    last_led_state = last_whole_mode = last_split = None
                        if (g0_stable == "POINTER" and g1_stable == "POINTER" and first_hand_landmarks
                                and second_hand_landmarks and len(first_hand_landmarks) > INDEX_TIP
                                and len(second_hand_landmarks) > INDEX_TIP):
                            if now - last_fun_sent >= FUN_THROTTLE_SEC:
                                lm0 = first_hand_landmarks[INDEX_TIP]
                                lm1 = second_hand_landmarks[INDEX_TIP]
                                p1 = max(0, min(100, int(lm0.x * 100)))
                                h1 = max(0, min(360, int(lm0.y * 360) % 360))
                                p2 = max(0, min(100, int(lm1.x * 100)))
                                h2 = max(0, min(360, int(lm1.y * 360) % 360))
                                if _send_led_fun_two(args.led_url, p1, h1, p2, h2):
                                    last_fun_sent = now
                                    last_led_state = last_whole_mode = last_split = None
                        elif g0_stable == "POINTER" and first_hand_landmarks and len(first_hand_landmarks) > INDEX_TIP:
                            if now - last_fun_sent >= FUN_THROTTLE_SEC:
                                lm = first_hand_landmarks[INDEX_TIP]
                                position_pct = max(0, min(100, int(lm.x * 100)))
                                hue = max(0, min(360, int(lm.y * 360) % 360))
                                if _send_led_fun(args.led_url, position_pct, hue):
                                    last_fun_sent = now
                                    last_led_state = last_whole_mode = last_split = None
                        # PEACE: send ripple only when hand is moving; still hand = no new ripples (they fade out)
                        if g0_stable == "PEACE" and first_hand_landmarks and len(first_hand_landmarks) > INDEX_TIP and now - last_ripple_sent >= RIPPLE_THROTTLE_SEC:
                            lm = first_hand_landmarks[INDEX_TIP]
                            pos_pct = max(0, min(100, int(lm.x * 100)))
                            prev_x = last_peace_x.get(0)
                            dx = (pos_pct - prev_x) if prev_x is not None else 0
                            last_peace_x[0] = pos_pct
                            velocity = min(50, int(abs(dx) * 3))  # faster movement = larger ripple reach
                            if velocity >= RIPPLE_MIN_VELOCITY:
                                dir_val = 1 if dx > 0 else -1
                                if _send_led_ripple(args.led_url, ripple_hue, position_pct=pos_pct, direction=dir_val, velocity=velocity):
                                    last_ripple_sent = now
                                    ripple_hue = (ripple_hue + 25) % 360
                                    last_led_state = last_whole_mode = last_split = None
                        if g1_stable == "PEACE" and second_hand_landmarks and len(second_hand_landmarks) > INDEX_TIP and now - last_ripple_sent >= RIPPLE_THROTTLE_SEC:
                            lm = second_hand_landmarks[INDEX_TIP]
                            pos_pct = max(0, min(100, int(lm.x * 100)))
                            prev_x = last_peace_x.get(1)
                            dx = (pos_pct - prev_x) if prev_x is not None else 0
                            last_peace_x[1] = pos_pct
                            velocity = min(50, int(abs(dx) * 3))
                            if velocity >= RIPPLE_MIN_VELOCITY:
                                dir_val = 1 if dx > 0 else -1
                                if _send_led_ripple(args.led_url, (ripple_hue + 180) % 360, position_pct=pos_pct, direction=dir_val, velocity=velocity):
                                    last_ripple_sent = now
                                    last_led_state = last_whole_mode = last_split = None
            else:
                led_gesture_hist = {}
                last_peace_x = {}

            n_hands = len(hand_data)
            for k in list(debounce_hist):
                if k >= n_hands:
                    del debounce_hist[k]
                    stable.pop(k, None)

            for i, (gesture, action, fingers, label, score) in enumerate(hand_data):
                _draw_hud(out, gesture, action, fingers, label, score, y_offset=i * 120)

            if not hand_data:
                cv2.putText(
                    out, "No hands detected", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_WHITE, 2,
                )

            if args.scale != 1.0:
                h, w = out.shape[:2]
                out = cv2.resize(
                    out, (int(w * args.scale), int(h * args.scale)),
                    interpolation=cv2.INTER_LINEAR,
                )

            try:
                frame_queue.put_nowait(out)
            except queue.Full:
                pass

    except Exception as e:
        print(f"[worker] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if hasattr(landmarker, "close"):
            landmarker.close()
        frame_queue.put(None)


def _normalize_led_url(url):
    """Strip whitespace and add http:// if no scheme."""
    if not url or not url.strip():
        return ""
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _get_frame_source(args):
    """Return a frame iterator: webcam or stream URL."""
    if args.webcam:
        return read_frames_webcam(args.camera_index)
    return read_frames(args.url)


def main():
    args = parse_args()
    args.led_url = _normalize_led_url(args.led_url)
    if args.webcam:
        print(f"Using webcam (device index {args.camera_index})")
    else:
        print(f"Connecting to {args.url}")
    print("Gestures: OPEN_HAND=ON  OK=OFF  HANG_LOOSE=rainbow  PEACE=ripple  ROCK=strobe  THREE=breathe  POINTER=fun  PINCH=explosion")
    if args.led_url:
        print(f"LED strip: {args.led_url}  One hand = whole strip; two hands = left/right half (split)")
        if not requests:
            print("WARNING: install 'requests' to control LED strip over WiFi.")

    if args.no_window:
        model_path = _get_model_path()
        options = HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=VisionRunningMode.VIDEO,
            num_hands=args.max_hands,
            min_hand_detection_confidence=args.min_confidence,
        )
        landmarker = HandLandmarker.create_from_options(options)
        led_gesture_hist = {}
        last_led_state = None
        last_whole_mode = None
        last_split = None
        last_fun_sent = 0.0
        last_ripple_sent = 0.0
        ripple_hue = 0
        last_peace_x = {}
        last_brightness_sent = 0.0
        last_explosion_sent = 0.0
        try:
            ts = 0
            for frame in _get_frame_source(args):
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect_for_video(mp_image, ts)
                ts += 33
                if result.hand_landmarks:
                    hand_gestures = []
                    hand_landmarks_list = []
                    for i, (landmarks_list, handedness_list) in enumerate(zip(result.hand_landmarks, result.handedness or [])):
                        label = handedness_list[0].display_name if handedness_list else "Unknown"
                        hand_lm = _landmarks_wrapper(landmarks_list)
                        fingers = get_finger_states(hand_lm, label)
                        gesture = classify_gesture(fingers, hand_lm)
                        action = GESTURE_ACTIONS.get(gesture, "-")
                        print(f"{label}: {gesture} -> {action}")
                        hand_gestures.append(gesture)
                        hand_landmarks_list.append(landmarks_list)
                    if args.led_url and hand_gestures:
                        for i in range(len(hand_gestures)):
                            hist = led_gesture_hist.get(i, [])
                            hist.append(hand_gestures[i])
                            if len(hist) > LED_DEBOUNCE_FRAMES:
                                hist = hist[-LED_DEBOUNCE_FRAMES:]
                            led_gesture_hist[i] = hist
                        for k in list(led_gesture_hist):
                            if k >= len(hand_gestures):
                                del led_gesture_hist[k]
                        g0_stable = (led_gesture_hist[0][0] if len(led_gesture_hist.get(0, [])) == LED_DEBOUNCE_FRAMES
                                    and len(set(led_gesture_hist[0])) == 1 else None)
                        g1_stable = (led_gesture_hist[1][0] if len(led_gesture_hist.get(1, [])) == LED_DEBOUNCE_FRAMES
                                    and len(set(led_gesture_hist[1])) == 1 else None)
                        lm0 = hand_landmarks_list[0] if len(hand_landmarks_list) > 0 else None
                        lm1 = hand_landmarks_list[1] if len(hand_landmarks_list) > 1 else None
                        m0 = _gesture_to_mode(g0_stable) if g0_stable else None
                        m1 = _gesture_to_mode(g1_stable) if g1_stable else None
                        if g0_stable and g1_stable and m0 is not None and m1 is not None:
                            if last_split != (m0, m1):
                                if _send_led_split(args.led_url, m0, m1):
                                    last_split = (m0, m1)
                                    last_whole_mode = last_led_state = None
                        elif m0 is not None:
                            if last_whole_mode != m0:
                                if _send_led_mode(args.led_url, m0):
                                    last_whole_mode = m0
                                    last_split = last_led_state = None
                        else:
                            now = time.time()
                            if g0_stable == "PEACE" and lm0 and len(lm0) > INDEX_TIP and now - last_ripple_sent >= RIPPLE_THROTTLE_SEC:
                                pos_pct = max(0, min(100, int(lm0[INDEX_TIP].x * 100)))
                                prev_x = last_peace_x.get(0)
                                dx = (pos_pct - prev_x) if prev_x is not None else 0
                                last_peace_x[0] = pos_pct
                                velocity = min(50, int(abs(dx) * 3))
                                if velocity >= RIPPLE_MIN_VELOCITY:
                                    dir_val = 1 if dx > 0 else -1
                                    if _send_led_ripple(args.led_url, ripple_hue, position_pct=pos_pct, direction=dir_val, velocity=velocity):
                                        last_ripple_sent = now
                                        ripple_hue = (ripple_hue + 25) % 360
                                        last_whole_mode = last_split = last_led_state = None
                            if g0_stable == "THUMB_UP" and now - last_brightness_sent >= BRIGHTNESS_THROTTLE_SEC:
                                if _send_led_brightness_up(args.led_url):
                                    last_brightness_sent = now
                            elif g0_stable == "THUMB_DOWN" and now - last_brightness_sent >= BRIGHTNESS_THROTTLE_SEC:
                                if _send_led_brightness_down(args.led_url):
                                    last_brightness_sent = now
                            if now - last_explosion_sent >= EXPLOSION_THROTTLE_SEC:
                                pinch0 = g0_stable == "PINCH" and lm0 and len(lm0) > INDEX_TIP
                                pinch1 = g1_stable == "PINCH" and lm1 and len(lm1) > INDEX_TIP
                                if pinch0 and pinch1:
                                    p1 = max(0, min(100, int(lm0[INDEX_TIP].x * 100)))
                                    h1 = max(0, min(360, int(lm0[INDEX_TIP].y * 360) % 360))
                                    p2 = max(0, min(100, int(lm1[INDEX_TIP].x * 100)))
                                    h2 = max(0, min(360, int(lm1[INDEX_TIP].y * 360) % 360))
                                    if _send_led_explosion_two(args.led_url, p1, h1, p2, h2):
                                        last_explosion_sent = now
                                        last_led_state = last_whole_mode = last_split = None
                                elif pinch0:
                                    p0 = max(0, min(100, int(lm0[INDEX_TIP].x * 100)))
                                    h0 = max(0, min(360, int(lm0[INDEX_TIP].y * 360) % 360))
                                    if _send_led_explosion(args.led_url, p0, h0):
                                        last_explosion_sent = now
                                        last_led_state = last_whole_mode = last_split = None
                                elif pinch1:
                                    p1 = max(0, min(100, int(lm1[INDEX_TIP].x * 100)))
                                    h1 = max(0, min(360, int(lm1[INDEX_TIP].y * 360) % 360))
                                    if _send_led_explosion_hand1(args.led_url, p1, h1):
                                        last_explosion_sent = now
                                        last_led_state = last_whole_mode = last_split = None
                            if (g0_stable == "POINTER" and g1_stable == "POINTER" and lm0 and lm1
                                    and len(lm0) > INDEX_TIP and len(lm1) > INDEX_TIP):
                                if now - last_fun_sent >= FUN_THROTTLE_SEC:
                                    p1 = max(0, min(100, int(lm0[INDEX_TIP].x * 100)))
                                    h1 = max(0, min(360, int(lm0[INDEX_TIP].y * 360) % 360))
                                    p2 = max(0, min(100, int(lm1[INDEX_TIP].x * 100)))
                                    h2 = max(0, min(360, int(lm1[INDEX_TIP].y * 360) % 360))
                                    if _send_led_fun_two(args.led_url, p1, h1, p2, h2):
                                        last_fun_sent = now
                                        last_led_state = last_whole_mode = last_split = None
                            elif g0_stable == "POINTER" and lm0 and len(lm0) > INDEX_TIP:
                                if now - last_fun_sent >= FUN_THROTTLE_SEC:
                                    lm = lm0[INDEX_TIP]
                                    p1 = max(0, min(100, int(lm.x * 100)))
                                    h1 = max(0, min(360, int(lm.y * 360) % 360))
                                    if _send_led_fun(args.led_url, p1, h1):
                                        last_fun_sent = now
                                        last_led_state = last_whole_mode = last_split = None
                else:
                    led_gesture_hist = {}
        finally:
            if hasattr(landmarker, "close"):
                landmarker.close()
        return

    mirror_ref = [args.mirror]  # worker reads this so mirror applies before drawing (text stays readable)
    print("Press 'q' to quit, 'm' to toggle mirror.")
    print("Opening Hand Gestures window...")
    sys.stdout.flush()

    cv2.namedWindow("Hand Gestures", cv2.WINDOW_NORMAL)
    frame_queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()
    worker = threading.Thread(target=_worker, args=(args, frame_queue, stop_event, mirror_ref), daemon=True)
    worker.start()

    last_frame = _placeholder_frame(text="Connecting...")
    stream_ended = False
    cv2.imshow("Hand Gestures", last_frame)
    cv2.waitKey(100)

    try:
        while True:
            if not stream_ended:
                try:
                    item = frame_queue.get(timeout=0.1)
                    if item is None:
                        stream_ended = True
                        if last_frame is None:
                            last_frame = _placeholder_frame(text="Stream ended.")
                    else:
                        last_frame = item
                except queue.Empty:
                    pass

            if last_frame is not None:
                display = last_frame.copy()
                if stream_ended:
                    h, w = display.shape[:2]
                    cv2.putText(
                        display, "Stream ended. Press Q to quit", (20, h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2,
                    )
                cv2.imshow("Hand Gestures", display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("Quit requested by user.")
                break
            elif key == ord("m"):
                mirror_ref[0] = not mirror_ref[0]
                print(f"Mirror: {'ON' if mirror_ref[0] else 'OFF'}")
    finally:
        stop_event.set()
        worker.join(timeout=1.0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Fatal error:", e, file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
