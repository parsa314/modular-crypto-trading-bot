import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research_bot.course.economic_acceptance import (
    assess_event_panel,
    finalize_family,
    holm_adjust,
)


def protocol():
    return json.loads(Path("research_bot/course/configs/course_economic_pilot_v1.json").read_text())


def test_holm_known_family_with_unsorted_ties():
    assert holm_adjust([.04, .01, .01, .2]) == pytest.approx([.08, .04, .04, .2])


@pytest.mark.parametrize("bad", [[], [.1, float("nan")], [-.1], [1.1], [[.1]]])
def test_holm_rejects_invalid_family(bad):
    with pytest.raises(ValueError):
        holm_adjust(bad)


def events():
    start = pd.date_range("2025-01-01", periods=50, freq="8h", tz="UTC")
    return pd.DataFrame({"event_id": [str(i) for i in range(50)], "direction": [-1]*50,
                         "gross_return": [-.01]*50, "round_trip_cost": [0]*50,
                         "net_return": [-.01]*50, "entry_time": start,
                         "exit_time": start+pd.Timedelta(hours=4)})


def test_losing_shorts_remain_losing_and_cost_is_charged_once():
    p = protocol(); p["bootstrap_runs"] = 40
    report = assess_event_panel(events(), p)
    assert report["scenarios"]["24"]["mean_net_event_return"] == pytest.approx(-.0124)
    assert report["economic_confirmation"] == "ECONOMIC_CONFIRMATION_DEFERRED"
    assert report["execution_authorization"] == "DENIED_RESEARCH_ONLY"
    assert report["dsr"]["status"] == "NOT_APPLICABLE"
    assert report["scenarios"]["24"]["total_return"] is None


def test_precharged_cost_cannot_be_charged_again():
    x = events(); x["round_trip_cost"] = .0024; x["net_return"] -= .0024
    with pytest.raises(ValueError, match="COST_MUST_NOT"):
        assess_event_panel(x, protocol())


def test_missing_panels_are_retained_in_family_not_dropped():
    p = protocol()
    panels = [assess_event_panel(pd.DataFrame(), p) for _ in range(30)]
    finalize_family(panels, p)
    assert all(panel["holm_adjusted_p"] == 1 for panel in panels)
    assert all(panel["economic_confirmation"] == "ECONOMIC_CONFIRMATION_DEFERRED" for panel in panels)
    with pytest.raises(ValueError, match="INCOMPLETE_PREREGISTERED_FAMILY"):
        finalize_family(panels[:-1], p)


def test_even_positive_family_never_authorizes_or_economically_promotes():
    p = protocol(); p["bootstrap_runs"] = 40
    x = events(); x["gross_return"] = .02; x["net_return"] = .02
    report = assess_event_panel(x, p)
    report["p_one_sided"] = 0.0001
    family = [report.copy() for _ in range(30)]
    finalize_family(family, p)
    assert family[0]["pilot_result"] == "CONDITIONAL_POSITIVE_PILOT"
    assert family[0]["economic_confirmation"] == "ECONOMIC_CONFIRMATION_DEFERRED"
    assert family[0]["execution_authorization"] == "DENIED_RESEARCH_ONLY"
    assert np.isfinite(family[0]["holm_adjusted_p"])
