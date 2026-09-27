import numpy as np

from research_bot.confluence_v53 import decide_confluence_v53


def test_missing_ichimoku_and_htf_data_do_not_create_short_votes():
    row = {
        "smc_structure_state": 0,
        "brooks_always_in": 0,
        "brooks_market_trend": 0,
        "ichi_tk_bullish": np.nan,
        "ichi_price_below_visible_cloud": np.nan,
        "ichi_projected_cloud_bullish": np.nan,
        "4h_smc_structure_state": np.nan,
        "4h_brooks_always_in": np.nan,
        "4h_ichi_projected_cloud_bullish": np.nan,
    }
    out = decide_confluence_v53(row)
    assert out.action == "NO_TRADE"
    assert out.family_votes_short == 0
    assert out.execution_authorized is False
