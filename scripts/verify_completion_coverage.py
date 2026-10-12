"""Run offline completion tests and measure stdlib trace statement coverage.

No source exclusions or pragma suppression. Every executable statement in the
new completion modules, including imports and dataclass declarations, counts.
This is statement coverage, not branch coverage or scientific validation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import trace
import unittest


def verify(output: Path, threshold: float = 80.0) -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    tracer = trace.Trace(count=True, trace=False, ignoredirs=[sys.base_prefix])

    def run_tests() -> unittest.TestResult:
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_completion*.py")
        return unittest.TextTestRunner(verbosity=1).run(suite)

    result = tracer.runfunc(run_tests)
    counts = tracer.results().counts
    modules = {}
    for path in sorted((root / "research_bot" / "completion").rglob("*.py")):
        if path.name == "__init__.py":
            continue
        executable = set(trace._find_executable_linenos(str(path)))
        hit = {line for (filename, line), count in counts.items()
               if Path(filename).resolve() == path.resolve() and count > 0} & executable
        modules[str(path.relative_to(root))] = {"covered": len(hit), "statements": len(executable),
            "coverage_percent": round(100 * len(hit) / len(executable), 2),
            "missing_lines": sorted(executable - hit)}
    covered = sum(row["covered"] for row in modules.values())
    statements = sum(row["statements"] for row in modules.values())
    report = {"method": "Python stdlib trace executable statement coverage; no source exclusions",
        "python": sys.version.split()[0], "tests": result.testsRun,
        "test_success": result.wasSuccessful(), "coverage_percent": round(100 * covered / statements, 2),
        "covered": covered, "statements": statements, "modules": modules}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "modules"}, sort_keys=True))
    for name, row in modules.items():
        print(f'{name}: {row["coverage_percent"]}%')
    return 0 if result.wasSuccessful() and all(row["coverage_percent"] > threshold for row in modules.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("completion-coverage.json"))
    parser.add_argument("--threshold", type=float, default=80.0)
    args = parser.parse_args()
    return verify(args.output, args.threshold)


if __name__ == "__main__":
    raise SystemExit(main())
