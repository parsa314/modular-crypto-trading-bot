# Run the paper bot (Linux/VPS/laptop)
1. `python -m pip install -r requirements-bot.txt` (Python 3.11+).
2. `./start.sh` = Binance public H4 WebSocket, ten selected spot symbols, **$1,000 simulated**, never real orders.
3. `./start.sh src/bot/config.replay.yaml` = publisher-checksummed BTCUSDT M15 → H4 replay; logs under `logs/bot-replay/`.
4. `./monitor.sh` checks heartbeat; install `deploy/paper-bot.service` as user systemd service after editing WorkingDirectory and ExecStart for your location; then `systemctl --user enable --now paper-bot.service`.
5. UTC JSONL and atomic state persist; fatal feed gaps **halt and latch** until manual reconciliation. No 24h/14d uptime or financial returns are claimed in advance.
