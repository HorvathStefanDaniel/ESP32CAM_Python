"""
Simple UI to paste Camera and LED strip IPs, then start the hand gesture script.
Run: python gesture_launcher.py   or: .\run.ps1 gesture_launcher
"""

import os
import subprocess
import sys

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
except ImportError:
    print("tkinter not available. Use: python hand_gesture.py --url http://CAM_IP/stream --led-url http://LED_IP")
    sys.exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, ".gesture_launcher_config.txt")


def ensure_http(s):
    s = (s or "").strip().rstrip("/")
    if not s:
        return ""
    if not s.startswith(("http://", "https://")):
        return "http://" + s
    return s


def load_config():
    out = {"camera": "", "led": "", "mirror": False, "webcam": False}
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("camera="):
                        out["camera"] = line.split("=", 1)[1].strip()
                    elif line.startswith("led="):
                        out["led"] = line.split("=", 1)[1].strip()
                    elif line.startswith("mirror="):
                        out["mirror"] = line.split("=", 1)[1].strip().lower() in ("1", "true", "yes")
                    elif line.startswith("webcam="):
                        out["webcam"] = line.split("=", 1)[1].strip().lower() in ("1", "true", "yes")
        except Exception:
            pass
    return out


def save_config(camera, led, mirror, webcam):
    try:
        with open(CONFIG_FILE, "w") as f:
            f.write(f"camera={camera}\n")
            f.write(f"led={led}\n")
            f.write(f"mirror={str(mirror).lower()}\n")
            f.write(f"webcam={str(webcam).lower()}\n")
    except Exception:
        pass


def start_gesture():
    camera = ensure_http(camera_var.get())
    led = ensure_http(led_var.get())
    mirror = mirror_var.get()
    webcam = webcam_var.get()

    if not led:
        messagebox.showwarning("Missing IP", "Enter the LED strip IP (e.g. 10.209.65.13)")
        return
    if not webcam and not camera:
        messagebox.showwarning("Missing IP", "Enter the Camera IP or check 'Use webcam'.")
        return

    args = [
        sys.executable,
        os.path.join(SCRIPT_DIR, "hand_gesture.py"),
        "--led-url", led,
    ]
    if webcam:
        args.append("--webcam")
    else:
        stream_url = camera.rstrip("/") + "/stream" if not camera.endswith("/stream") else camera
        args.extend(["--url", stream_url])
    if mirror:
        args.append("--mirror")

    save_config(camera_var.get().strip(), led_var.get().strip(), mirror, webcam)

    try:
        subprocess.Popen(
            args,
            cwd=SCRIPT_DIR,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )
        status_var.set("Started. Hand gesture window will open.")
    except Exception as e:
        messagebox.showerror("Error", str(e))
        status_var.set("Error starting script.")


root = tk.Tk()
root.title("Hand Gesture Launcher")
root.resizable(False, False)
root.geometry("420x240")

cfg = load_config()

main = ttk.Frame(root, padding=12)
main.pack(fill=tk.BOTH, expand=True)

webcam_var = tk.BooleanVar(value=cfg["webcam"])
ttk.Checkbutton(main, text="Use webcam (instead of camera server)", variable=webcam_var).grid(row=0, column=0, sticky=tk.W, pady=(0, 4))

ttk.Label(main, text="Camera IP or URL:").grid(row=1, column=0, sticky=tk.W, pady=(0, 4))
camera_var = tk.StringVar(value=cfg["camera"])
camera_entry = ttk.Entry(main, textvariable=camera_var, width=40)
camera_entry.grid(row=2, column=0, sticky=tk.EW, pady=(0, 10))

ttk.Label(main, text="LED strip IP or URL:").grid(row=3, column=0, sticky=tk.W, pady=(0, 4))
led_var = tk.StringVar(value=cfg["led"])
led_entry = ttk.Entry(main, textvariable=led_var, width=40)
led_entry.grid(row=4, column=0, sticky=tk.EW, pady=(0, 10))

mirror_var = tk.BooleanVar(value=cfg["mirror"])
ttk.Checkbutton(main, text="Mirror video", variable=mirror_var).grid(row=5, column=0, sticky=tk.W, pady=(0, 10))

btn = ttk.Button(main, text="Start hand gesture", command=start_gesture)
btn.grid(row=6, column=0, pady=(4, 8))

status_var = tk.StringVar(value="Paste IPs above (or use webcam) and click Start.")
ttk.Label(main, textvariable=status_var, foreground="gray").grid(row=7, column=0, sticky=tk.W)

root.columnconfigure(0, weight=1)
root.mainloop()
