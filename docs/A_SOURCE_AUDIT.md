# A. Source Audit Report — Sajad Jamalian course notebooks

Audit date: 2026-10-10 UTC. Evidence type: **static source engineering evidence**. Status: **STATIC_SOURCE_VERIFIED / RESEARCH_CANDIDATE**. No notebook cell was executed. No API authentication, order, notebook server, browser session, or course live loop was started.

The supplied six notebooks are the attribution boundary. Their filenames and content support the specifications below; they do not independently verify authorship, course marketing claims, profitability, or production readiness. Features added by the integration specification are labeled **ENGINEERING_EXTENSION**. Cell indices throughout this report are zero-based; function line numbers are local to the cell.

## A.1 Source inventory and reproducibility

| Notebook | SHA-256 | Cells / code cells | Function definitions | Classification |
|---|---|---:|---:|---|
| `Python_1.ipynb` | `b87ff4c0420701d4dc494d998ad70511888edd10b427e480916986b08448a344` | 249 / 230 | 9 | `REUSE_AS_CONCEPT` |
| `Python_2_Mall_Customer.ipynb` | `6c735f48b407ae9e100d80db37f0edc80b5a792aa39ad4365f63f92f19cd15bb` | 68 / 67 | 1 | `REUSE_AS_CONCEPT` |
| `Python_3.ipynb` | `1b0f0002c25af7e14bf695b6aca5f80160407d02f876c473d5e6d7ce9e9b0c33` | 19 / 19 | 0 | `REWRITE_REQUIRED` |
| `Python_4Connect_to_Bitpin.ipynb` | `bc3a9fed89543123e5ee2943e8d8c8896e2436812c90edfef80c87f5b190d6ad` | 66 / 47 | 19 | `REWRITE_REQUIRED` |
| `Python_5_Trade_Strategy.ipynb` | `082b19d5bccc5eda3084eac30aae9fd12589130686b0ef10c3a9044672e8192b` | 21 / 12 | 26 | `REWRITE_REQUIRED` |
| `Python_6_Build_Bot.ipynb` | `dc2fab1506425e1d184d8bdf4bbb050fbabb40110861e0d24b500631f722cd5b` | 25 / 15 | 30 | `REWRITE_REQUIRED` |

Total: **448 cells, 390 code cells, 85 function definition occurrences**. Repeated definitions are retained as separate occurrences because they overwrite behavior in notebook execution order. The earlier and latest uploads of notebooks 2, 3, 4 and 5 are byte-identical. Notebook 1 is taken from the earlier upload; notebook 6 from the latest upload.

Machine-readable inventory: `evidence/source_audit.json`. Rebuild using `python evidence/audit_notebooks_static.py` in this workspace. The script reads notebook JSON and parses Python AST, hashes cell source and existing outputs, and saves only inventories and redacted findings. Existing output payloads and authentication values are not copied. Empty code cells remain in the inventory. Non-Python/syntax cells are flagged rather than executed.

## A.2 Notebook purpose, dependencies and migration

### `Python_1.ipynb`

Python types, control flow, functions/lambda, text handling, regular expressions, NumPy array operations, datetimes, pandas Series/DataFrames.

Dependency inventory: `datetime`, `numpy`, `pandas`, `re`.

General programming concepts only. Do not migrate tutorial examples, random data, or broad exception patterns into strategy/risk code.

Existing output cells: 147. Limited JWT/credential-field pattern scan flagged 0 output cells and 0 nonempty hardcoded credential assignments. These zero counts do **not** establish a secret-free certificate: indirect secrets, opaque values and external `config_bitpin` are outside the scan. External credential configuration and referenced datasets were not supplied.

| Function occurrence | Cell : line | Parameters | Migration |
|---|---|---|---|
| `fun` | 67 : 1 | `x` | `REUSE_AS_CONCEPT` |
| `fun` | 70 : 1 | `x` | `REUSE_AS_CONCEPT` |
| `func1` | 74 : 1 | `pv`, `per`, `r` | `REUSE_AS_CONCEPT` |
| `func3` | 81 : 1 | `a`, `b` | `REUSE_AS_CONCEPT` |
| `fun` | 84 : 1 | `x` | `REUSE_AS_CONCEPT` |
| `emailsplit` | 95 : 1 | `email` | `REUSE_AS_CONCEPT` |
| `countbtc` | 98 : 1 | `sen` | `REUSE_AS_CONCEPT` |
| `div` | 106 : 1 | `a`, `b` | `REUSE_AS_CONCEPT` |
| `clean` | 121 : 2 | `string` | `REUSE_AS_CONCEPT` |

