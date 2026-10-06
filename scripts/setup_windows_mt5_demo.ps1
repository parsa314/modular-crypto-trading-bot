#requires -Version 5.1
<#
.SYNOPSIS
    Prepare a Windows/VPS host for the local MT5 DEMO control panel.

.DESCRIPTION
    Creates a Python virtual environment, installs the project with the MT5
    optional extra, writes a local-only .env.mt5.local template if missing,
    checks for the MetaTrader 5 desktop terminal, and can start the panel.

    This script never writes an MT5 password.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "============================================================"
Write-Host "  Modular Crypto Bot - MT5 DEMO Windows Setup"
Write-Host "============================================================"
Write-Host ""

function Find-Python {
    $candidates = @("py", "python")
    foreach ($name in $candidates) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $cmd) { return $name }
    }
    throw "Python 3.11+ was not found. Install Python, then run this script again."
}

$Python = Find-Python

try {
    $versionText = & $Python --version 2>&1
    Write-Host "Python: $versionText"
} catch {
    throw "Unable to execute Python."
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv ..."
    & $Python -m venv .venv
}

$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment creation failed."
}

Write-Host "Upgrading pip ..."
& $VenvPython -m pip install --upgrade pip

Write-Host "Installing project + DEV + MT5 dependencies ..."
& $VenvPython -m pip install -e ".[dev,mt5]"

$envFile = Join-Path $PSScriptRoot ".env.mt5.local"
if (-not (Test-Path $envFile)) {
@"
# LOCAL ONLY - DO NOT COMMIT
MT5_SERVER=MetaQuotes-Demo
MT5_LOGIN=
MT5_PASSWORD=
MT5_TERMINAL_PATH=
MT5_DEMO_SUBMIT_ENABLED=0

# Direct strategy defaults
MT5_MAX_ORDER_NOTIONAL=5000
MT5_MAX_SPREAD_BPS=35

# The Python process will read MT5_PASSWORD at runtime.
# This file is ignored by git when named .env.mt5.local.
"@ | Set-Content -Path $envFile -Encoding UTF8
    Write-Host "Created $envFile"
} else {
    Write-Host "$envFile already exists; leaving it unchanged."
}

$terminalHints = @(
    "$env:APPDATA\MetaQuotes\Terminal",
    "$env:ProgramFiles\MetaTrader 5\terminal64.exe",
    "$env:ProgramFiles(x86)\MetaTrader 5\terminal64.exe"
)

$foundTerminal = $false
foreach ($hint in $terminalHints) {
    if ($hint -like "*.exe" -and (Test-Path $hint)) {
        $foundTerminal = $true
        Write-Host "MT5 terminal found: $hint"
    }
    elseif ($hint -like "*Terminal" -and (Test-Path $hint)) {
        $foundTerminal = $true
        Write-Host "MetaTrader data directory found: $hint"
    }
}

if (-not $foundTerminal) {
    Write-Warning "MetaTrader 5 Desktop was not detected. Install/open MT5 Desktop and log into the DEMO account first."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Next:"
Write-Host "  1) Open MetaTrader 5 Desktop and login to the DEMO account."
Write-Host "  2) Set MT5_LOGIN / MT5_PASSWORD locally, or enter credentials in the panel."
Write-Host "  3) Run .\START_TRADINGVIEW_MT5_DEMO_PANEL.bat"
Write-Host "  4) Keep DEMO submission disabled for the first Evaluate Once."
Write-Host ""

$start = Read-Host "Start the local panel now? [Y/N]"
if ($start -match "^[Yy]$") {
    & (Join-Path $PSScriptRoot "START_TRADINGVIEW_MT5_DEMO_PANEL.bat")
}
