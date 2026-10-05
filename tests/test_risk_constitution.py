from dataclasses import FrozenInstanceError, replace
import math

import pytest

from research_bot.execution.constitution import RISK_CONSTITUTION_V1 as R, RiskConstitution
from research_bot.execution.config import GovernanceConfig, LiveConfig
from research_bot.v59.config import FinancialConstitution


def test_one_risk_policy_research_governance_and_rl():
    from research_bot.execution.rl_policy import TradingEnvConfig
    assert R.drawdown_kill == .05
    assert FinancialConstitution().drawdown_kill == R.drawdown_kill
    assert GovernanceConfig().max_drawdown_fraction == R.drawdown_kill
    assert TradingEnvConfig().max_drawdown == R.drawdown_kill
    assert TradingEnvConfig().max_cvar == R.max_cvar95
    assert TradingEnvConfig().risk_constitution_hash == GovernanceConfig().risk_constitution_hash == R.sha256
    assert len(R.sha256) == 64
    with pytest.raises(FrozenInstanceError):
        R.drawdown_kill = .10


@pytest.mark.parametrize('field', ['risk_per_trade', 'max_asset_weight', 'drawdown_kill', 'max_daily_loss', 'max_cvar95'])
@pytest.mark.parametrize('value', [.90, float('nan'), float('inf'), True])
def test_policy_cannot_be_overridden(field, value):
    with pytest.raises(ValueError):
        replace(R, **{field: value})


def test_former_ten_and_twelve_percent_overrides_are_rejected():
    from research_bot.execution.rl_policy import TradingEnvConfig
    with pytest.raises(ValueError):
        GovernanceConfig(max_drawdown_fraction=.10)
    with pytest.raises(ValueError):
        TradingEnvConfig(max_drawdown=.12)
    assert FinancialConstitution(drawdown_kill=.04).sha256 != R.sha256
    with pytest.raises(ValueError):
        LiveConfig('binance', 'BTC/USDT', '1h', 100, 20, .02, .10)


@pytest.mark.parametrize('field', ['risk_per_trade', 'max_asset_weight', 'max_gross_exposure', 'max_daily_loss',
                                 'drawdown_warning', 'drawdown_kill', 'max_cvar95', 'max_turnover_per_step', 'min_cash_buffer'])
def test_every_effective_limit_is_hash_bound(field):
    value = getattr(R, field)
    changed = replace(R, **{field: value*1.1 if field == 'min_cash_buffer' else value*.9})
    assert changed.sha256 != R.sha256
    assert RiskConstitution.from_json(changed.canonical_json()).sha256 == changed.sha256


@pytest.mark.parametrize('value', ['{"drawdown_kill":0.05,"drawdown_kill":0.04}', '{"unknown_override":0.9}',
                                 '{"drawdown_kill":NaN}', '{"drawdown_kill":Infinity}', '{"drawdown_kill":5.0}'])
def test_invalid_json_cannot_override_risk(value):
    with pytest.raises(ValueError):
        RiskConstitution.from_json(value)
