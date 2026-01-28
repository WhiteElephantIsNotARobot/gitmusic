# GitMusic CLI 启动脚本
$PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VENV_PYTHON = "$PSScriptRoot\.venv\Scripts\python.exe"
$CLI_SCRIPT = "$PSScriptRoot\repo\tools\cli.py"

if (Test-Path $VENV_PYTHON) {
    & $VENV_PYTHON $CLI_SCRIPT $args
} else {
    Write-Host "Error: Could not find virtual environment Python ($VENV_PYTHON)" -ForegroundColor Red
    Write-Host "Please ensure .venv is created and dependencies are installed." -ForegroundColor Yellow
    pause
}
