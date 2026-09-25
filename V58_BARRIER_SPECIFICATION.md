# V58 Barrier Specification

Primary: next-bar open; stop `1.00 × ATR14_t`; target `1.50 × ATR14_t`; 12 bars including entry. ATR is frozen at event time. Same-bar ambiguity is `STOP_FIRST`. Timeout remains a distinct class. Premature data termination is `RIGHT_CENSORED`.

Sensitivity-only contracts: A=`0.75/1.50/8`, B=`1.00/2.00/12`, C=`1.25/2.50/18` (stop ATR / target ATR / bars). They cannot determine promotion.

Gross return and R are computed before costs. Round-trip 0/24/36/50 bps are applied once; 24 bps is primary. Labels never change due to costs.
