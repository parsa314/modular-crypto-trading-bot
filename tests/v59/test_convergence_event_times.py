from dataclasses import replace

import pandas as pd
import pytest

from research_bot.v59.fixtures import tournament_fixture_events
from research_bot.v59.tournament import TournamentConfig, run_tournament

FEATURES = ('f_trend', 'f_vol', 'f_structure', 'f_memory')


def timed_events():
    x = tournament_fixture_events()
    x['decision_at'] = x.timestamp
    x['entry_time'] = x.timestamp + pd.Timedelta(seconds=1)
    x['information_start'] = x.timestamp
    x['event_end_time'] = x.timestamp + pd.Timedelta(seconds=20)
    x['information_end'] = x.event_end_time
    x['label_available_at'] = x.event_end_time
    x['feature_available_at'] = x.timestamp
    for name in ('feature_snapshot_id', 'data_version', 'strategy_version', 'source_hash'):
        x[name] = 'ENGINEERING_FIXTURE_'+name
    return x


def test_duplicate_identity_at_different_clocks_is_rejected():
    x = tournament_fixture_events()
    x = x[x.strategy_id == x.strategy_id.iloc[0]].reset_index(drop=True)
    x.loc[1, 'event_id'] = x.loc[0, 'event_id']
    with pytest.raises(ValueError, match='duplicate event'):
        run_tournament(x, feature_columns=FEATURES, config=TournamentConfig(models=('PRIOR',)))


@pytest.mark.parametrize('evidence_class', ['REAL_MARKET_EVENT_DATA', 'REAL_OOS_DEVELOPMENT', 'FORWARD'])
def test_every_nonfixture_class_requires_complete_times(evidence_class):
    x = tournament_fixture_events()
    x['information_end'] = x.timestamp + pd.Timedelta(seconds=20)
    with pytest.raises(ValueError, match='interval|temporal'):
        run_tournament(x, feature_columns=FEATURES,
                       config=TournamentConfig(models=('PRIOR',), evidence_class=evidence_class))


def test_late_label_publication_is_purged_before_calibration():
    x = timed_events()
    # Outcome occurred early but the label did not become available until 2030.
    x.loc[x.index[:50], 'label_available_at'] = pd.Timestamp('2030-01-01T00:00:00Z')
    r = run_tournament(x, feature_columns=FEATURES,
                       config=TournamentConfig(models=('PRIOR',), evidence_class='REAL_MARKET_EVENT_DATA'))
    for fold in r['attempted_folds']:
        if fold.get('status'):
            continue
        assert pd.Timestamp(fold['train_max_label_available_at']) < pd.Timestamp(fold['validation_information_start']) - pd.Timedelta(hours=24)


@pytest.mark.parametrize('field', ['entry_time', 'event_end_time', 'label_available_at', 'information_start'])
def test_invalid_temporal_order_is_rejected(field):
    x = timed_events()
    x.loc[0, field] = x.loc[0, 'decision_at'] - pd.Timedelta(days=1) if field != 'information_start' else x.loc[0, 'entry_time']
    with pytest.raises(ValueError, match='temporal'):
        run_tournament(x, feature_columns=FEATURES,
                       config=TournamentConfig(models=('PRIOR',), evidence_class='REAL_MARKET_EVENT_DATA'))
