# PIT triple-barrier label availability — research-only correction

Parent research head: `c40190733eef091ac4f8a00f21a651aba4c315af` / PR #148.
Source context: public preview of López de Prado, *Advances in Financial Machine
Learning*, §4.2, cited as conceptual background, NOT as directly prescribing
this particular implementation. The pathwise max-availability rule is an
engineering deduction: a path-dependent label consumes each preceding bar,
therefore its earliest legal availability is `max(available_at of consumed bars)`.

Fix: keep existing barrier outcomes and chronology, but report the maximum
availability of all consumed bars for TP, SL, ambiguous two-barrier events and
horizon TIMEOUT. Do not use future bars or overwrite historical evidence.

Synthetic tests cover late first bar, normal case, ambiguous event, timeout,
and a fail-closed as-of before the initial bar arrives.

Verification: run targeted research tests and full relevant regression on the
exact branch SHA. No real OHLCV/holdout or economic performance was tested;
scientific and paper/live execution gates remain BLOCKED.
