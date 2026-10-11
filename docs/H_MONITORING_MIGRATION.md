# H. Read-only monitoring, migration and rollback

The existing `research_bot/service.py` FastAPI service is extended with **GET `/research/course`** and 15 course evidence panels in **GET `/dashboard`**. No second web service, Gradio dependency, control endpoint, background worker or exchange client is introduced. Existing health, scientific status and blocked decision/paper endpoints retain their governance contracts.

`research_bot/course/monitoring.py` reads only the explicit operator environment path `COURSE_RESEARCH_REPORT`; when unset, it reports `NOT_RUN`. Set it to a versioned report created by the separate offline research runner. The service does not create, modify, train, restart or execute anything when this path is configured. Remove the variable to disconnect the course report immediately.

## Input contract

The selected JSON artifact must have `schema=COURSE_RESEARCH_REPORT_V1`, an aware ISO-8601 `generated_at`, and strict boolean `execution_flags={research_only:true,paper_execution:false,live_execution:false}`. A true/incorrect execution flag yields `BLOCKED`. Missing file, invalid JSON/schema/timestamp, duplicate keys, oversized input, NaN or malformed strategy list yields `ERROR`. Raw exception text, file paths and parsed fragments are withheld.

Artifact reads are bounded to 4 MiB. Reports aged at least 24 hours are explicitly `STALE`; a timestamp more than five seconds in the future is invalid. Artifact age **does not certify market-data freshness**. The corresponding market freshness panel stays `NOT_EVALUATED` without explicitly supplied source evidence.

Optional fields are `status`, `scientific_status`, `data_class`, a `strategies` list with known course `strategy_id` and accepted status labels, numeric `metrics`, and an explicitly named `panels` mapping. Public output uses `schema=COURSE_RESEARCH_MONITORING_V1`. Unknown text labels become `NOT_EVALUATED`; unknown keys are dropped. Strategy count is a count of public registry rows, not trade events.

## Public projection and safety

The endpoint publishes a bounded whitelist of aggregate statuses/counts/metrics. It excludes raw hypotheses, models, candidate metadata, credentials, tokens, account identities, balances, private positions, raw ledger rows and source file paths. Numeric fields must be finite real numbers, cannot be booleans and are bounded in magnitude. This is an artifact projection and cannot authenticate scientific claims independently; the report author remains responsible for truthful evidence and metric applicability.

The dashboard refreshes these read-only endpoints every 30 seconds. All artifact-derived output is rendered through DOM `textContent`, never HTML interpolation or `innerHTML`. Missing fields remain `NOT_EVALUATED`; unrun calibration/forward/economic tests do not become passing panels. Simulation is explicitly labeled hypothetical. Neither report success nor dashboard refresh changes execution authorization.

Panels cover system report health, market data freshness, hypotheses, strategies, regime, order book, portfolio risk, risk decisions, simulated orders, grid state, arbitrage, experiment registry, calibration, evidence and scientific validation. The initial page and JSON distinguish missing evidence from a completed test. A panel's existence does not establish an implemented or validated financial capability.

## Changed files and compatibility

| File | Change |
|---|---|
| `research_bot/service.py` | Add GET projection; extend existing HTML dashboard |
| `research_bot/course/monitoring.py` | Pure read-only bounded report parser and public whitelist |
| `tests/test_course_monitoring.py` | Report failures, flags, staleness, disclosure/XSS and endpoint firewall regression |

No new package dependency, execution flag, risk threshold, scientific status record or persistence schema is required. Installing optional dashboard libraries is unnecessary. The code is compatible with the pinned main service interface and the existing course report runner; it does not import unmerged #110/#111 journals.

## Verification boundary

Focused tests exercised the actual in-process ASGI routes, existing health/dashboard, locked POST endpoints, malformed/duplicate/oversized reports, sensitive-field filtering, XSS-safe rendering, artifact staleness and strict false execution flags. The first sandboxed TestClient call hung even on the unchanged baseline `/health`; faulthandler located the wait in AnyIO's cross-thread portal. The identical offline tests passed when run outside that sandbox. This environmental distinction is recorded rather than presented as a code failure or live-provider proof.

Reproduce with the project's development dependencies:

```bash
python -m pytest -q tests/test_course_monitoring.py tests/test_service_v1.py tests/test_v08_service.py
python -m mypy --follow-imports=skip research_bot/course/monitoring.py
python -m ruff check research_bot/course/monitoring.py tests/test_course_monitoring.py research_bot/service.py
```

The monitoring verification log is `../evidence/monitoring_verification.json`. Full integration/regression and scientific evaluation remain separate evidence gates owned by the final delivery report.

## Rollback

Unset `COURSE_RESEARCH_REPORT` to return to `NOT_RUN` without changing stored artifacts. For source rollback, remove the added GET route/import and course dashboard section, and remove the new projection module/test. Leave existing service firewall, risk policy, historical records and append-only evidence intact. Do not delete negative reports or reinterpret engineering tests as authorization.
