#!/usr/bin/env bash
set -euo pipefail
paper_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$paper_root"
if [[ -n "${PAPER_PYTHON:-}" ]]; then
  paper_python="$(command -v -- "$PAPER_PYTHON")"
else
  paper_python="$paper_root/.venv-paper/bin/python"
  if [[ ! -x "$paper_python" ]]; then
    python3 -m venv "$paper_root/.venv-paper"
  fi
  if ! "$paper_python" -c 'import pandas, numpy, yaml, websockets, pyarrow' >/dev/null 2>&1; then
    "$paper_python" -m pip install -e '.[paper]'
  fi
fi
export PYTHONPATH="$paper_root/src:$paper_root${PYTHONPATH:+:$PYTHONPATH}"
exec "$paper_python" -m paper_trading.main "$@"
