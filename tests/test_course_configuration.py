import json

import pytest

from research_bot.course.configuration import (
    DEFAULT_EVALUATION_CONFIG,
    load_evaluation_config,
)


def test_versioned_evaluation_defaults_remain_unapproved():
    config = load_evaluation_config()
    assert config.cost_bps == (0, 24, 36, 50)
    assert not config.baseline_admissible
    assert config.independent_trials is None


@pytest.mark.parametrize("mutation", ["missing", "reference", "approved", "extra"])
def test_reject_untraceable_parameter_change(tmp_path, mutation):
    payload = json.loads(DEFAULT_EVALUATION_CONFIG.read_text())
    if mutation == "missing":
        del payload["parameter_governance"]["random_seed"]
    elif mutation == "reference":
        payload["random_seed"] = 9
    elif mutation == "approved":
        payload["parameter_governance"]["random_seed"]["scientific_approval"] = (
            "APPROVED"
        )
    else:
        payload["unknown"] = 5
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_evaluation_config(path)
