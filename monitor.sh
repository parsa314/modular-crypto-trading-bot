#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
STATE="${1:-logs/bot/state.json}"
python - "$STATE" <<'PY'
from datetime import datetime,timezone
from pathlib import Path
import json,sys
path=Path(sys.argv[1])
if not path.is_file():
    print("STOPPED reason=missing_state");sys.exit(2)
try:
    d=json.loads(path.read_text())
    heartbeat=datetime.fromisoformat(d["heartbeat_utc"])
    age=(datetime.now(timezone.utc)-heartbeat).total_seconds()
    from pathlib import Path as _P
    processes=[]
    for pid in _P("/proc").iterdir():
        if not pid.name.isdigit(): continue
        try:
            command=(pid/"cmdline").read_bytes()
            if b"bot.main" in command and b"python" in command:
                processes.append(int(pid.name))
        except (FileNotFoundError,PermissionError,ProcessLookupError):
            continue
    if d["halted"] or age>180 or age< -30 or not processes:
        print(f'STOPPED age_seconds={age:.0f} halted={d["halted"]} errors={d["errors"]}')
        sys.exit(2)
    print(f'RUNNING nav={d["nav"]:.4f} trades={d["trades"]} errors={d["errors"]} age_seconds={age:.0f}')
except Exception as e:
    print("STOPPED reason="+type(e).__name__)
    sys.exit(2)
PY
