"""
Face detection (and optional recognition) from ESP32-CAM MJPEG stream.
Uses OpenCV Haar cascade for detection. With --recognize, uses face_recognition
to label known faces (place images in known_faces/ named e.g. name.jpg).
UI runs on main thread; stream/processing runs in worker thread so the window stays responsive.
"""

import sys
import os
# Ensure script directory is on path so "import stream_reader" works when run from anywhere
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import argparse
import queue
import threading
import cv2
import numpy as np

from stream_reader import read_frames, add_stream_url_arg

# OpenCV Haar cascade (shipped with opencv-python)
CASCADE_PATH = os.path.join(
    os.path.dirname(cv2.__file__), "data", "haarcascade_frontalface_default.xml"
)


def parse_args():
    p = argparse.ArgumentParser(description="Face detection from ESP32-CAM stream.")
    add_stream_url_arg(p)
    p.add_argument(
        "--recognize",
        action="store_true",
        help="Enable face recognition (requires face_recognition; use known_faces/ folder)",
    )
    p.add_argument(
        "--known-faces",
        default="known_faces",
        help="Folder with reference images for recognition (default: known_faces)",
    )
    p.add_argument("--no-window", action="store_true", help="Headless: only print, no imshow")
    p.add_argument("--scale", type=float, default=2.0, help="Display scale (e.g. 2.0 = double size). Default: 2.0")
    p.add_argument("--no-face-windows", action="store_true", help="Disable separate window per detected face")
    return p.parse_args()


def load_face_detector():
    if not os.path.isfile(CASCADE_PATH):
        raise FileNotFoundError(f"Haar cascade not found: {CASCADE_PATH}")
    return cv2.CascadeClassifier(CASCADE_PATH)


def load_known_encodings(known_faces_dir):
    """Load encodings from known_faces/ (filename without extension = label). Returns (names, encodings)."""
    try:
        import face_recognition
    except ImportError:
        raise ImportError("Face recognition requires: pip install face_recognition")
    names = []
    encodings = []
    if not os.path.isdir(known_faces_dir):
        return names, encodings
    for f in os.listdir(known_faces_dir):
        path = os.path.join(known_faces_dir, f)
        if not os.path.isfile(path) or f.startswith("."):
            continue
        name = os.path.splitext(f)[0]
        img = face_recognition.load_image_file(path)
        face_locs = face_recognition.face_locations(img)
        if not face_locs:
            continue
        encs = face_recognition.face_encodings(img, face_locs)
        if encs:
            names.append(name)
            encodings.append(encs[0])
    return names, encodings


def detect_faces(gray, detector):
    return detector.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )


def recognize_faces(frame_bgr, boxes_bgr, known_names, known_encodings):
    """Label each face in boxes_bgr using known_names/known_encodings. Returns list of names (or 'Unknown')."""
    try:
        import face_recognition
    except ImportError:
        return ["Unknown"] * len(boxes_bgr)
    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    # face_recognition uses (top, right, bottom, left)
    face_locations = []
    for (x, y, w, h) in boxes_bgr:
        face_locations.append((y, x + w, y + h, x))
    encodings = face_recognition.face_encodings(frame_rgb, face_locations)
    names = []
    for enc in encodings:
        if not known_encodings:
            names.append("Unknown")
            continue
        matches = face_recognition.compare_faces(known_encodings, enc, tolerance=0.6)
        name = "Unknown"
        if True in matches:
            idx = matches.index(True)
            name = known_names[idx]
        names.append(name)
    return names


