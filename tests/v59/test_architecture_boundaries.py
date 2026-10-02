from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path("research_bot/v59")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result


def test_v59_kernel_does_not_depend_on_v58_implementation():
    offenders = []
    for path in ROOT.glob("*.py"):
        if path.name == "compat_v58.py":
            continue
        for name in _imports(path):
            if "research_bot.v58" in name or name.startswith("v58"):
                offenders.append((str(path), name))
    assert offenders == []


def test_v59_phase1_has_no_exchange_or_execution_connector_imports():
    forbidden = ("ccxt", "binance", "coinex", "bybit", "nobitex", "metatrader")
    offenders = []
    for path in ROOT.glob("*.py"):
        for name in _imports(path):
            if any(token in name.lower() for token in forbidden):
                offenders.append((str(path), name))
    assert offenders == []


def test_v59_package_has_explicit_core_modules():
    required = {
        "__init__.py",
        "__main__.py",
        "hashing.py",
        "config.py",
        "contracts.py",
        "decision.py",
        "finance.py",
        "evidence.py",
        "orchestrator.py",
        "registry.py",
        "think_tank.py",
        "compat_v58.py",
    }
    assert required.issubset({path.name for path in ROOT.glob("*.py")})


def test_stage2_source_adapters_are_isolated_from_kernel_and_read_only():
    adapter = Path("research_bot/v59/adapters/ccxt_public.py")
    source = adapter.read_text(encoding="utf-8").lower()
    assert "create_order" not in source
    assert "cancel_order" not in source
    assert "fetch_balance" not in source
    assert "withdraw" not in source
    assert "private" not in source
