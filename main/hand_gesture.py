"""
Hand gesture detection from ESP32-CAM stream for LED strip control.

Uses MediaPipe Hands for landmark detection and classifies gestures
based on finger extension states. Prototype displays debug overlay
with landmarks, finger states, and detected gesture + mapped LED action.

Gestures detected:
  FIST        → OFF          OPEN_HAND   → ON
  THUMB_UP    → BRIGHT+      POINTER     → SELECT
  PEACE       → NEXT MODE    THREE       → MODE 3
  FOUR        → MODE 4       ROCK        → EFFECT
  PINCH       → ADJUST       OK          → CONFIRM

Controls:
  q  – quit
  m  – toggle mirror

Source: use --url for ESP32-CAM stream (default) or --webcam for local webcam.

LED strip: --led-url set. Open palm = on, fist = off. POINTER (index) = fun mode: cluster follows finger X, color from finger Y.
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
    "FIST": "OFF",
    "OPEN_HAND": "ON",
    "THUMB_UP": "BRIGHT+",
    "POINTER": "SELECT",
    "PEACE": "NEXT MODE",
    "THREE": "MODE 3",
    "FOUR": "MODE 4",
    "ROCK": "EFFECT",
    "PINCH": "ADJUST",
    "OK": "CONFIRM",
    "L": "L",
    "HANG_LOOSE": "CHILL",
}

COLOR_CYAN = (255, 255, 0)
COLOR_YELLOW = (0, 255, 255)
COLOR_GREEN = (0, 255, 0)
COLOR_WHITE = (255, 255, 255)
COLOR_BG = (40, 40, 40)

DEBOUNCE_FRAMES = 3
LED_DEBOUNCE_FRAMES = 5  # require this many same gesture before changing strip (reduces flicker)
FUN_THROTTLE_SEC = 1.0 / 15.0  # max 15 Hz for /fun position updates

# Hand landmarker model (downloaded on first run)
HAND_LANDMARKER_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)


def _get_model_path():
    """Return path to hand_landmarker.task, downloading if needed."""
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


def _draw_hand_landmarks(frame, landmarks_list, height, width, color=(0, 255, 0), thickness=2):
    """Draw hand skeleton and points on frame. landmarks_list: list of 21 normalized (x,y,z) landmarks."""
    if not landmarks_list or len(landmarks_list) < 21:
        return
    pts = []
    for lm in landmarks_list:
        x = int(lm.x * width)
        y = int(lm.y * height)
        pts.append((x, y))
    for (i, j) in HAND_CONNECTIONS:
        if i < len(pts) and j < len(pts):
            cv2.line(frame, pts[i], pts[j], color, thickness)
    for (x, y) in pts:
        cv2.circle(frame, (x, y), thickness + 1, color, -1)


def _dist(a, b):
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _landmarks_wrapper(landmarks_list):
    """Wrap list of 21 landmarks so they have .landmark for legacy-style access."""
    class Wrapper:
        landmark = landmarks_list
    return Wrapper()


def get_finger_states(hand_landmarks, handedness_label):
    """Return [thumb, index, middle, ring, pinky] booleans (True = extended).
    Uses both axis checks and distance-from-wrist so gestures work at different angles.
    """
    lm = hand_landmarks.landmark if hasattr(hand_landmarks, "landmark") else hand_landmarks
    wrist = lm[WRIST]

    # Thumb: x-direction (left/right) plus distance so it works when palm faces camera
    thumb_along_x = lm[THUMB_TIP].x < lm[THUMB_IP].x if handedness_label == "Right" else lm[THUMB_TIP].x > lm[THUMB_IP].x
    thumb_dist = _dist(wrist, lm[THUMB_TIP]) > _dist(wrist, lm[THUMB_IP]) * 1.08
    thumb = thumb_along_x or thumb_dist

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
    """Map finger extension pattern to a named gesture."""
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
        return "THUMB_UP"
    # POINTER = index up, other fingers (except thumb) down; thumb can be up (avoids L when pointing)
    if index and not middle and not ring and not pinky:
        return "POINTER"
    if index and middle and not any([thumb, ring, pinky]):
        return "PEACE"
    if index and middle and ring and not any([thumb, pinky]):
        return "THREE"
    # FOUR folded into OPEN_HAND above (n_up >= 4)
    if index and pinky and not any([thumb, middle, ring]):
        return "ROCK"
    if thumb and index and not any([middle, ring, pinky]):
        return "L"
    if thumb and pinky and not any([index, middle, ring]):
        return "HANG_LOOSE"

    return "UNKNOWN"


def _draw_hud(frame, gesture, action, fingers, hand_label, confidence, y_offset=0):
    """Draw gesture info overlay with drop shadow for readability."""
    finger_labels = ["T", "I", "M", "R", "P"]
    finger_str = " ".join(
        f"{n}{'↑' if up else '↓'}" for n, up in zip(finger_labels, fingers)
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


def _send_led_fun(base_url, position_pct: int, hue: int, timeout=1.0):
    """Send GET /fun?p=position_pct&h=hue for moving cluster (fun mode)."""
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


def _worker(args, frame_queue, stop_event):
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
    last_print = 0
    frame_timestamp_ms = 0
    last_led_state = None  # "on", "off", or None
    led_gesture_hist = []  # recent gestures for first hand (longer debounce for strip)
    last_fun_sent = 0.0

    try:
        for frame in _get_frame_source(args):
            if stop_event.is_set():
                break

            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w = frame.shape[:2]
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
            result = landmarker.detect_for_video(mp_image, frame_timestamp_ms)
            frame_timestamp_ms += 33  # ~30 fps

            out = frame.copy()
            hand_data = []
            first_hand_landmarks = None

            if result.hand_landmarks:
                for i, (landmarks_list, handedness_list) in enumerate(zip(result.hand_landmarks, result.handedness)):
                    if i == 0:
                        first_hand_landmarks = landmarks_list
                    _draw_hand_landmarks(out, landmarks_list, h, w)

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

                # LED strip: open palm = on, fist = off; POINTER = fun mode (cluster position + hue)
                if args.led_url and hand_data:
                    g = hand_data[0][0]
                    led_gesture_hist.append(g)
                    if len(led_gesture_hist) > LED_DEBOUNCE_FRAMES:
                        led_gesture_hist.pop(0)
                    if len(led_gesture_hist) == LED_DEBOUNCE_FRAMES and len(set(led_gesture_hist)) == 1:
                        g_stable = led_gesture_hist[0]
                        want = None
                        if g_stable == "OPEN_HAND":
                            want = "on"
                        elif g_stable == "FIST":
                            want = "off"
                        if want is not None and last_led_state != want:
                            _send_led_command(args.led_url, want == "on")
                            last_led_state = want
                        elif g_stable == "POINTER" and first_hand_landmarks and len(first_hand_landmarks) > INDEX_TIP:
                            now = time.time()
                            if now - last_fun_sent >= FUN_THROTTLE_SEC:
                                lm = first_hand_landmarks[INDEX_TIP]
                                position_pct = max(0, min(100, int(lm.x * 100)))
                                hue = max(0, min(360, int(lm.y * 360) % 360))
                                if _send_led_fun(args.led_url, position_pct, hue):
                                    last_fun_sent = now
            else:
                led_gesture_hist = []

                now = time.time()
                if now - last_print > 0.5 and hand_data:
                    parts = [f"{lbl}: {g} -> {a}" for g, a, _, lbl, _ in hand_data]
                    print(f"[gesture] {' | '.join(parts)}")
                    last_print = now

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
    print("Gestures: FIST=OFF  OPEN_HAND=ON  THUMB_UP=BRIGHT+  POINTER=SELECT  PEACE=NEXT")
    if args.led_url:
        print(f"LED strip: {args.led_url}  Open palm = ON, Fist = OFF")
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
        debounce_hist = []
        last_led_state = None
        last_fun_sent = 0.0
        try:
            ts = 0
            for frame in _get_frame_source(args):
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect_for_video(mp_image, ts)
                ts += 33
                if result.hand_landmarks:
                    # Use first hand for display and LED
                    landmarks_list = result.hand_landmarks[0]
                    handedness_list = result.handedness[0] if result.handedness else []
                    label = handedness_list[0].display_name if handedness_list else "Unknown"
                    hand_lm = _landmarks_wrapper(landmarks_list)
                    fingers = get_finger_states(hand_lm, label)
                    gesture = classify_gesture(fingers, hand_lm)
                    action = GESTURE_ACTIONS.get(gesture, "-")
                    print(f"{label}: {gesture} -> {action}")
                    # LED: open palm = on, fist = off; POINTER = fun mode
                    debounce_hist.append(gesture)
                    if len(debounce_hist) > LED_DEBOUNCE_FRAMES:
                        debounce_hist = debounce_hist[-LED_DEBOUNCE_FRAMES:]
                    if len(debounce_hist) == LED_DEBOUNCE_FRAMES and len(set(debounce_hist)) == 1:
                        g = debounce_hist[-1]
                        if g == "OPEN_HAND" and last_led_state != "on" and args.led_url:
                            _send_led_command(args.led_url, True)
                            last_led_state = "on"
                        elif g == "FIST" and last_led_state != "off" and args.led_url:
                            _send_led_command(args.led_url, False)
                            last_led_state = "off"
                        elif g == "POINTER" and len(landmarks_list) > INDEX_TIP and args.led_url:
                            now = time.time()
                            if now - last_fun_sent >= FUN_THROTTLE_SEC:
                                lm = landmarks_list[INDEX_TIP]
                                position_pct = max(0, min(100, int(lm.x * 100)))
                                hue = max(0, min(360, int(lm.y * 360) % 360))
                                if _send_led_fun(args.led_url, position_pct, hue):
                                    last_fun_sent = now
                else:
                    debounce_hist = []
        finally:
            if hasattr(landmarker, "close"):
                landmarker.close()
        return

    mirror = args.mirror
    print("Press 'q' to quit, 'm' to toggle mirror.")
    print("Opening Hand Gestures window...")
    sys.stdout.flush()

    cv2.namedWindow("Hand Gestures", cv2.WINDOW_NORMAL)
    frame_queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()
    worker = threading.Thread(target=_worker, args=(args, frame_queue, stop_event), daemon=True)
    worker.start()

    last_frame = _placeholder_frame(text="Connecting...")
    stream_ended = False
    # Show window once before loop so it appears even if worker fails immediately
    cv2.imshow("Hand Gestures", cv2.flip(last_frame, 1) if mirror else last_frame)
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
                display = cv2.flip(display, 1) if mirror else display
                cv2.imshow("Hand Gestures", display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("Quit requested by user.")
                break
            elif key == ord("m"):
                mirror = not mirror
                print(f"Mirror: {'ON' if mirror else 'OFF'}")
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
