"""Inspect a draft protocol; never starts market data or trading."""
from __future__ import annotations

import argparse
import json
from .adapters import nautilus_availability
from .protocol import StudyProtocol, gap_matrix


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha',required=True)
    parser.add_argument('--study-id',default='SAFER_TRADE_DRAFT_001')
    args=parser.parse_args()
    protocol=StudyProtocol(args.study_id,args.source_sha)
    print(json.dumps({'readiness':protocol.readiness(),'gaps':gap_matrix(),
                      'nautilus':nautilus_availability()},indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
