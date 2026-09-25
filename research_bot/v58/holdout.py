from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .contracts import require_aware_utc


class Partition(str, Enum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


_FORBIDDEN_HOLDOUT_PURPOSES = {
    "TRAIN", "FIT_PREPROCESSOR", "FEATURE_SELECTION", "CALIBRATE",
    "THRESHOLD_SELECTION", "HYPERPARAMETER_TUNING", "MODEL_SELECTION",
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
        if len(self.manifest_hash) != 64:
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


def authorize_partition_access(partition: Partition, *, purpose: str) -> None:
    normalized = purpose.strip().upper()
    if not normalized:
        raise ValueError("purpose is required")
    if partition is Partition.FINAL_HOLDOUT and normalized in _FORBIDDEN_HOLDOUT_PURPOSES:
        raise PermissionError(f"final holdout access forbidden for {normalized}")
