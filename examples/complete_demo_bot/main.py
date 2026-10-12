"""Replacement demo entry point; install the canonical project before running.

No legacy random-weight HPT, immediate-fill simulator, synthetic-as-live fallback
or ungated TP/SL execution is imported by this migration entry point.
"""
from pathlib import Path
import sys
import research_bot

# scripts/ is a source-tree CLI, not part of the research_bot wheel. Resolve the
# explicit editable checkout so this entry point also works inside the old demo.
repository = Path(research_bot.__file__).resolve().parent.parent
if not (repository / "scripts" / "run_demo_migration.py").is_file():
    raise RuntimeError("Install the reviewed source checkout with: python -m pip install -e .")
sys.path.insert(0, str(repository))
from scripts.run_demo_migration import main

if __name__ == "__main__":
    main()