Cells statically containing top-level network/authentication, remote download, live/order calls, server startup or file writes: none detected by the documented classifier. Detection of a potential side effect does not assert that it was executed successfully.

### `Python_2_Mall_Customer.ipynb`

Mall customer tabular import/export, missing/duplicate checks, descriptive statistics, grouping, charts, categorization, quantile capping and dummy encoding.

Dependency inventory: `datetime`, `matplotlib`, `numpy`, `pandas`, `seaborn`.

Retain only the general data quality and train-only transformation concepts. Customer categorization is unrelated to market prediction and is not a trading ML model. No fitted financial predictive model is evidenced.

Existing output cells: 46. Limited JWT/credential-field pattern scan flagged 0 output cells and 0 nonempty hardcoded credential assignments. These zero counts do **not** establish a secret-free certificate: indirect secrets, opaque values and external `config_bitpin` are outside the scan. External credential configuration and referenced datasets were not supplied.

| Function occurrence | Cell : line | Parameters | Migration |
|---|---|---|---|
| `categorize_customer` | 52 : 1 | `row` | `REUSE_AS_CONCEPT` |

Cells statically containing top-level network/authentication, remote download, live/order calls, server startup or file writes: 3, 4. Detection of a potential side effect does not assert that it was executed successfully.

### `Python_3.ipynb`

HTTP request/response and POST demonstrations.

Dependency inventory: `requests`.

Reconstruct request handling only through audited ports; no trading endpoint, authenticated account connection or reusable exchange capability can be inferred from generic HTTP examples.

Existing output cells: 7. Limited JWT/credential-field pattern scan flagged 0 output cells and 0 nonempty hardcoded credential assignments. These zero counts do **not** establish a secret-free certificate: indirect secrets, opaque values and external `config_bitpin` are outside the scan. External credential configuration and referenced datasets were not supplied.

No named function definitions; the notebook uses top-level cells. These cells are inventoried individually in the JSON artifact.

Cells statically containing top-level network/authentication, remote download, live/order calls, server startup or file writes: 1, 5, 8, 11. Detection of a potential side effect does not assert that it was executed successfully.

### `Python_4Connect_to_Bitpin.ipynb`

Bitpin authentication/public market data/account reads/order requests and a simple looping bot.

Dependency inventory: `config_bitpin`, `pandas`, `requests`, `time`.

All direct order and authentication paths rejected from research runtime. Public schemas/endpoints are course examples, requiring current official verification before adapter activation.

Existing output cells: 21. Limited JWT/credential-field pattern scan flagged 0 output cells and 0 nonempty hardcoded credential assignments. These zero counts do **not** establish a secret-free certificate: indirect secrets, opaque values and external `config_bitpin` are outside the scan. External credential configuration and referenced datasets were not supplied.

| Function occurrence | Cell : line | Parameters | Migration |
|---|---|---|---|
| `login` | 3 : 1 | none | `REWRITE_REQUIRED` |
| `get_valid_header` | 3 : 27 | none | `REWRITE_REQUIRED` |
| `fetch_data` | 6 : 1 | `endpoint` | `REWRITE_REQUIRED` |
| `get_all_prices` | 8 : 3 | none | `REWRITE_REQUIRED` |
| `get_orderbook` | 11 : 1 | `symbol` | `REWRITE_REQUIRED` |
| `price_monitor` | 14 : 3 | `symbol`, `target_price`, `check_interval` | `REUSE_AS_CONCEPT` |
| `get_my_balance` | 19 : 1 | none | `REWRITE_REQUIRED` |
| `get_deposit_address` | 23 : 1 | `currency_code`, `network_id` | `REWRITE_REQUIRED` |
| `get_all_networks` | 26 : 1 | none | `REWRITE_REQUIRED` |
| `place_limit_order` | 30 : 1 | `symbol`, `side`, `price`, `amount` | `SECURITY_REJECTED` |
| `place_market_order` | 33 : 1 | `symbol`, `side`, `amount` | `SECURITY_REJECTED` |
| `place_stop_limit_order` | 37 : 1 | `symbol`, `side`, `stop_price`, `price`, `amount` | `SECURITY_REJECTED` |
| `get_active_orders` | 40 : 1 | `symbol` | `REWRITE_REQUIRED` |
| `get_order_history` | 43 : 1 | `symbol`, `limit` | `REWRITE_REQUIRED` |
| `cancel_order` | 47 : 1 | `order_id` | `SECURITY_REJECTED` |
| `get_deposit_history` | 50 : 1 | `currency`, `limit` | `REWRITE_REQUIRED` |
| `get_withdraws_history` | 53 : 1 | `currency`, `limit` | `REWRITE_REQUIRED` |
| `place_bulk_orders` | 56 : 1 | `symbol`, `orderlist` | `SECURITY_REJECTED` |
| `run_simple_bot` | 63 : 9 | none | `SECURITY_REJECTED` |

