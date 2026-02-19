"""
Shared MJPEG stream reader for ESP32-CAM.
Uses HTTP streaming + manual MJPEG parse (reliable with ESP32-CAM).
OpenCV VideoCapture often fails or times out; this matches how browsers receive the stream.
"""

import os
import re
import time
import numpy as np
import cv2

try:
    import requests
except ImportError:
    requests = None


DEFAULT_STREAM_URL = os.environ.get("STREAM_URL", "http://192.168.1.100/cam-lo.jpg")
RECONNECT_DELAY_SEC = 2.0
CONNECT_TIMEOUT_SEC = 10
STREAM_READ_TIMEOUT_SEC = 30


def _normalize_stream_url(url):
    if not url:
        return DEFAULT_STREAM_URL
    if (("snap" in url or "cam-" in url) and ".jpg" in url) or url.endswith(".jpg"):
        return url.rstrip("/")
    if url.endswith("/stream") or url.endswith("/stream/"):
        return url.rstrip("/") if url.endswith("/") else url
    return url.rstrip("/") + "/stream" if not url.endswith("/") else url + "stream"


def _read_frames_poll(url, debug=False):
    """Yield BGR frames by polling a single-JPEG endpoint (e.g. /snap.jpg). One GET = one frame. Often better FPS/quality."""
    while True:
        try:
            resp = requests.get(
                url,
                timeout=CONNECT_TIMEOUT_SEC,
                headers={"User-Agent": "ESP32-CAM-Python/1.0", "Accept": "image/*", "Accept-Encoding": "identity"},
            )
            resp.raise_for_status()
            frame = cv2.imdecode(np.frombuffer(resp.content, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                if debug:
                    print(f"[stream_reader] Poll frame: {frame.shape[1]}x{frame.shape[0]}")
                yield frame
        except requests.RequestException as e:
            if debug:
                print(f"[stream_reader] Poll error: {e}")
            raise


def _read_frames_http(url, debug=False):
    """Yield BGR frames from a streaming HTTP MJPEG response (multipart/x-mixed-replace)."""
    resp = requests.get(
        url,
        stream=True,
        timeout=(CONNECT_TIMEOUT_SEC, STREAM_READ_TIMEOUT_SEC),
        headers={
            "User-Agent": "ESP32-CAM-Python/1.0",
            "Accept": "image/*",
            "Accept-Encoding": "identity",  # Don't use gzip; we need raw multipart bytes
        },
    )
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    m = re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))', content_type)
    boundary = ((m.group(1) or m.group(2)) if m else None) or "frame"
    boundary = boundary.strip().encode("ascii")
    frame_start = b"--" + boundary
    buf = b""
    read_size = 4096  # Larger chunks reduce overhead when reading MJPEG stream
    first_chunk = True
    first_frame_logged = False
    pending_content_length = None  # when set, we're reading frame body across chunks
    try:
        for chunk in resp.iter_content(chunk_size=read_size):
            if not chunk:
                continue
            if first_chunk and debug:
                print(f"[stream_reader] First chunk: {len(chunk)} bytes, starts with: {chunk[:80]!r}")
                first_chunk = False
            buf += chunk
            while True:
                if pending_content_length is not None:
                    # We're in the middle of reading a frame body (split across chunks)
                    if len(buf) < pending_content_length:
                        break
                    jpeg = buf[:pending_content_length]
                    buf = buf[pending_content_length:]
                    if buf.startswith(b"\r\n"):
                        buf = buf[2:]
                    elif buf.startswith(b"\n"):
                        buf = buf[1:]
                    pending_content_length = None
                    frame = cv2.imdecode(
                        np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if frame is not None:
                        if debug and not first_frame_logged:
                            print(f"[stream_reader] First frame: {frame.shape[1]}x{frame.shape[0]}")
                            first_frame_logged = True
                        yield frame
                    continue
                # Look for next boundary
                idx = buf.find(frame_start)
                if idx < 0:
                    if len(buf) > len(frame_start) + 4:
                        buf = buf[-len(frame_start) - 4:]
                    break
                buf = buf[idx + len(frame_start):]
                if buf.startswith(b"\r\n"):
                    buf = buf[2:]
                elif buf.startswith(b"\n"):
                    buf = buf[1:]
                sep = b"\r\n\r\n" if b"\r\n\r\n" in buf else (b"\n\n" if b"\n\n" in buf else None)
                if sep is None:
                    break
                header_block, buf = buf.split(sep, 1)
                content_length = None
                for line in header_block.split(b"\r\n"):
                    line = line.strip(b"\r")
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                        break
                if content_length is None:
                    for line in header_block.split(b"\n"):
                        if line.lower().startswith(b"content-length:"):
                            content_length = int(line.split(b":", 1)[1].strip())
                            break
                if content_length is None or content_length <= 0:
                    continue
                pending_content_length = content_length
                # loop again to check len(buf) >= pending_content_length
    except requests.RequestException:
        raise


def read_frames(stream_url=None, reconnect=True):
    """
    Yield BGR frames (numpy arrays) from the MJPEG stream.
    Uses HTTP + manual MJPEG parse for compatibility with ESP32-CAM.
    """
    url = _normalize_stream_url(stream_url or DEFAULT_STREAM_URL)
    if requests is None:
        raise RuntimeError("stream_reader requires 'requests'. Install with: pip install requests")
    use_poll = (("snap" in url or "cam-" in url) and ".jpg" in url) or url.endswith(".jpg")
    if use_poll:
        print(f"[stream_reader] Using snapshot polling: {url}")
    attempt = 0
    while True:
        attempt += 1
        if not use_poll:
            print(f"[stream_reader] Connecting to: {url}")
        try:
            reader = _read_frames_poll(url, debug=(attempt <= 2)) if use_poll else _read_frames_http(url, debug=(attempt <= 2))
            for frame in reader:
                attempt = 0
                yield frame
        except Exception as e:
            if attempt <= 2:
                print(f"[stream_reader] Error: {type(e).__name__}: {e}")
                print(f"  - Check ESP32-CAM is on, close browser tab with stream open (only 1 client)")
            print(f"[stream_reader] Waiting for stream (attempt {attempt})...")
        except StopIteration:
            pass
        if not reconnect:
            break
        time.sleep(RECONNECT_DELAY_SEC)
        print("[stream_reader] Reconnecting ...")


def add_stream_url_arg(parser):
    """Add --url to an ArgumentParser. Call before parser.parse_args()."""
    parser.add_argument(
        "--url",
        default=DEFAULT_STREAM_URL,
        help=f"MJPEG stream URL (default: {DEFAULT_STREAM_URL} or STREAM_URL env). Auto-appends /stream if missing.",
    )
