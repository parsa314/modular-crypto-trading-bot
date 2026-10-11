"""Load versioned evaluation parameters with auditable, unapproved provenance."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from .evaluation import EvaluationConfig

DEFAULT_EVALUATION_CONFIG = (
    Path(__file__).resolve().parent / "configs/course_evaluation_v1.json"
)


def load_evaluation_config(path: Path = DEFAULT_EVALUATION_CONFIG) -> EvaluationConfig:
    """Reject incomplete metadata; loading never authorizes promotion or execution."""
    payload = json.loads(path.read_text())
    names = {f.name for f in fields(EvaluationConfig)}
    if set(payload) != names | {"parameter_governance"}:
        raise ValueError("INVALID_EVALUATION_CONFIG_FIELDS")
    governance = payload.pop("parameter_governance")
    if not isinstance(governance, dict) or set(governance) != names:
        raise ValueError("INCOMPLETE_PARAMETER_GOVERNANCE")
    required = {
        "type",
        "unit",
        "allowed_domain",
        "reference_value",
        "source",
        "version",
        "determination_method",
        "scientific_approval",
    }
    for name, value in payload.items():
        entry = governance[name]
        if (
            not isinstance(entry, dict)
            or set(entry) != required
            or entry["reference_value"] != value
            or entry["version"] != payload["version"]
            or entry["scientific_approval"] != "NOT_APPROVED_FOR_PROMOTION"
            or any(
                not isinstance(entry[k], str) or not entry[k]
                for k in required - {"reference_value"}
            )
        ):
            raise ValueError("INVALID_PARAMETER_GOVERNANCE")
    payload["cost_bps"] = tuple(payload["cost_bps"])
    payload["model_names"] = tuple(payload["model_names"])
    return EvaluationConfig(**payload)