Cells statically containing top-level network/authentication, remote download, live/order calls, server startup or file writes: 3, 20, 24, 28, 31, 34, 38, 41, 44, 46, 48, 51, 54, 58, 64. Detection of a potential side effect does not assert that it was executed successfully.

### `Python_5_Trade_Strategy.ipynb`

Duplicates Bitpin helper layer and presents six OHLC technical strategy backtests downloaded through Binance Client.

Dependency inventory: `binance`, `config_bitpin`, `matplotlib`, `pandas`, `requests`, `time`.

Reuse mathematical hypotheses; rewrite causal timing, immutable identities, costs, execution simulation, risk integration and validation. Course output is not accepted as scientific evidence.

Existing output cells: 7. Limited JWT/credential-field pattern scan flagged 0 output cells and 0 nonempty hardcoded credential assignments. These zero counts do **not** establish a secret-free certificate: indirect secrets, opaque values and external `config_bitpin` are outside the scan. External credential configuration and referenced datasets were not supplied.

| Function occurrence | Cell : line | Parameters | Migration |
|---|---|---|---|
| `login` | 1 : 17 | none | `REWRITE_REQUIRED` |
| `get_valid_header` | 1 : 43 | none | `REWRITE_REQUIRED` |
| `fetch_data` | 1 : 62 | `endpoint` | `REWRITE_REQUIRED` |
| `get_all_prices` | 1 : 102 | none | `REWRITE_REQUIRED` |
| `get_orderbook` | 1 : 112 | `symbol` | `REWRITE_REQUIRED` |
| `price_monitor` | 1 : 123 | `symbol`, `target_price`, `check_interval` | `REUSE_AS_CONCEPT` |
| `get_my_balance` | 1 : 154 | none | `REWRITE_REQUIRED` |
| `get_deposit_address` | 1 : 174 | `currency_code`, `network_id` | `REWRITE_REQUIRED` |
| `get_all_networks` | 1 : 190 | none | `REWRITE_REQUIRED` |
| `place_limit_order` | 1 : 213 | `symbol`, `side`, `price`, `amount` | `SECURITY_REJECTED` |
| `place_market_order` | 1 : 258 | `symbol`, `side`, `amount` | `SECURITY_REJECTED` |
| `place_stop_limit_order` | 1 : 304 | `symbol`, `side`, `stop_price`, `price`, `amount` | `SECURITY_REJECTED` |
| `get_active_orders` | 1 : 353 | `symbol` | `REWRITE_REQUIRED` |
| `get_order_history` | 1 : 382 | `symbol`, `limit` | `REWRITE_REQUIRED` |
| `cancel_order` | 1 : 411 | `order_id` | `SECURITY_REJECTED` |
| `get_deposit_history` | 1 : 433 | `currency`, `limit` | `REWRITE_REQUIRED` |
| `get_withdraws_history` | 1 : 457 | `currency`, `limit` | `REWRITE_REQUIRED` |
| `place_bulk_orders` | 1 : 482 | `symbol`, `orderlist` | `SECURITY_REJECTED` |
| `calculate_sma` | 4 : 24 | `price`, `window` | `REUSE_AS_CONCEPT` |
| `calculate_sma` | 6 : 28 | `price`, `window` | `REUSE_AS_CONCEPT` |
| `bollinger_band` | 6 : 56 | `prices`, `window`, `num_std_dev` | `REUSE_AS_CONCEPT` |
| `calculate_rsi` | 9 : 27 | `prices`, `window` | `REUSE_AS_CONCEPT` |
| `calculate_donchian` | 11 : 27 | `df`, `window` | `REUSE_AS_CONCEPT` |
| `calculate_macd` | 13 : 27 | `prices`, `fast`, `slow`, `signal_window` | `REUSE_AS_CONCEPT` |
| `calculate_macd` | 16 : 29 | `prices`, `fast`, `slow`, `signal_window` | `REUSE_AS_CONCEPT` |
| `calculate_rsi` | 16 : 42 | `prices`, `window` | `REUSE_AS_CONCEPT` |

Cells statically containing top-level network/authentication, remote download, live/order calls, server startup or file writes: 1, 4, 6, 9, 11, 13, 16. Detection of a potential side effect does not assert that it was executed successfully.

### `Python_6_Build_Bot.ipynb`

Duplicates Bitpin helpers and implements five bot styles plus a Gradio trading dashboard.

Dependency inventory: `config_bitpin`, `gradio`, `numpy`, `pandas`, `requests`, `time`.

