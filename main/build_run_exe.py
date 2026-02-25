"""
Build run.exe from run_launcher.py using PyInstaller.
Run from the project directory:  python build_run_exe.py
Creates run.exe in the same folder as run.ps1 (and run_launcher.py).
Double-click run.exe to get a menu, or run from cmd: run.exe hand_gesture --webcam
"""

import os
import subprocess
import sys

def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    print("Installing PyInstaller if needed ...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pyinstaller"], check=True, timeout=120)

    launcher = os.path.join(project_dir, "run_launcher.py")
    if not os.path.isfile(launcher):
        print("ERROR: run_launcher.py not found.", file=sys.stderr)
        sys.exit(1)

    # --onefile = single exe; --console = keep console for menu and script output
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "run",
        "--console",
        "--distpath", project_dir,
        "--specpath", os.path.join(project_dir, "build"),
        "--workpath", os.path.join(project_dir, "build"),
        "-y",
        launcher,
    ]
    print("Building run.exe ...")
    r = subprocess.run(cmd, cwd=project_dir)
    if r.returncode != 0:
        sys.exit(r.returncode)

    exe_path = os.path.join(project_dir, "run.exe")
    if os.path.isfile(exe_path):
        print(f"Done. Double-click {exe_path} or run: run.exe hand_gesture --webcam")
    else:
        print("Build may have placed run.exe elsewhere; check the build folder.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
