from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .contracts import require_aware_utc


class Partition(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


_ALLOWED_PARTITIONS = {
    "TRAIN": {Partition.DEVELOPMENT},
    "FIT_PREPROCESSOR": {Partition.DEVELOPMENT},
    "FEATURE_SELECTION": {Partition.DEVELOPMENT},
    "CALIBRATE": {Partition.VALIDATION},
    "THRESHOLD_SELECTION": {Partition.VALIDATION},
    "HYPERPARAMETER_TUNING": {Partition.VALIDATION},
    "MODEL_SELECTION": {Partition.VALIDATION},
    "EVALUATE": {Partition.DEVELOPMENT, Partition.VALIDATION, Partition.TEST},
    "FINAL_EVALUATION": {Partition.FINAL_HOLDOUT},
}


@dataclass(frozen=True)
class TemporalSeal:
    development_end: datetime
    validation_end: datetime
    holdout_end: datetime
    manifest_hash: str

    def __post_init__(self) -> None:
        d = require_aware_utc(self.development_end, name="development_end")
        v = require_aware_utc(self.validation_end, name="validation_end")
        h = require_aware_utc(self.holdout_end, name="holdout_end")
        if not d < v < h:
            raise ValueError("temporal partitions must be strictly ordered")
        if (not isinstance(self.manifest_hash, str) or len(self.manifest_hash) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in self.manifest_hash)):
            raise ValueError("manifest_hash must be SHA-256 hex")

    def partition_for(self, timestamp: datetime) -> Partition:
        ts = require_aware_utc(timestamp, name="timestamp")
        if ts <= self.development_end:
            return Partition.DEVELOPMENT
        if ts <= self.validation_end:
            return Partition.VALIDATION
        if ts <= self.holdout_end:
            return Partition.FINAL_HOLDOUT
        raise ValueError("timestamp outside sealed partitions")


def authorize_partition_access(partition: Partition | str, *, purpose: str,
                               final_evaluation_authorized: bool = False) -> None:
    """Validate caller-declared split metadata; this does not read or unlock data.

    A final evaluation also requires the caller's explicit governance approval.
    TEST is evaluation-only; the existing three-window TemporalSeal is unchanged.
    """
    try:
        partition = Partition(partition)
    except (ValueError, TypeError) as exc:
        raise ValueError("unknown partition") from exc
    if not isinstance(purpose, str):
        raise ValueError("purpose must be a string")
    normalized = purpose.strip().upper()
    if normalized not in _ALLOWED_PARTITIONS:
        raise ValueError(f"unknown partition access purpose: {normalized!r}")
    if partition not in _ALLOWED_PARTITIONS[normalized]:
        raise PermissionError(f"{partition.value} access forbidden for {normalized}")
    if partition is Partition.FINAL_HOLDOUT and final_evaluation_authorized is not True:
        raise PermissionError("final holdout evaluation requires explicit authorization")
