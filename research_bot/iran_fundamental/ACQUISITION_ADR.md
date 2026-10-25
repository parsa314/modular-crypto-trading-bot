# Official-source acquisition contract 1.0.0

Evidence state: ENGINEERING. Parent: 8fea393e92d008eb72e4c2a0284f602aae59bb5f.

This increment implements bounded official HTTPS retrieval and immutable raw-byte
storage. It does not certify issuer identity, publication time, statement scope,
revision completeness or extracted financial values. Retrieved bytes are not
automatically VERIFIED. The existing uploaded-evidence report remains unchanged.

Only exact allowlisted Codal/TSETMC hosts are accepted. Credentials, fragments,
nonstandard ports and redirects are rejected. TLS/proxy configuration is inherited.
Responses are bounded to 4 MB and a 15-second socket timeout. HTTP failures and
transport failures are recorded as BLOCKED, never scientific rejection. Successful
retrieval is RAW_CAPTURED_UNVERIFIED. UTC retrieval timestamps are actual capture
times, never historical availability dates. SHA-256 identifies raw bytes, not text
after normalization. SQLite transactions preserve body and metadata together;
reusing a request key with a different URL fails. An identical retry returns the
original receipt without making another network request. A new key explicitly
requests a fresh observation, including after an infrastructure failure.

Acceptance: independent digest oracle, byte-preserving persistent replay, rejected
URLs, bounded responses, and recorded transport failure. A real filing, independent
manual extraction oracle and issuer/revision mapping are still required before
this acquisition boundary can support an official financial report. No trading,
email, v0.51 policy, cost model or frozen research definition changes.
