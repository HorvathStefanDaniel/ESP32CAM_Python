# Run a Python script with auto-created venv and dependency install.
# Usage: .\run.ps1 object_count [--url http://...]
#        .\run.ps1 face_detect [--url http://...] [--recognize]

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("object_count", "face_detect")]
    [string]$Script,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$ProjectDir = $PSScriptRoot
$VenvDir = Join-Path $ProjectDir ".venv"
$Pip = Join-Path $VenvDir "Scripts\pip.exe"
$Python = Join-Path $VenvDir "Scripts\python.exe"
$Requirements = Join-Path $ProjectDir "requirements.txt"

# Create venv if missing
if (-not (Test-Path $Python)) {
    Write-Host "Creating virtual environment in .venv ..."
    python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# Install dependencies
Write-Host "Ensuring dependencies are installed ..."
& $Python -m pip install -q -r $Requirements
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Run the script; ensure project dir is on Python path so "import stream_reader" works
$ScriptPath = Join-Path $ProjectDir "$Script.py"
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$ProjectDir;$env:PYTHONPATH" } else { $env:PYTHONPATH = $ProjectDir }
& $Python $ScriptPath @ScriptArgs
exit $LASTEXITCODE
