@echo off
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   TradingView - MetaTrader 5 DEMO Control Panel
echo ============================================================
echo.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PY=py"
) else (
  where python >nul 2>nul
  if %ERRORLEVEL% neq 0 (
    echo Python was not found. Install Python 3.11+ and run this file again.
    pause
    exit /b 1
  )
  set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  %PY% -m venv .venv
  if %ERRORLEVEL% neq 0 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"

if not exist ".venv\.mt5_panel_ready" (
  echo Installing project and MetaTrader5 Python integration...
  python -m pip install --upgrade pip
  python -m pip install -e ".[dev,mt5]"
  if %ERRORLEVEL% neq 0 (
    echo Installation failed.
    pause
    exit /b 1
  )
  echo ready>".venv\.mt5_panel_ready"
)

echo Starting local control panel...
python scripts\start_mt5_tradingview_ui.py

echo.
echo Control panel stopped.
pause
