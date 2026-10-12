"""Strict external stress settings; existing domain dataclasses stay intact."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import CompletionPolicy
from ...jamalian_data import decimal_value


class SimulationStressInput(BaseModel):
    """Research assumptions, not calibrated venue rates or fill probabilities."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    displayed_depth_fraction: Decimal = Decimal("0.10")
    min_submission_interval_ms: int = Field(default=100, ge=0, strict=True)

    @field_validator("displayed_depth_fraction", mode="before")
    @classmethod
    def _strict_fraction(cls, value: object) -> Decimal:
        result = decimal_value(value, positive=True)
        if result > Decimal(1):
            raise ValueError("displayed depth fraction must be in (0,1]")
        return result

    def to_policy(self, base: CompletionPolicy | None = None) -> CompletionPolicy:
        return replace(base or CompletionPolicy(), displayed_depth_fraction=self.displayed_depth_fraction,
            min_submission_interval_ms=self.min_submission_interval_ms)