Reuse concepts only. Reconstruct state machines and read-only monitoring; reject direct order callbacks and all live loops.

Existing output cells: 7. Limited JWT/credential-field pattern scan flagged 0 output cells and 0 nonempty hardcoded credential assignments. These zero counts do **not** establish a secret-free certificate: indirect secrets, opaque values and external `config_bitpin` are outside the scan. External credential configuration and referenced datasets were not supplied.

| Function occurrence | Cell : line | Parameters | Migration |
|---|---|---|---|
| `login` | 1 : 17 | none | `REWRITE_REQUIRED` |
| `get_valid_header` | 1 : 43 | none | `REWRITE_REQUIRED` |
| `fetch_data` | 1 : 62 | `endpoint` | `REWRITE_REQUIRED` |
| `get_all_prices` | 1 : 102 | none | `REWRITE_REQUIRED` |
| `get_orderbook` | 1 : 114 | `symbol`, `limit` | `REWRITE_REQUIRED` |
| `price_monitor` | 1 : 139 | `symbol`, `target_price`, `check_interval` | `REUSE_AS_CONCEPT` |
| `get_my_balance` | 1 : 170 | none | `REWRITE_REQUIRED` |
| `get_deposit_address` | 1 : 190 | `currency_code`, `network_id` | `REWRITE_REQUIRED` |
| `get_all_networks` | 1 : 206 | none | `REWRITE_REQUIRED` |
| `place_limit_order` | 1 : 229 | `symbol`, `side`, `price`, `amount` | `SECURITY_REJECTED` |
| `place_market_order` | 1 : 274 | `symbol`, `side`, `amount` | `SECURITY_REJECTED` |
| `place_stop_limit_order` | 1 : 320 | `symbol`, `side`, `stop_price`, `price`, `amount` | `SECURITY_REJECTED` |
| `get_active_orders` | 1 : 369 | `symbol` | `REWRITE_REQUIRED` |
| `get_order_history` | 1 : 398 | `symbol`, `limit` | `REWRITE_REQUIRED` |
| `cancel_order` | 1 : 427 | `order_id` | `SECURITY_REJECTED` |
| `get_deposit_history` | 1 : 449 | `currency`, `limit` | `REWRITE_REQUIRED` |
| `get_withdraws_history` | 1 : 473 | `currency`, `limit` | `REWRITE_REQUIRED` |
| `place_bulk_orders` | 1 : 498 | `symbol`, `orderlist` | `SECURITY_REJECTED` |
| `simple_scalper_bot` | 4 : 1 | `TARGET_SYMBOL`, `PROFIT_MARGIN`, `TRADE_AMOUNT`, `BUY_THRESHOLD` | `SECURITY_REJECTED` |
| `market_sentiment` | 8 : 1 | `symbol` | `REUSE_AS_CONCEPT` |
| `sentiment_bot` | 8 : 23 | `symbol`, `traed_amount`, `profit_pct`, `stop_loss`, `imalance_threshold` | `SECURITY_REJECTED` |
| `calculate_rsi` | 11 : 3 | `prices`, `window` | `REUSE_AS_CONCEPT` |
| `rsi_ma_bot` | 11 : 29 | none | `SECURITY_REJECTED` |
| `calculate_grid` | 14 : 7 | `current_price` | `REUSE_AS_CONCEPT` |
| `grid_bot` | 14 : 32 | none | `SECURITY_REJECTED` |
| `check_arbitrage` | 18 : 5 | none | `REUSE_AS_CONCEPT` |
| `triangle_arbitrage_bot` | 18 : 38 | none | `SECURITY_REJECTED` |
| `calculate_rsi` | 22 : 5 | `prices`, `window` | `REUSE_AS_CONCEPT` |
| `stop_bot` | 22 : 22 | none | `REUSE_AS_CONCEPT` |
| `start_bot` | 22 : 26 | `symbol`, `trade_amount`, `profit_pct`, `rsi_period`, `ma_period`, `rsi_oversold` | `SECURITY_REJECTED` |

Cells statically containing top-level network/authentication, remote download, live/order calls, server startup or file writes: 1, 5, 9, 12, 15, 19, 22. Detection of a potential side effect does not assert that it was executed successfully.

## A.3 Exact algorithm specifications and source variants

All styles begin as **RESEARCH_CANDIDATE**. No calibrated confidence, expected edge estimator, uncertainty estimate, regime fit model or statistical acceptance threshold is present in these strategy examples. Educational parameter values are reference values, never optimal values.

### 1. Moving Average Crossover

Source: `Python_5_Trade_Strategy.ipynb`, cell 4; functions: `calculate_sma`.

Formula and variant: SMA_n(t)=sum(C[t-n+1:t])/n; actual fast=50 slow=150 despite SMA20/SMA50 labels.

