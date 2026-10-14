@echo off
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo   Direct V59 Confluence + AI -> MetaTrader 5 DEMO
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo Python environment not prepared.
  echo Run scripts\setup_windows_mt5_demo.ps1 first.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo.
echo DEMO submission is intentionally OFF unless you explicitly set:
echo   MT5_DEMO_SUBMIT_ENABLED=1
echo and pass --submit-demo.
echo.

python scripts\run_mt5_direct_demo_strategy.py ^
  --canonical-symbol BTC/USDT ^
  --venue-symbol BTCUSD ^
  --strategy H4_V59_CONFLUENCE_DEMO ^
  --ai-gate ^
  --risk-percent 0.25 ^
  --bars 600 ^
  --poll-seconds 15

echo.
echo Direct MT5 DEMO runner stopped.
pause
