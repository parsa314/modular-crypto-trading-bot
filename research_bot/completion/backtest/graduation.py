"""Fail-closed BACKTEST -> research PAPER gate, not live authorization.

Engineering demonstrations never graduate. Frozen experiment, original first-
seen records, all tried return paths and positive block inference are required.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import re
from typing import Mapping
import numpy as np
import pandas as pd

from ...hypothesis import canonical_json
from ...jamalian_validation import ValidationHarness
from ...regime_robustness import moving_block_bootstrap
from ..journal import TradeJournal, FirstSeenRegistry
from ..models import decimal_value


@dataclass(frozen=True)
class GraduationProtocol:
    preregistration_sha: str
    dataset_sha256: str
    trial_ids: tuple[str, ...]
    selected_trial: str
    strategy_id: str
    oos_locked: bool
    fill_model_validated: bool
    configuration_sha256: str
    execution_model_sha256: str
    dsr_threshold: Decimal = Decimal("0.95")
    max_pbo: Decimal = Decimal("0.10")
    embargo: pd.Timedelta = pd.Timedelta(0)
    block_length: int = 18
    bootstrap_runs: int = 700
    seed: int = 314

    def __post_init__(self) -> None:
        if (not re.fullmatch(r"[a-f0-9]{40}", self.preregistration_sha)
                or any(not re.fullmatch(r"[a-f0-9]{64}", value) for value in
                    (self.dataset_sha256, self.configuration_sha256, self.execution_model_sha256))):
            raise ValueError("frozen preregistration and dataset hashes required")
        object.__setattr__(self, "trial_ids", tuple(self.trial_ids))
        if self.selected_trial not in self.trial_ids or len(set(self.trial_ids)) != len(self.trial_ids) or not self.strategy_id.strip():
            raise ValueError("complete unique trial inventory required")
        for name in ("dsr_threshold", "max_pbo"):
            value = decimal_value(getattr(self, name))
            if value > 1:
                raise ValueError("bounded probability threshold required")
            object.__setattr__(self, name, value)
        if type(self.block_length) is not int or self.block_length < 2 or type(self.bootstrap_runs) is not int or self.bootstrap_runs < 100 or self.embargo < pd.Timedelta(0):
            raise ValueError("valid block inference and embargo configuration required")


class GraduationPipeline:
    def __init__(self, journal: TradeJournal) -> None:
        self.journal = journal

    def evaluate(self, backtest: dict, protocol: GraduationProtocol, net_returns: pd.DataFrame,
                 label_ends: pd.DatetimeIndex, first_seen: FirstSeenRegistry, at: datetime) -> dict:
        reasons = []
        if backtest.get("mode") != "BACKTEST":
            reasons.append("BACKTEST_REQUIRED")
        if backtest.get("purpose") == "SYNTHETIC_ENGINEERING":
            reasons.append("SYNTHETIC_CANNOT_GRADUATE")
        if protocol.dataset_sha256 != backtest.get("dataset_sha256"):
            reasons.append("DATASET_PROVENANCE_MISMATCH")
        if (protocol.configuration_sha256 != backtest.get("configuration_sha256")
                or protocol.execution_model_sha256 != backtest.get("execution_model_sha256")):
            reasons.append("FROZEN_CONFIGURATION_MISMATCH")
        if not protocol.oos_locked or not protocol.fill_model_validated:
            reasons.append("OOS_OR_EXECUTION_PROTOCOL_UNVERIFIED")
        if tuple(net_returns.columns) != protocol.trial_ids:
            reasons.append("INCOMPLETE_TRIAL_INVENTORY")
        coverage = first_seen.report()
        source_times = set(backtest.get("source_event_times", ()))
        if (not coverage["prospective_clean"] or not source_times
                or not source_times.issubset(coverage["observed_times"])
                or len(source_times) < backtest.get("frames", 0)):
            reasons.append("FIRST_SEEN_NOT_CLEAN")
        if Decimal(backtest["metrics"]["net_total_return"]) <= 0:
            reasons.append("NONPOSITIVE_NET_ECONOMIC_PERFORMANCE")
        diagnostics, block = None, None
        expected = np.asarray([float(Decimal(v)) for v in backtest["metrics"]["net_returns"]])
        if (protocol.selected_trial not in net_returns or len(expected) != len(net_returns)
                or not np.array_equal(expected, net_returns.get(protocol.selected_trial, pd.Series(dtype=float)).to_numpy(float))):
            reasons.append("SELECTED_TRIAL_DOES_NOT_MATCH_SIMULATOR_RETURNS")
        expected_index = pd.DatetimeIndex([r["timestamp"] for r in backtest.get("curve", [])[1:]])
        if not net_returns.index.equals(expected_index):
            reasons.append("TRIAL_TIMESTAMPS_DO_NOT_MATCH_REPLAY")
        if not reasons:
            try:
                diagnostics = ValidationHarness().diagnose(net_returns, label_ends,
                    dataset_sha256=protocol.dataset_sha256, trial_count=len(protocol.trial_ids),
                    data_class="DEVELOPMENT_REPLAY", embargo=protocol.embargo)
                dsr = diagnostics["dsr_per_period"][protocol.selected_trial]
                if dsr <= float(protocol.dsr_threshold):
                    reasons.append("DSR_THRESHOLD_NOT_MET")
                if diagnostics["cscv_pbo"]["pbo"] > float(protocol.max_pbo):
                    reasons.append("PBO_DATA_SNOOPING_FLAG")
                block = moving_block_bootstrap(net_returns[protocol.selected_trial], block=protocol.block_length,
                    runs=protocol.bootstrap_runs, seed=protocol.seed)
                if not np.isfinite(block["ci_low"]) or block["ci_low"] <= 0 or block["p_one_sided"] >= 0.05:
                    reasons.append("DEPENDENCE_AWARE_POSITIVE_EDGE_NOT_SUPPORTED")
            except ValueError as error:
                reasons.append("VALIDATION_SUPPORT_UNAVAILABLE:" + str(error))
        result = {"paper_allowed": not reasons, "live_execution": False,
            "eligible_strategy_ids": [protocol.strategy_id] if not reasons else [],
            "reasons": reasons, "preregistration_sha": protocol.preregistration_sha,
            "dataset_sha256": protocol.dataset_sha256, "selected_trial": protocol.selected_trial,
            "configuration_sha256": protocol.configuration_sha256,
            "execution_model_sha256": protocol.execution_model_sha256,
            "trial_ids": list(protocol.trial_ids), "diagnostics": diagnostics, "block_inference": block,
            "scope": "INDEPENDENT_RESEARCH_PAPER_ONLY_NO_ALPHA_GUARANTEE"}
        # Non-finite statistical diagnostics must not be committed as evidence.
        encoded = canonical_json(result)
        with self.journal.atomic():
            self.journal.append("graduation:" + sha256(encoded.encode()).hexdigest(), "GRADUATION", at, result)
            self.journal.checkpoint("graduation", result)
        return result
