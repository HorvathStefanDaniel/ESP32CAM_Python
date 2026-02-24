# Running without scripts (work laptop / execution policy)

If you cannot run `.ps1` or `.bat` scripts, use one of these options.

## Option 1: Use the batch file

Many environments allow `.bat` but block PowerShell scripts. From the **main** folder in Command Prompt (cmd):

```cmd
run.bat hand_gesture --webcam --led-url http://10.209.65.13
```

Or with the camera stream:

```cmd
run.bat hand_gesture --url http://192.168.137.91/stream --led-url http://192.168.137.22 --mirror
```

To open the launcher UI:

```cmd
run.bat gesture_launcher
```

---

## Option 2: Run commands manually (no script at all)

Open **Command Prompt** or **PowerShell**, then `cd` to the **main** folder. Run the following in order. Replace `...` with your extra options (e.g. `--webcam --led-url http://10.0.0.1`).

**First time only (create venv and install deps):**

```cmd
cd path\to\ESP32CAM_Python\main

python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If `python` is not found, try:

```cmd
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**Every time you want to run hand gesture:**

```cmd
cd path\to\ESP32CAM_Python\main

set PYTHONPATH=%CD%
.venv\Scripts\python.exe -u hand_gesture.py ... 
```

Examples for `...`:

- Webcam + LED: `--webcam --led-url http://10.209.65.13`
- Camera stream + LED: `--url http://192.168.137.91/stream --led-url http://192.168.137.22 --mirror`
- Launcher UI: run `.venv\Scripts\python.exe -u gesture_launcher.py` instead of `hand_gesture.py ...`

**PowerShell (manual):**

```powershell
cd path\to\ESP32CAM_Python\main
$env:PYTHONPATH = (Get-Location).Path
.\.venv\Scripts\python.exe -u hand_gesture.py --webcam --led-url http://10.209.65.13
```

---

## Option 3: Bypass PowerShell policy once (if only .ps1 is blocked)

In PowerShell, for the current process only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run.ps1 hand_gesture --webcam --led-url http://10.209.65.13
```

If Group Policy locks execution policy, this may still be denied; use Option 1 or 2 instead.