Entry: Long if SMA50(t-1)<SMA150(t-1) and SMA50(t)>SMA150(t).

Exit: TP5%, SL2%, or inverse crossover; close-price only educational fill assumption.

### 2. Bollinger Mean Reversion

Source: `Python_5_Trade_Strategy.ipynb`, cell 6; functions: `calculate_sma`, `bollinger_band`.

Formula and variant: 20-close sample standard deviation ddof=1; bands SMA20 +/- 2*sigma20.

Entry: Long when C(t)<lower(t).

Exit: C(t)>=SMA20(t), TP15%, SL5%.

### 3. RSI Overbought/Oversold

Source: `Python_5_Trade_Strategy.ipynb`, cell 9; functions: `calculate_rsi`.

Formula and variant: delta.diff; gain=max(delta,0), loss=max(-delta,0); EWM alpha=1/14 adjust=True with default initial-series behavior; RSI=100-100/(1+gainEWM/lossEWM).

Entry: Long previousRSI<=20/currentRSI>20; short previousRSI>=80/currentRSI<80.

Exit: Opposite RSI zone, TP20%, SL7%; synthetic short accounting.

### 4. Donchian / Range Breakout

Source: `Python_5_Trade_Strategy.ipynb`, cell 11; functions: `calculate_donchian`.

Formula and variant: Upper=max(last30 High), lower=min(last30 Low), mid=(upper+lower)/2.

Entry: Long C(t)>upper(t-1); short C(t)<lower(t-1).

Exit: Current mid crossing, TP5%, SL2%.

### 5. MACD Signal Crossover

Source: `Python_5_Trade_Strategy.ipynb`, cell 13; functions: `calculate_macd`.

Formula and variant: Adjusted EWM12(C)-adjusted EWM26(C), adjusted EWM9 signal.

Entry: Long MACD-signal <=0 to >0 crossover; short >=0 to <0.

Exit: Opposite crossover, TP5%, SL2%.

### 6. RSI + MACD + EMA Confluence

Source: `Python_5_Trade_Strategy.ipynb`, cell 16; functions: `calculate_macd`, `calculate_rsi`.

Formula and variant: MACD fast/slow EWM12/26 adjust=False, signal9 adjust=True; RSI14 adjust=True; EMA50 adjust=False.

Entry: MACD up-cross AND RSI>50 AND C>EMA50 long; down-cross AND RSI<50 AND C<EMA50 short.

Exit: Opposite crossover, TP7%, SL3%.

### 7. Simple Scalper

Source: `Python_6_Build_Bot.ipynb`, cell 4; functions: `simple_scalper_bot`.

Formula and variant: Fixed absolute-price threshold, not statistical scalping model.

Entry: BTC_USDT ticker<=5000; amount0.001, no existing active order; sends immediate sell request after buy accepted.

Exit: Sell target=ticker*(1+0.01); no proven fill lifecycle or stop.

Reconstruction boundary: ENGINEERING_EXTENSION.

### 8. Order Book Imbalance Bot

Source: `Python_6_Build_Bot.ipynb`, cell 8; functions: `market_sentiment`, `sentiment_bot`.

Formula and variant: I=(sum(top5 bid amounts)-sum(top5 ask amounts))/(sum(top5 bid amounts)+sum(top5 ask amounts)).

Entry: Long I>supplied imbalance_threshold; negative threshold only waits.

Exit: Immediate sell request at ticker*(1+profit_pct); stop_loss parameter unused.

### 9. RSI + Moving Average Bot

Source: `Python_6_Build_Bot.ipynb`, cell 11; functions: `calculate_rsi`, `rsi_ma_bot`.

Formula and variant: Simple means of gains/losses of last14 ticker differences; RSI insufficient=50, down=0 =>100 including flat; MA mean of last20 polling prices.

Entry: Ticker>MA20 AND RSI<30; amount0.0001.

Exit: Immediate sell request at ticker*(1+0.006).

Reconstruction boundary: Closed 4h candle input instead of polling ticks is ENGINEERING_EXTENSION, not literal source behavior.

### 10. Grid Trading Bot

Source: `Python_6_Build_Bot.ipynb`, cell 14; functions: `calculate_grid`, `grid_bot`.

Formula and variant: P_i=74000+i*(78000-74000)/4, i=0..4; buy below ticker, sell above; amount0.0001 each.

Entry: Submit multi-order plan with no range-state or inventory-reservation validation.

Exit: If ID absent from active list assumed fill, replenish opposite side adjacent step.

Reconstruction boundary: ENGINEERING_EXTENSION.

### 11. Triangular Arbitrage Bot

