"""
Build a portable package of standalone .exe files so users do not need Python,
CMake, or C++ build tools installed. Run from the project directory (main/):
  python build_portable.py

Requires: Python 3.9+ with pip; run once with internet to install deps and build.
Output: dist_portable/ containing run.exe, hand_gesture.exe, face_detect.exe,
        object_count.exe, gesture_launcher.exe. Copy that folder to any Windows PC
        and run run.exe (or any script exe directly). No Python needed on the target.
"""

import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_PORTABLE = os.path.join(PROJECT_DIR, "dist_portable")
# PyInstaller --add-data on Windows: "source;dest" (dest "." = bundle root)
SEP = ";" if sys.platform == "win32" else ":"


def run(cmd, cwd=None, timeout=300):
    r = subprocess.run(cmd, cwd=cwd or PROJECT_DIR, timeout=timeout)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main():
    os.chdir(PROJECT_DIR)
    print("Installing PyInstaller and project dependencies (this can take several minutes) ...")
    run([sys.executable, "-m", "pip", "install", "-q", "pyinstaller"], timeout=120)
    run([sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"], timeout=600)

    os.makedirs(DIST_PORTABLE, exist_ok=True)
    build_dir = os.path.join(PROJECT_DIR, "build")
    spec_dir = os.path.join(PROJECT_DIR, "build", "portable_specs")
    os.makedirs(spec_dir, exist_ok=True)

    common = [
        "--onefile",
        "--console",
        "--distpath", DIST_PORTABLE,
        "--specpath", spec_dir,
        "--workpath", build_dir,
        "--path", PROJECT_DIR,
        "-y",
    ]
    hidden = ["--hidden-import", "stream_reader", "--hidden-import", "cv2", "--hidden-import", "numpy"]

    # Bundle Haar cascade for face_detect.exe (opencv data dir is not always collected)
    try:
        import cv2 as _cv2
        _cascade_src = os.path.join(os.path.dirname(_cv2.__file__), "data", "haarcascade_frontalface_default.xml")
        if os.path.isfile(_cascade_src):
            face_data = ["--add-data", os.path.abspath(_cascade_src) + SEP + "."]
        else:
            face_data = []
    except Exception:
        face_data = []

    scripts = [
        ("object_count", ["object_count.py"], ["--collect-submodules", "cv2"]),
        (
            "face_detect",
            ["face_detect.py"],
            [
                "--hidden-import", "face_recognition",
                "--hidden-import", "face_recognition_models",
                "--collect-submodules", "cv2",
                "--collect-all", "face_recognition",
                "--collect-all", "face_recognition_models",  # .dat models for dlib (shape_predictor_68 etc.)
            ]
            + face_data,
        ),
        ("gesture_launcher", ["gesture_launcher.py"], []),
    ]

    # hand_gesture: bundle .task if present so first run doesn't need download (use absolute path so PyInstaller finds it)
    hand_task = os.path.join(PROJECT_DIR, "hand_landmarker.task")
    hand_data = []
    if os.path.isfile(hand_task):
        hand_data = ["--add-data", os.path.abspath(hand_task) + SEP + "."]

    for name, script_list, extra in scripts:
        script_path = os.path.join(PROJECT_DIR, script_list[0])
        if not os.path.isfile(script_path):
            print(f"Skip {name}: {script_list[0]} not found")
            continue
        print(f"Building {name}.exe ...")
        cmd = (
            [sys.executable, "-m", "PyInstaller"]
            + common
            + ["--name", name]
            + hidden
            + extra
            + [script_path]
        )
        run(cmd, timeout=420)

    # hand_gesture with mediapipe and optional .task
    hg_path = os.path.join(PROJECT_DIR, "hand_gesture.py")
    if os.path.isfile(hg_path):
        # Remove stale spec so a previous failed run doesn't reference missing hand_landmarker.task
        stale_spec = os.path.join(spec_dir, "hand_gesture.spec")
        if os.path.isfile(stale_spec):
            try:
                os.remove(stale_spec)
            except OSError:
                pass
        print("Building hand_gesture.exe (MediaPipe bundle, may take a few minutes) ...")
        cmd = (
            [sys.executable, "-m", "PyInstaller"]
            + common
            + ["--name", "hand_gesture"]
            + ["--path", PROJECT_DIR]
            + ["--hidden-import", "stream_reader", "--hidden-import", "cv2", "--hidden-import", "numpy"]
            + ["--collect-all", "mediapipe"]
            + hand_data
            + [hg_path]
        )
        run(cmd, timeout=600)

    # Launcher run.exe (prefer sibling .exe when present)
    print("Building run.exe (launcher) ...")
    run(
        [
            sys.executable, "-m", "PyInstaller",
            "--onefile", "--name", "run", "--console",
            "--distpath", DIST_PORTABLE,
            "--specpath", spec_dir,
            "--workpath", build_dir,
            "-y",
            os.path.join(PROJECT_DIR, "run_launcher.py"),
        ],
        timeout=120,
    )

    # README for portable folder
    readme = os.path.join(DIST_PORTABLE, "README_portable.txt")
    with open(readme, "w", encoding="utf-8") as f:
        f.write("ESP32-CAM Python – Portable package\n")
        f.write("====================================\n\n")
        f.write("No Python or build tools needed on this PC.\n\n")
        f.write("• Double-click run.exe for a menu (script, webcam, URLs, options).\n")
        f.write("• Or run any script directly, e.g. hand_gesture.exe --webcam --led-url http://LED_IP\n\n")
        f.write("• For face recognition: create a folder 'known_faces' here and add photos (name.jpg).\n")
        f.write("  Then use run.exe -> face_detect and choose recognition, or run face_detect.exe --recognize.\n\n")
        f.write("• hand_gesture.exe may download hand_landmarker.task on first run if not bundled.\n")
        f.write("  Place hand_landmarker.task in this folder to avoid that.\n\n")
        f.write("• Keep this folder intact; do not move individual .exe files away from each other.\n")

    print(f"\nDone. Portable package is in: {DIST_PORTABLE}")
    print("Copy that folder to any Windows PC and run run.exe (or any .exe). No Python required.")


if __name__ == "__main__":
    main()
