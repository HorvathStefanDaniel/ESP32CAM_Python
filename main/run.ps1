# Run a Python script with auto-created venv and dependency install.
# Usage: .\run.ps1 object_count [--url http://...]
#        .\run.ps1 face_detect [--url http://...] [--recognize]
#        .\run.ps1 hand_gesture [--url http://...] [--led-url http://...] [--mirror]
#        .\run.ps1 hand_gesture --webcam [--led-url http://...] [--mirror]
#        .\run.ps1 gesture_launcher   (UI to paste Camera + LED IPs, then Start)

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("object_count", "face_detect", "hand_gesture", "gesture_launcher")]
    [string]$Script,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$ProjectDir = $PSScriptRoot
$VenvDir = Join-Path $ProjectDir ".venv"
$Pip = Join-Path $VenvDir "Scripts\pip.exe"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectDir "requirements.txt"

# Create venv if missing (hand_gesture needs Python 3.9+ for mediapipe)
if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment in .venv ..."
    $venvPython = $null
    if ($Script -eq "hand_gesture") {
        foreach ($ver in @("3.11", "3.10", "3.9")) {
            $py = Get-Command "py" -ErrorAction SilentlyContinue
            if ($py) {
                & py -$ver -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    & py -$ver -m venv $VenvDir
                    if ($LASTEXITCODE -eq 0) { break }
                }
            }
        }
    }
    if (-not (Test-Path $Python)) {
        python -m venv $VenvDir
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# hand_gesture and gesture_launcher need Python 3.9+ for mediapipe when launched from UI
if ($Script -eq "hand_gesture" -or $Script -eq "gesture_launcher") {
    $ver = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    & $Python -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: hand_gesture requires Python 3.9+. Current venv is Python $ver." -ForegroundColor Red
        Write-Host "Remove .venv and install Python 3.9+ (e.g. from python.org), then run this script again." -ForegroundColor Yellow
        exit 1
    }
}

# Install dependencies
Write-Host "Ensuring dependencies are installed ..."
& $Python -m pip install -q -r $Requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Run the script; ensure project dir is on Python path so "import stream_reader" works
$ScriptPath = Join-Path $ProjectDir "$Script.py"
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$ProjectDir;$env:PYTHONPATH" } else { $env:PYTHONPATH = $ProjectDir }
# -u = unbuffered; 2>&1 = show stderr (tracebacks) in console
& $Python -u $ScriptPath @ScriptArgs 2>&1
exit $LASTEXITCODE
