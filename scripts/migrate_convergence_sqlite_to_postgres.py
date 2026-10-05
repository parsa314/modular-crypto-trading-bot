"""Explicit offline engineering migration; never discovers production DSNs."""
import argparse
import json
import os

from research_bot.execution.postgres_ledger import PostgresLedger
from research_bot.execution.postgres_migration import migrate_sqlite, sqlite_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--journal-id', required=True)
    parser.add_argument('--writers-stopped', action='store_true', required=True)
    args = parser.parse_args(argv)
    dsn = os.environ.get('CONVERGENCE_TEST_DATABASE_URL', '')
    if not dsn:
        parser.error('CONVERGENCE_TEST_DATABASE_URL is required; DATABASE_URL is never used')
    with sqlite_snapshot(args.source) as snapshot:
        identity = snapshot['identity']
    with PostgresLedger(dsn, args.journal_id, identity) as target:
        print(json.dumps(migrate_sqlite(args.source, target), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
