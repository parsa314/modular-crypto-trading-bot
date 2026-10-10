@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment is not ready.
  echo Run scripts\setup_windows_mt5_demo.ps1 first.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo ============================================================
echo  V59 Confluence + AI -> MT5 DEMO AUTO AGENT
echo ============================================================
echo.
echo Account: MT5 Demo only
echo Strategy: H4_V59_CONFLUENCE_DEMO
echo AI gate: ON
echo Risk: 0.25%%
echo.
echo NOTE: MetaTrader 5 Desktop must be logged into the DEMO account.
echo Real-money MT5 accounts are refused by the bot.
echo.

python -m research_bot.mt5_demo_agent

echo.
echo Agent stopped.
pause