Source: `Python_6_Build_Bot.ipynb`, cell 18; functions: `check_arbitrage`, `triangle_arbitrage_bot`.

Formula and variant: Final IRT=(10000000/ask(USDT_IRT))*(1-.004)/ask(BTC_USDT)*(1-.004)*bid(BTC_IRT)*(1-.004).

Entry: Top-price theoretical edge>50000 IRT; sequential market requests roundedUSDT2/BTC5 decimals.

Exit: Three accepted requests labeled profit secured; actual fills, depth, fee currency, residuals not reconciled.

Reconstruction boundary: ENGINEERING_EXTENSION.

The OHLC notebook uses pandas rolling and EWM defaults. Rolling windows require their full window; an SMA50/150 crossover requires at least 151 valid close observations to compare successive complete values. Bollinger requires 20 closes and sample standard deviation (`ddof=1`). Donchian needs 30 historical high/low observations plus the decision bar, and deliberately compares to the **previous** channel. EWM/RSI examples do not impose a full explicit stabilization warm-up; their first values depend on pandas initialization and initial zero/NaN treatment. A reconstructed warm-up, Wilder RSI seed, EMA `adjust=False` policy or flat-RSI convention is a preregistered **ENGINEERING_EXTENSION**, unless reproducing the exact named educational variant.

The RSI variants differ: notebook 5 uses adjusted EWM gain/loss; notebook 6 averages gains/losses over the last `window` ticker differences (a simple-window/Cutler-like calculation). Neither should silently be described as a standard Wilder-seeded RSI. Notebook 6 returns RSI=100 when loss=0, including an entirely flat window, and returns RSI=50 for insufficient input. MACD in cell 13 is fully adjusted EWM; confluence cell 16 combines recursive fast/slow EWM with adjusted signal EWM. These are distinct algorithms.

The strategy backtests use same-close entry/exit prices, commission 0.001 per leg, almost all available capital, and close-only TP/SL observations. They do not establish attainable fills at that close, intrabar barrier order, spread, latency, market impact, short borrow/funding availability, minimum notional, liquidity capacity or turnover-adjusted robustness. Earliest execution must be modeled after feature availability; a next-open or order-book execution assumption belongs to the reconstruction protocol. No timeout or evidence-linked invalidation scheme is evidenced in the source.

## A.4 Security and correctness findings

Findings below are source-supported static defects or missing contracts. Runtime manifestation and economic impact have not been reproduced by executing the course cells.

| Finding | Source location | Evidence / consequence | Classification |
|---|---|---|---|
| `SRC-SEC-001` CRITICAL | Python_3.ipynb, Python_4Connect_to_Bitpin.ipynb, Python_5_Trade_Strategy.ipynb, Python_6_Build_Bot.ipynb | Executable top-level HTTP/authentication/read/order/live-loop cells; not safe to import/run wholesale | `SECURITY_REJECTED` |
| `SRC-SEC-002` HIGH | Python_4Connect_to_Bitpin.ipynb, Python_5_Trade_Strategy.ipynb, Python_6_Build_Bot.ipynb | Requests calls omit timeout; fixed unbounded recursive HTTP429 retry, no circuit breaker; get_active_orders returns empty on unavailable response | `REWRITE_REQUIRED` |
| `SRC-SEC-003` HIGH | Python_4Connect_to_Bitpin.ipynb, Python_5_Trade_Strategy.ipynb, Python_6_Build_Bot.ipynb | External config_bitpin credentials not supplied; globaltoken expiry hardcoded900 seconds; refresh token assigned but no refresh workflow; failedlogin not checked before header generation | `REWRITE_REQUIRED` |
| `SRC-COR-001` MEDIUM | Python_5_Trade_Strategy.ipynb cell 4 | Column names SMA20/SMA50 actually use50/150 windows; coursefaithful reconstruction must expose exact variant | `REWRITE_REQUIRED` |
| `SRC-COR-002` HIGH | Python_5_Trade_Strategy.ipynb cells 4,6,9,11,13,16 | Signals use current finalized close and fills assumed at thatsameclose; no timeofavailability/next executable price/latency/spread model. Entry uses nearlyallcapital not calibrated pertrade risk. Short instrumentborrowfunding/marginconstraints omitted | `REWRITE_REQUIRED` |
| `SRC-COR-003` HIGH | Python_6_Build_Bot.ipynb cell 11 | Stray bare name a after orders can raise NameError; live RSI variant differs from notebook5 and gives100 onflat; polling prices not closedcandles | `REWRITE_REQUIRED` |
| `SRC-COR-004` CRITICAL | Python_6_Build_Bot.ipynb cells 4,8,11,22 | Sell leg submitted onbuyorderacceptance without confirmedfill/reservedinventory; activeorderreadfailure may be treated asnone | `REWRITE_REQUIRED` |
| `SRC-COR-005` CRITICAL | Python_6_Build_Bot.ipynb cell 14 | Uses actice_grid collection but calls undefined active_grid.remove; absence fromactiveorders conflatesfilled,cancelled,rejected,expired,dataunavailable; reservation estimate usescurrentprice instead ofsum(levelprice*quantity); no inventorycheck/partialfill/rangedetection | `REWRITE_REQUIRED` |
| `SRC-COR-006` CRITICAL | Python_6_Build_Bot.ipynb cell 18 | Topprice-only triangular estimate ignoresdepth/skew/precision/minnotional/latency/depletion; subsequentlegs useassumedamounts; wrong securedprofitclaim on requestacceptance; no failedlegunwind | `REWRITE_REQUIRED` |
| `SRC-GOV-001` CRITICAL | Python_6_Build_Bot.ipynb cell 22 | Gradio start callback directly executes order functions; UI controls are not scientificapproval or independentexecutionauthorization | `SECURITY_REJECTED` |
| `SRC-SCI-001` INFO | Python_1.ipynb, Python_2_Mall_Customer.ipynb | General programming, descriptive dataanalysis/customer segmentation examples; no financialpredictivevalidation evidenced. Customer labeling/scaling not marketmodel evidence | `REUSE_AS_CONCEPT` |
| `SRC-SCI-002` HIGH | Python_1.ipynb, Python_2_Mall_Customer.ipynb, Python_3.ipynb, Python_4Connect_to_Bitpin.ipynb, Python_5_Trade_Strategy.ipynb, Python_6_Build_Bot.ipynb | No CPCV/PBO/DSR, meta-labeling, calibration, trainonlyfitting, heldoutpromotion, causal timestampledger or risksovereignty implementation evidenced in code; additions are ENGINEERING_EXTENSION | `UNSUPPORTED_BY_EVIDENCE` |

