"""Measure real statement coverage for all ten-strategy modules using C-tracer.

Run the registered ten-strategy regression suite only once. Do not claim a
coverage pass if tests fail, the instrumenter fails, or a module is uncovered.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
import unittest

import coverage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=80)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 100:
        raise ValueError("coverage threshold must be in [0, 100]")

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests"), pattern="test_ten_strategies*.py"
    )
    # Limit instrumentation to our own code; coverage.py's CTracer replaces
    # the prohibitively slow stdlib trace.Trace line callback.
    tracker = coverage.Coverage(
        source=["research_bot.ten_strategies"],
        branch=False,
        config_file=False,
        data_file=None,
    )
    started = perf_counter()
    tracker.start()
    try:
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    finally:
        tracker.stop()
    elapsed = round(perf_counter() - started, 3)

    modules = {}
    for file in sorted((root / "research_bot" / "ten_strategies").glob("*.py")):
        if file.name == "__init__.py":
            continue
        _, statements, excluded, missing, _ = tracker.analysis2(str(file))
        covered = len(statements) - len(missing)
        modules[str(file.relative_to(root))] = {
            "coverage_percent": round(100 * covered / len(statements), 2)
            if statements else 0.0,
            "covered": covered,
            "statements": len(statements),
            "missing": missing,
            "excluded": excluded,
        }
    # Evidence is written even when a test/threshold failed, so the PR
    # exposes the exact cause rather than an absent artifact.
    passed = bool(
        result.wasSuccessful()
        and modules
        and all(m["coverage_percent"] >= args.threshold for m in modules.values())
    )
    report = {
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "tests_success": result.wasSuccessful(),
        "success": passed,
        "modules": modules,
        "method": "coverage.py CTracer, scoped package statement coverage",
        "elapsed_seconds": elapsed,
        "threshold": args.threshold,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
