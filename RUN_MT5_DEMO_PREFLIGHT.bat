@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup_windows_mt5_demo.ps1 first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
echo ============================================================
echo V59 MT5 DEMO PREFLIGHT - NO ORDER WILL BE SENT
echo ============================================================
python -m research_bot.mt5_preflight
echo.
echo Preflight finished. This command NEVER submits an order.
pause
