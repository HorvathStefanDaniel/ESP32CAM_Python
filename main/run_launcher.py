"""
Launcher that does the same job as run.ps1: ensure venv + deps, then run
object_count, face_detect, hand_gesture, or gesture_launcher.
When run with no arguments (e.g. double-click), shows a menu to pick the script,
then prompts for webcam (y/n), camera URL, and (for hand_gesture) LED URL.
Usage: python run_launcher.py <script> [script_args...]
   or: run.exe <script> [script_args...]
   or: run.exe   (then choose from menu and answer prompts)
"""

import os
import sys
import subprocess

VALID_SCRIPTS = ("object_count", "face_detect", "hand_gesture", "gesture_launcher")
DEFAULT_STREAM_URL = os.environ.get("STREAM_URL", "http://192.168.1.100/stream")


def get_project_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def prompt_interactive_options(script):
    """When running from the menu, ask for webcam, camera URL, and (hand_gesture only) LED URL. Returns list of args."""
    script_args = []
    if script == "gesture_launcher":
        return script_args  # has its own GUI for IPs and webcam

    # Use webcam?
    use_webcam = input(f"Use webcam instead of ESP32-CAM stream? (y/n) [n]: ").strip().lower() or "n"
    if use_webcam in ("y", "yes"):
        script_args.append("--webcam")
        # Optional: camera index
        idx = input("Webcam device index [0]: ").strip()
        if idx and idx.isdigit():
            script_args.extend(["--camera-index", idx])
    else:
        url = input(f"Camera/stream URL [{DEFAULT_STREAM_URL}]: ").strip() or DEFAULT_STREAM_URL
        if url:
            script_args.extend(["--url", url])

    if script == "hand_gesture":
        led = input("LED strip URL (optional, press Enter to skip): ").strip()
        if led:
            script_args.extend(["--led-url", led])

    if script == "face_detect":
        use_recognition = input("Use face recognition (identify known faces)? (y/n) [n]: ").strip().lower() or "n"
        if use_recognition in ("y", "yes"):
            script_args.append("--recognize")

    return script_args


def main():
    project_dir = get_project_dir()
    venv_dir = os.path.join(project_dir, ".venv")
    if sys.platform == "win32":
        python_exe = os.path.join(venv_dir, "Scripts", "python.exe")
        pip_exe = os.path.join(venv_dir, "Scripts", "pip.exe")
    else:
        python_exe = os.path.join(venv_dir, "bin", "python")
        pip_exe = os.path.join(venv_dir, "bin", "pip")
    requirements = os.path.join(project_dir, "requirements.txt")

    # Parse args: optional script name + script args
    args = sys.argv[1:]
    if args and args[0] in VALID_SCRIPTS:
        script = args[0]
        script_args = args[1:]
    else:
        # No args or invalid: show menu when interactive
        if not sys.stdin.isatty():
            print("Usage: run_launcher.py object_count|face_detect|hand_gesture|gesture_launcher [args...]", file=sys.stderr)
            sys.exit(1)
        print("Run which script?")
        for i, name in enumerate(VALID_SCRIPTS, 1):
            print(f"  {i}. {name}")
        try:
            choice = input("Enter number (1-4): ").strip()
            idx = int(choice)
            if 1 <= idx <= len(VALID_SCRIPTS):
                script = VALID_SCRIPTS[idx - 1]
                script_args = prompt_interactive_options(script)
            else:
                print("Invalid choice.", file=sys.stderr)
                sys.exit(1)
        except (ValueError, EOFError):
            print("Invalid input.", file=sys.stderr)
            sys.exit(1)

    # Create venv if missing
    if not os.path.isfile(python_exe):
        print("Creating virtual environment in .venv ...")
        created = False
        if script in ("hand_gesture", "gesture_launcher") and sys.platform == "win32":
            for ver in ("3.11", "3.10", "3.9"):
                try:
                    r = subprocess.run(
                        ["py", f"-{ver}", "-m", "venv", venv_dir],
                        cwd=project_dir,
                        capture_output=True,
                        timeout=60,
                    )
                    if r.returncode == 0 and os.path.isfile(python_exe):
                        created = True
                        break
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass
        if not created:
            r = subprocess.run([sys.executable, "-m", "venv", venv_dir], cwd=project_dir, timeout=60)
            if r.returncode != 0:
                sys.exit(r.returncode)
    if not os.path.isfile(python_exe):
        print("ERROR: Could not create .venv. Install Python 3.9+ and try again.", file=sys.stderr)
        sys.exit(1)

    # Python 3.9+ check for hand_gesture / gesture_launcher
    if script in ("hand_gesture", "gesture_launcher"):
        r = subprocess.run(
            [python_exe, "-c", "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"],
            capture_output=True,
            timeout=10,
        )
        if r.returncode != 0:
            ver = subprocess.run([python_exe, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"], capture_output=True, text=True, timeout=5)
            v = ver.stdout.strip() if ver.returncode == 0 else "?"
            print(f"ERROR: {script} requires Python 3.9+. Current venv is Python {v}.", file=sys.stderr)
            print("Remove .venv and install Python 3.9+ (e.g. from python.org), then run again.", file=sys.stderr)
            sys.exit(1)

    # Install dependencies
    print("Ensuring dependencies are installed ...")
    r = subprocess.run(
        [python_exe, "-m", "pip", "install", "-q", "-r", requirements],
        cwd=project_dir,
        timeout=120,
    )
    if r.returncode != 0:
        sys.exit(r.returncode)

    script_path = os.path.join(project_dir, f"{script}.py")
    if not os.path.isfile(script_path):
        print(f"ERROR: Script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env["PYTHONPATH"] = project_dir if not env.get("PYTHONPATH") else f"{project_dir}{os.pathsep}{env['PYTHONPATH']}"

    r = subprocess.run(
        [python_exe, "-u", script_path] + script_args,
        cwd=project_dir,
        env=env,
    )
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