def _placeholder_frame(width=640, height=480, text="Connecting..."):
    """Black image with status text for when no stream frame is available."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)
    cv2.putText(
        img, text, (width // 4, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (100, 200, 100), 2,
    )
    return img


# Minimum size for face crop windows so small detections are visible
FACE_CROP_MIN_SIZE = 120
# Layout for per-face windows: horizontal row with this gap (pixels)
FACE_WINDOW_MARGIN = 8
FACE_WINDOW_START_X = 10
FACE_WINDOW_START_Y = 30


def _worker_face_detect(args, detector, known_names, known_encodings, frame_queue, stop_event):
    """Run in thread: read stream, detect/recognize faces, push (display_frame, face_crops) to queue."""
    try:
        for frame in read_frames(args.url):
            if stop_event.is_set():
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            raw_boxes = detect_faces(gray, detector)
            boxes = [] if raw_boxes is None or len(raw_boxes) == 0 else raw_boxes
            if args.recognize and known_encodings and len(boxes) > 0:
                names = recognize_faces(frame, boxes, known_names, known_encodings)
            else:
                names = [f"Face {i+1}" for i in range(len(boxes))]
            out = frame.copy()
            face_crops = []  # list of (crop_image, label) for separate windows
            for (x, y, w, h), name in zip(boxes, names):
                cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(
                    out, name, (x, y - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1,
                )
                # Crop face (with bounds check) for the per-face window
                h_f, w_f = frame.shape[:2]
                x1, y1 = max(0, x), max(0, y)
                x2, y2 = min(w_f, x + w), min(h_f, y + h)
                if x2 > x1 and y2 > y1:
                    crop = frame[y1:y2, x1:x2].copy()
                    if crop.size > 0:
                        # Scale up small crops so they're visible
                        cw, ch = crop.shape[1], crop.shape[0]
                        if cw < FACE_CROP_MIN_SIZE or ch < FACE_CROP_MIN_SIZE:
                            scale = max(FACE_CROP_MIN_SIZE / cw, FACE_CROP_MIN_SIZE / ch)
                            crop = cv2.resize(crop, (int(cw * scale), int(ch * scale)), interpolation=cv2.INTER_LINEAR)
                        face_crops.append((crop, name))
            n = len(boxes)
            cv2.putText(
                out, f"Faces: {n}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2,
            )
            if args.scale != 1.0:
                h, w = out.shape[:2]
                out = cv2.resize(out, (int(w * args.scale), int(h * args.scale)), interpolation=cv2.INTER_LINEAR)
            try:
                frame_queue.put_nowait((out, face_crops))
            except queue.Full:
                pass
    except Exception as e:
        print(f"[worker] Error: {e}")
    finally:
        frame_queue.put(None)


def main():
    args = parse_args()
    detector = load_face_detector()
    known_names, known_encodings = [], []
    if args.recognize:
        known_names, known_encodings = load_known_encodings(args.known_faces)
        print(f"Loaded {len(known_names)} known face(s) from {args.known_faces}")
    print(f"Connecting to {args.url}")
    if not args.no_window:
        print("Press 'q' in the main window to quit. Each detected face opens in its own window.")
        cv2.namedWindow("Face detection", cv2.WINDOW_NORMAL)
        show_face_windows = not args.no_face_windows
    else:
        show_face_windows = False
        for frame in read_frames(args.url):
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            raw_boxes = detect_faces(gray, detector)
            boxes = [] if raw_boxes is None or len(raw_boxes) == 0 else raw_boxes
            if args.recognize and known_encodings and len(boxes) > 0:
                names = recognize_faces(frame, boxes, known_names, known_encodings)
            else:
                names = [f"Face {i+1}" for i in range(len(boxes))]
            print(len(boxes))
        return

    frame_queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_worker_face_detect,
        args=(args, detector, known_names, known_encodings, frame_queue, stop_event),
        daemon=True,
    )
    worker.start()

    last_frame = _placeholder_frame(text="Connecting...")
    last_face_window_names = []  # track names so we can destroy when face count drops
    try:
        while True:
            try:
                item = frame_queue.get(timeout=0.1)
                if item is None:
                    break
                out, face_crops = item
                last_frame = out
                if show_face_windows:
                    n = len(face_crops)
                    new_names = [f"Face {i + 1} - {name}" for i, (_, name) in enumerate(face_crops)]
                    x_offset = FACE_WINDOW_START_X
                    for i in range(n):
                        crop, name = face_crops[i]
                        win_name = new_names[i]
                        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
                        cv2.moveWindow(win_name, x_offset, FACE_WINDOW_START_Y)
                        cv2.imshow(win_name, crop)
                        x_offset += crop.shape[1] + FACE_WINDOW_MARGIN
                    for i in range(n, len(last_face_window_names)):
                        try:
                            cv2.destroyWindow(last_face_window_names[i])
                        except cv2.error:
                            pass
                    last_face_window_names = new_names
            except queue.Empty:
                pass
            cv2.imshow("Face detection", last_frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("Quit requested by user.")
                break
    finally:
        stop_event.set()
        worker.join(timeout=1.0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
