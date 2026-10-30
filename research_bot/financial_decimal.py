"""Exact wire/ledger decimal conversion, independent of analytical floats."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_value(value: Any, name: str = "value") -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal {name}") from exc
    if not number.is_finite():
        raise ValueError(f"nonfinite decimal {name}")
    # A bounded wire contract avoids silent arithmetic rounding / resource abuse.
    if len(number.as_tuple().digits) > 60 or abs(number.as_tuple().exponent) > 36:
        raise ValueError(f"unsupported decimal precision {name}")
    return number
