"""
Object counting from ESP32-CAM MJPEG stream.
Counts distinct blobs (objects) on a contrasting background using contours.
Tunables: min/max contour area, blur, threshold.
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

from stream_reader import read_frames, add_stream_url_arg, DEFAULT_STREAM_URL


# Tunables (override via CLI or edit here)
MIN_AREA = 500
MAX_AREA = 100_000
BLUR_KSIZE = (5, 5)
USE_OTSU = True
FIXED_THRESH = 127
INVERT_THRESH = False  # Set True if objects are lighter than background
BACKGROUND_TOLERANCE = 50  # Pixels within this range of background gray = background (higher = ignore slight shading)


def parse_args():
    p = argparse.ArgumentParser(description="Count objects on contrasting background from ESP32-CAM stream.")
    add_stream_url_arg(p)
    p.add_argument("--min-area", type=int, default=MIN_AREA, help="Min contour area (pixels)")
    p.add_argument("--max-area", type=int, default=MAX_AREA, help="Max contour area (pixels)")
    p.add_argument("--blur", type=int, default=5, help="Gaussian blur kernel size (odd)")
    p.add_argument("--threshold", type=int, default=FIXED_THRESH, help="Fixed threshold (if not using Otsu)")
    p.add_argument("--no-otsu", action="store_true", help="Use fixed threshold instead of Otsu")
    p.add_argument("--invert", action="store_true", help="Invert threshold (light objects on dark background)")
    p.add_argument("--no-window", action="store_true", help="Headless: only print count, no imshow")
    p.add_argument("--scale", type=float, default=2.0, help="Display scale (e.g. 2.0 = double size). Default: 2.0")
    p.add_argument("--tolerance", type=int, default=BACKGROUND_TOLERANCE, help="Background tolerance when using click-to-set (default 50)")
    return p.parse_args()


def count_objects(frame, min_area, max_area, blur_ksize, use_otsu, fixed_thresh, invert, background_gray=None, tolerance=BACKGROUND_TOLERANCE):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, blur_ksize, 0)
    if background_gray is not None:
        # Threshold by distance from clicked background color
        diff = cv2.absdiff(blurred, np.full_like(blurred, background_gray, dtype=np.uint8))
        _, thresh = cv2.threshold(diff, tolerance, 255, cv2.THRESH_BINARY)
    elif use_otsu:
        _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    else:
        _, thresh = cv2.threshold(blurred, fixed_thresh, 255, cv2.THRESH_BINARY)
    if invert:
        thresh = cv2.bitwise_not(thresh)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    count = 0
    good_contours = []
    for c in contours:
        area = cv2.contourArea(c)
        if min_area <= area <= max_area:
            count += 1
            good_contours.append(c)
    return count, good_contours, thresh


def _placeholder_frame(width=640, height=480, text="Connecting..."):
    """Black image with status text for when no stream frame is available."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)
    cv2.putText(
        img, text, (width // 4, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (100, 200, 100), 2,
    )
    return img


def _worker_object_count(args, blur_ksize, use_otsu, frame_queue, stop_event, ui_state):
    """Run in thread: read stream, count objects, push (display_frame, count, raw_frame) to queue."""
    frame_count = 0
    try:
        for frame in read_frames(args.url):
            if stop_event.is_set():
                break
            frame_count += 1
            if frame_count == 1:
                print(f"First frame received! Shape: {frame.shape}")
            bg = ui_state.get("background_gray")
            tol = ui_state.get("tolerance", BACKGROUND_TOLERANCE)
            n, contours, _ = count_objects(
                frame,
                min_area=args.min_area,
                max_area=args.max_area,
                blur_ksize=blur_ksize,
                use_otsu=use_otsu,
                fixed_thresh=args.threshold,
                invert=args.invert,
                background_gray=bg,
                tolerance=tol,
            )
            out = frame.copy()
            cv2.drawContours(out, contours, -1, (0, 255, 0), 2)
            cv2.putText(
                out, f"Count: {n}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2,
            )
            if bg is not None:
                cv2.putText(out, "BG set (click to change)", (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            if args.scale != 1.0:
                h, w = out.shape[:2]
                out = cv2.resize(out, (int(w * args.scale), int(h * args.scale)), interpolation=cv2.INTER_LINEAR)
            try:
                frame_queue.put_nowait((out, n, frame))  # frame = raw for click sampling
            except queue.Full:
                pass  # Drop frame if UI is slow
    except Exception as e:
        print(f"[worker] Error: {e}")
    finally:
        frame_queue.put(None)  # Sentinel: worker finished


def main():
    args = parse_args()
    blur_ksize = (args.blur | 1, args.blur | 1)
    use_otsu = not args.no_otsu
    print(f"Connecting to {args.url} (min_area={args.min_area}, max_area={args.max_area})")
    if not args.no_window:
        print("Press 'q' to quit. Click on the image to set that color as background for counting.")
        cv2.namedWindow("Object count", cv2.WINDOW_NORMAL)
    else:
        print("Running in headless mode (no window).")
        frame_count = 0
        for frame in read_frames(args.url):
            frame_count += 1
            n, contours, _ = count_objects(
                frame,
                min_area=args.min_area,
                max_area=args.max_area,
                blur_ksize=blur_ksize,
                use_otsu=use_otsu,
                fixed_thresh=args.threshold,
                invert=args.invert,
            )
            if frame_count % 30 == 0:
                print(f"Frame {frame_count}: Count = {n}")
            else:
                print(n)
        return

    # Shared state: worker reads background_gray/tolerance; main thread sets them on click and stores last_raw_frame
    ui_state = {"background_gray": None, "tolerance": args.tolerance, "last_raw_frame": None, "scale": args.scale}

    def _on_mouse(event, x, y, _flags, _userdata):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        raw = ui_state.get("last_raw_frame")
        if raw is None:
            return
        scale = ui_state.get("scale", 1.0)
        rx = int(x / scale)
        ry = int(y / scale)
        h, w = raw.shape[:2]
        if 0 <= rx < w and 0 <= ry < h:
            # Sample a small region (5x5) and use median so slight illumination variation isn't "too accurate"
            r = 2  # 5x5 patch
            y1, y2 = max(0, ry - r), min(h, ry + r + 1)
            x1, x2 = max(0, rx - r), min(w, rx + r + 1)
            patch = raw[y1:y2, x1:x2]
            gray_patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
            gray_val = int(np.median(gray_patch))
            ui_state["background_gray"] = gray_val
            print(f"Background set to grayscale {gray_val} (median of {patch.shape[0]}x{patch.shape[1]} region at {rx},{ry})")

    cv2.setMouseCallback("Object count", _on_mouse, None)

    frame_queue = queue.Queue(maxsize=2)
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_worker_object_count,
        args=(args, blur_ksize, use_otsu, frame_queue, stop_event, ui_state),
        daemon=True,
    )
    worker.start()

    last_frame = _placeholder_frame(text="Connecting... (click image to set background)")
    last_count = 0
    try:
        while True:
            try:
                item = frame_queue.get(timeout=0.1)
                if item is None:
                    break  # Worker finished
                last_frame, last_count, raw_frame = item
                ui_state["last_raw_frame"] = raw_frame
            except queue.Empty:
                pass
            cv2.imshow("Object count", last_frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                print("Quit requested by user.")
                break
    finally:
        stop_event.set()
        worker.join(timeout=1.0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