Bitpin course URLs and authentication payloads are source extraction facts only. They do not establish the exchange’s current official API, current support for candles/server time/stop-limit/bulk/idempotency, precision or currency units. In particular, an internal `event_id` does not establish exchange idempotency. Unsupported capabilities must fail explicitly; network outages must not become empty-order-list success.

## A.5 Course-to-architecture traceability matrix

This matrix states intended compatible destinations. The repository compatibility report must verify canonical class/file existence and the implementation report must record the actual added paths before marking any row integrated. A mapping is not implementation evidence.

| Extracted source functions or cells | Reconstructed component | Canonical boundary | Status / attribution |
|---|---|---|---|
| Python1: `fun`, `func1`, `func3`, `emailsplit`, `countbtc`, `div`, `clean` | General programming concepts; no trading module | No product migration | REUSE_AS_CONCEPT |
| Python2: `categorize_customer`, descriptive/table/plot cells | Data-quality discipline, train-only fit principle | Existing dataset/feature pipeline; no customer data import | REUSE_AS_CONCEPT; train-only enforcement ENGINEERING_EXTENSION |
| Python3: HTTP cells | Structured timeout/schema errors | ResearchDataPort / read-only transport | REWRITE_REQUIRED |
| `login`, `get_valid_header` | Credential/session contract, if officially documented | Authenticated read port isolated from research strategy | SECURITY_REJECTED for direct copying |
| `fetch_data`, `get_all_prices`, `get_orderbook`, `get_all_networks`, `price_monitor` | Public schema validation, normalized public snapshots | ResearchDataPort / Bitpin read-only adapter | REWRITE_REQUIRED; current endpoint verification required |
| `get_my_balance`, `get_deposit_address`, `get_active_orders`, `get_order_history`, `get_deposit_history`, `get_withdraws_history` | Explicit capabilities and authenticated-read interfaces | No authenticated network in default research run | REWRITE_REQUIRED / unsupported until documentation verified |
| `place_limit_order`, `place_market_order`, `place_stop_limit_order`, `cancel_order`, `place_bulk_orders` | Simulator interface only | NoOrderTransport and deterministic simulator; existing RiskGate remains sovereign | Direct functions SECURITY_REJECTED; simulator ENGINEERING_EXTENSION |
| `calculate_sma`, `bollinger_band`, notebook5 `calculate_rsi`, `calculate_donchian`, `calculate_macd` | Six frozen technical hypothesis generators | Map immutable candidate to existing SignalEvidence, then ResearchTradingOrchestrator | REWRITE_REQUIRED; source mathematical variants recorded |
| `run_simple_bot`, `simple_scalper_bot` | Threshold hypothesis only; no immediate sell | Same hypothesis interface; simulator plans behind canonical risk | REWRITE_REQUIRED; lifecycle ENGINEERING_EXTENSION |
| `market_sentiment`, `sentiment_bot` | Validated depth imbalance hypothesis | Data quality/availability then same SignalEvidence boundary | REWRITE_REQUIRED; sequence/time validation ENGINEERING_EXTENSION |
| notebook6 `calculate_rsi`, `rsi_ma_bot` | Simple-window RSI+MA hypothesis with explicit ticker/candle variant | Same candidate and decision interface | REWRITE_REQUIRED; closed-candle conversion ENGINEERING_EXTENSION |
| `calculate_grid`, `grid_bot` | Inventory/capital-reserved grid plan and state machine | Existing portfolio/risk; deterministic research simulator | REWRITE_REQUIRED; full lifecycle ENGINEERING_EXTENSION |
| `check_arbitrage`, `triangle_arbitrage_bot` | Executable-depth conversion path hypothesis and non-atomic failure assessment | Existing portfolio/risk; simulator only | REWRITE_REQUIRED; depth/fees/failed-leg recovery ENGINEERING_EXTENSION |
| `stop_bot`, `start_bot`, Gradio blocks | Read-only research monitoring and explicit research run control | Existing monitoring if compatible; no execution permission | Direct live-order UI SECURITY_REJECTED; monitor ENGINEERING_EXTENSION |

