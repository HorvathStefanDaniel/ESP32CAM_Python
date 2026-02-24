@echo off
REM Run a Python script with auto-created venv and dependency install.
REM Usage: run.bat hand_gesture [--url http://...] [--led-url http://...] [--mirror]
REM        run.bat hand_gesture --webcam [--led-url http://...] [--mirror]
REM        run.bat gesture_launcher
REM If scripts are blocked: run the "Manual steps" below in cmd instead.

set "SCRIPT=%~1"
if "%SCRIPT%"=="" (
  echo Usage: run.bat hand_gesture ^| gesture_launcher ^| face_detect ^| object_count [options]
  echo Example: run.bat hand_gesture --webcam --led-url http://10.0.0.1
  echo.
  pause
  exit /b 1
)
shift

set "PROJECT_DIR=%~dp0"
set "PROJECT_DIR=%PROJECT_DIR:~0,-1%"
set "VENV_DIR=%PROJECT_DIR%\.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"
set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "REQUIREMENTS=%PROJECT_DIR%\requirements.txt"
set "SCRIPT_PATH=%PROJECT_DIR%\%SCRIPT%.py"

if not exist "%SCRIPT_PATH%" (
  echo Script not found: %SCRIPT_PATH%
  echo.
  pause
  exit /b 1
)

if not exist "%PYTHON%" (
  echo Creating virtual environment in .venv ...
  where py >nul 2>&1
  if %ERRORLEVEL% equ 0 (
    py -3.11 -m venv "%VENV_DIR%" 2>nul || py -3.10 -m venv "%VENV_DIR%" 2>nul || py -3.9 -m venv "%VENV_DIR%" 2>nul
  )
  if not exist "%PYTHON%" (
    python -m venv "%VENV_DIR%"
  )
  if not exist "%PYTHON%" (
    echo Failed to create venv. Install Python 3.9+ from python.org and ensure 'python' or 'py' is on PATH.
    echo.
    pause
    exit /b 1
  )
)

echo Ensuring dependencies are installed ...
"%PYTHON%" -m pip install -q -r "%REQUIREMENTS%"
if %ERRORLEVEL% neq 0 (
  echo Pip install failed.
  echo.
  pause
  exit /b %ERRORLEVEL%
)

if defined PYTHONPATH (set "PYTHONPATH=%PROJECT_DIR%;%PYTHONPATH%") else (set "PYTHONPATH=%PROJECT_DIR%")
"%PYTHON%" -u "%SCRIPT_PATH%" %*
echo.
pause
exit /b %ERRORLEVEL%
