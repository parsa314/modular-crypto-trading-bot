# ADR-COMP-009 — Verified demo boundary and causal HPT data repair

Status: accepted for offline engineering PAPER; date: 2026-10-11.

**Problem.** The uploaded demo's contracts/schema differ from the research
repository; execution bypasses gates and its HPT target leaks into features.

**Options.** (A) Replace canonical contracts with demo classes; (B) patch the
demo's immediate-fill main loop; (C) compose the reviewed simulator with an
explicit demo journal projection and a bounded replacement entry point.

**Evidence for/against.** Real fixtures show four existing REAL tables and a
different HypothesisCandidate. A and B would inherit ambiguous fee units,
unfunded sales, gate bypass and unsafe neural defaults. C can preserve original
rows and prove atomic projection/restart, but is not a drop-in copy of old
strategy economics. The existing simulator already has depth, latency and
residual-inventory tests.

**Chosen design.** C. Keep canonical contracts frozen. Legacy forecasts are
uncalibrated metadata, never automatically sized plans. Add explicit synthetic
admission with original demo limits; charge approved impact in fills, enforce
the unchanged total slippage cap and reject REDUCE/multi-leg shortcuts. Preserve
exact events as authority and project single-quote views into existing tables.
HPT remains disabled; a pure NumPy helper repairs forward labels and produces
purged expanding train/validation/test cohorts for future training work.

**Trade-offs.** Lossy REAL compatibility requires exact sidecar events. Existing
demo history cannot supply exact restart balances. Resting-order depth is a
displayed liquidity proxy, not executable depth. All-cash default Grid can
abstain; no threshold is lowered to manufacture trades. Neural inference and
multi-leg demo execution wait for separate scientific/admission protocols.

**Impact.** Optional runner admission port; extra Decimal impact field in paper
requests/fills; explicit execution policy revision invalidates old certificates.
No changes to frozen v0.51, the canonical DecisionEngine API, service execution
firewall, live adapters or risk ceilings. Existing general multi-leg simulations
remain available independently.

**Acceptance.** Uploaded fixtures stay unchanged; old rows survive; projection
and cursor roll back together; funded synthetic paths charge impact and log
partial/final fills; new-namespace restart matches cold replay; no risk weakening,
quantity redirection or real transport; feature perturbation proves future
prices cannot affect past model inputs; every completion module exceeds 80%
statement coverage. Model training/graduation are explicitly NOT_RUN.

**Rollback.** Revert this independent integration commit and use the prior
reviewed implementation/new namespace. Preserve existing scientific and exact
execution records. Do not migrate new-policy certificates backward or interpret
legacy REAL projections as authoritative capital.