All extracted named functions are covered above; duplicate helper/indicator definitions remain listed individually in A.2. Strategy logic implemented directly in notebook loops is attributed by exact cell, even when no strategy function name exists.

## A.6 Missing tests and scientific boundaries

- Point-in-time availability andclosedbarcausality.
- Indicator warmup,nan,flatRSI,EMAadjustvariants.
- No directorder path orRiskGatebypass.
- APItimeout,rate-limit,failed-auth,tokenexpiry,schema.
- Persistentintent/orderstate/idempotency/recovery.
- Partialfill andinventory/capitalconservation.
- Gridreserve/rebalance/rangefailure.
- Arbitragebidask,fees,depth,residual,failedleg.
- Netcost-aware chronological heldoutvalidation.

CPCV, PBO, DSR, average uniqueness, meta-labeling, confidence calibration, CSM-DAP, portfolio CVaR/shrinkage, event provenance, checkpoint recovery, immutable authorization and cognitive architecture are **ENGINEERING_EXTENSION**. These capabilities are not attributed to the supplied course. No future-aware training, benchmark selection or predictive profitability claim is supported by tutorial outputs.

The audit establishes source identities and inspectable specifications, not economic effectiveness. Historical order-book data, timestamped first-seen snapshots and actual fills were not supplied. Consequently order-book/grid/arbitrage forward evidence, real exchange compatibility and economic promotion remain **NOT_EVIDENCE_VERIFIED**. No live or paper execution permission follows from this report.

## Implemented traceability supplement (2026-10-10)

The following are file mappings, not claims of economic validity. `strategies.py` emits immutable candidates, `labels.py` replays conservative next-open exits for the six technical rules, and `integration.py` adapts to the existing SignalEvidence/DecisionEngine/RiskEngine. No course order function is migrated into an order transport.

| Audited source | Actual integration target | Boundary |
|---|---|---|
| Python_1 basic Python | Static inventory only | No relevant trading implementation migrated |
| Python_2 Mall Customer | Static inventory only | Customer clustering is not market-regime evidence |
| Python_3 Python/data/ML examples | Evaluation design concepts | Train-only preprocessing and statistical validation are ENGINEERING_EXTENSION |
| Python_4 Bitpin wrappers | `research_bot/course/bitpin.py` | Official-documentation-verified public GET paths only; private/order functions rejected from runtime |
| Python_5 six technical rules | `research_bot/course/strategies.py`, `labels.py` | SMA actual 50/150 retained; EMA/RSI variants documented; no same-close execution |
| Python_6 scalper, imbalance, RSI/MA | `strategies.py`, `microstructure.py` | Candle proxies explicitly extensions; imbalance requires valid timed/sequenced book |
| Python_6 Grid | `stateful.py`, `simulation.py` | Inventory/reservation/state machine is ENGINEERING_EXTENSION |
| Python_6 triangle | `stateful.py`, `simulation.py` | Depth, fees, actual-filled next leg and failure risk are ENGINEERING_EXTENSION |
| Python_6 Gradio | Existing `research_bot/service.py`, `course/monitoring.py` | Read-only existing dashboard extended; no Gradio runtime/control duplication |

Notebook function-by-function source locations and classifications remain in `evidence/source_audit.json` in the delivery bundle. None of the six notebooks was executed.
