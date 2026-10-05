"""Audit the new temporal/identity contract on pinned 2024 development data.

Offline only: re-verifies official cached ZIPs; no model fit or economic score.
Future CoinEx/Kraken holdouts are never inputs to this command.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pandas as pd
from research_bot.v59.native_study import SYMBOLS, END, load_verified_binance_bundle
from research_bot.v59.native_strategy import NativeStrategyConfig, MODEL_FEATURES
from research_bot.v59.native_dataset import build_native_events
from research_bot.v59.tournament import _validate_events, _validate_temporal_contract


def run(root, output):
    output = Path(output);output.mkdir(parents=True, exist_ok=False)
    audits, all_events = [], []
    for symbol in SYMBOLS:
        frame, manifest = load_verified_binance_bundle(Path(root)/symbol)
        events, audit = build_native_events(frame, venue='binance', symbol=symbol.replace('USDT','/USDT'),
            data_version=manifest['dataset_sha256'], as_of=END, config=NativeStrategyConfig(timeframe='1h'))
        validated = _validate_temporal_contract(_validate_events(events, MODEL_FEATURES))
        assert validated.event_id.is_unique and validated.event_identity_hash.is_unique
        audits.append({'symbol': symbol, 'bars': len(frame), 'events': len(events),
                       'data_hash': manifest['dataset_sha256'], 'dataset_hash': audit['dataset_hash']})
        all_events.append(events)
    events = pd.concat(all_events, ignore_index=True)
    assert events.event_id.is_unique
    events.to_csv(output/'events.csv', index=False)
    sources = ['research_bot/v59/native_strategy.py', 'research_bot/v59/native_dataset.py',
               'research_bot/v59/tournament.py', 'scripts/audit_convergence_native_events.py']
    report = {'status': 'PASS', 'protocol': 'CONVERGENCE_EVENT_TIME_AND_IDENTITY_V3',
        'registered_identity': 'NATIVE_EVENT_ID_V2', 'bars': sum(a['bars'] for a in audits),
        'events': len(events), 'assets': audits, 'global_event_uniqueness': True,
        'temporal_contract_validated': True, 'economic_evaluation_performed': False,
        'label_available_at_policy': 'HISTORICAL_CLOSED_BAR_ASSUMPTION_NOT_OBSERVED_PROSPECTIVELY',
        'old_economic_results_recomputed': False, 'coinex_holdout_consumed': False, 'kraken_holdout_consumed': False,
        'execution_authorized': False, 'paper_execution': False,
        'base_commit': subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        'source_hashes': {p: sha256(Path(p).read_bytes()).hexdigest() for p in sources},
        'created_at': datetime.now(timezone.utc).isoformat()}
    (output/'report.json').write_text(json.dumps(report, sort_keys=True, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-root', type=Path, required=True);parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({k:v for k,v in run(args.input_root,args.output).items() if k in {'status','bars','events'}}))
