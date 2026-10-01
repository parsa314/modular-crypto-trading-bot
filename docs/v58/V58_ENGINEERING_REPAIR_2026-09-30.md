# V58 evidence integrity repair — 2026-09-30

Engineering continuation from PR #89, immutable base
`455014d2b146165b7edc13cf7bc9c61489447948`.
This is a scoped repair of the executable V58 research path. The previously
recorded negative development findings remain historical evidence.

## Reproduced defects and fixes

| Defect | Repair | Regression evidence |
| --- | --- | --- |
| A synthetic DataFrame plus a claimed SHA/status was accepted as real market evidence | Intake now parses original CSV bytes and matches venue, asset, digest, bounds, row count and schema to the ten reviewed development sources | Unknown bytes, claimed digests and DataFrames rejected; a test-only trust fixture exercises the valid parser path |
| Existing results were overwritten before validation | Validate and serialize the full run, stage it beside the destination, then publish the directory atomically; existing runs must be byte-identical | Conflicting/partial output, mixed classification, staging failure and second-dataset failure |
| Changing input identity did not change the replay hash | Evidence schema 2 binds input digest, source-file fingerprint, timeframe, costs, barrier policy, provenance, records and ledger head | Zero-event input/cost changes alter the hash; modified records cannot export |
| An hourly pipeline used a four-hour outcome clock | Pass the parsed bar duration to the entry/outcome contract; frozen market archives remain H4 | Hourly synthetic path resolves within the stated horizon |
| Mutating caller payloads changed ledger history | Detached finite JSON snapshots on append and read; integrity check before export | Caller, returned-entry and entries-view mutation; invalid JSON; corrupt chain |
| Invalid costs passed when no events were generated | Validate costs before candidate generation | Negative, NaN and infinite costs rejected for zero-event input |

The registry in `research_bot/v58/development_sources.py` is copied from the
reviewed `evidence/v58_core_repair/V58_RECOVERED_DATA_MANIFEST.json`; a regression
test checks exact agreement. Callers cannot substitute their own trust store.
The source archives were previously inspected development data; matching bytes
does not independently authenticate an exchange or make them pristine holdouts.

## Run

Install the repository's declared development dependencies in your environment:

```bash
python -m pip --timeout 15 --retries 1 install -e '.[dev]'
python -m pytest -q tests/v58
python -m research_bot.v58 synthetic-demo --output results/v58-new-demo
```

Rerunning the exact same command and source is idempotent. After changing code,
configuration or input, choose a new output directory. Existing evidence is
intentionally never replaced. The legacy `synthetic_events.jsonl` filename is
retained for compatibility. Use a named output directory, not the current
working directory (`--output .`); replacing it would detach the caller's cwd.
Classification is explicit in every record and the
run manifest, including zero-event runs.

For exact registered archives recovered from the original workflow artifacts:

```bash
python -m research_bot.v58 verified-replay --venue coinex --archive /path/to/coinex-source.zip --output results/v58-coinex-new
python -m research_bot.v58 verified-replay --venue binance --archive /path/to/binance-source.zip --output results/v58-binance-new
```

The CLI obtains expected archive digests and source run/artifact identifiers
from the frozen registry. Wrong archives fail before any result is published.
An archive's five datasets and summary are published as one complete directory.
The two venue commands use separate output directories.

## Verification and environment limitation

Fresh detailed results and dependency identities are recorded in
`evidence/v58_integrity_repair/verification.json`.
Regression tests failed on the original implementation before the fixes.
The completed integrated V58 suite passes 284 tests. After network access was
restored, the complete local repository suite passed 447 tests with one database
skip and one dependency deprecation warning; no test files were excluded.
The connected five-asset AI CLI is replayed twice with identical output;
see the integrated runbook for its scope. Compilation and whitespace checks pass.

GitHub Actions also passed the complete 447-test repository suite, the 284-test
V58 suite and both integrated synthetic replays on implementation commit
`7d3d6237af632c649fa65c8bb9e7c72e4a2890f2`. Run and downloadable artifact
identities are recorded in `evidence/v58_integrity_repair/ci_verification.json`.

The execution sandbox could not connect to its configured proxy, so ordinary
clone/pip operations were unavailable. Repository files and the official
pytest/pluggy/iniconfig sources were retrieved using the connected GitHub API.
Only their build-generated version metadata was supplied locally; their test
runner code was unchanged. Those temporary dependencies are outside the project
and are not vendored into this repository.

The initial bounded run passed 436 tests with one skip while excluding two
ccxt-dependent files and two service files that hung in the sandbox's
AnyIO/Starlette TestClient portal. After the execution environment gained network
access, official ccxt and psycopg packages were installed into a separate local
test dependency directory. The full suite then completed, including both service
files. The initial exclusions are retained as historical diagnostics in
verification.json; full_repository.txt records the successful complete run.

When pytest is unavailable but NumPy and Pandas are installed, the new focused
regressions also run with the standard library:

```bash
python -m unittest discover -s tests/v58 -p '*unittest.py' -v
python -m unittest discover -s tests/v58 -p test_ledger_integrity.py -v
```

This fallback covers the new regressions, not the complete pre-existing suite.
Do not disable proxies, fabricate dependencies or equate an environment import
failure with a strategy failure. Normal CI still installs `.[dev]` and executes
the complete pytest suite.

## Scope and remaining evidence

No new real-market replay, prospective collection, empirical model training or
market economic validation was performed. The raw registered archives were not
available locally. The later [integrated engineering build](V58_INTEGRATED_AI_ENGINEERING_2026-10-01.md)
connects synthetic-only model fitting, shared-capital simulation and cost stress.
This does not establish profitable alpha or authorize market model selection.
Real data/split/holdout and promotion gates remain blocked. PAPER and LIVE
execution remain false and Kraken remains sealed.

Directory publication protects against partial visible writes during a process
failure. It is not a claim of distributed transaction or power-loss durability
on every filesystem. A hard-killed writer may leave an unpublished staging
directory beside its output.

## راهنمای کوتاه فارسی

خطاهای اعتبارسنجی داده، بازنویسی نتایج، ثبت منشأ داده، تایم‌فریم و دفتر شواهد
اصلاح شده‌اند. دستور `synthetic-demo` برای آزمون نرم‌افزار است. دستور
`verified-replay` فقط آرشیوهای مشخص و ثبت‌شده پروژه را می‌پذیرد. برای هر نسخه
جدید، مسیر خروجی تازه انتخاب کنید. این اصلاحات به معنی اثبات سودآوری یا فعال
شدن معامله با پول واقعی نیست.
