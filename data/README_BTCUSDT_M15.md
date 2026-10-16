# BTCUSDT M15 (2022–2024) — SOURCE EVIDENCE, NOT SYNTHETIC
A data file is not valid until 36 official Binance Vision monthly checksums, exact 105216 15-minute bars, byte SHA256, date bounds and continuous UTC timestamps are verified. CI must fail closed on one missing month or source error, and must not substitute Yahoo BTC-USD daily bars for BTCUSDT M15.

Dataset source: https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/15m/
Dataset usage license: CC BY-NC-SA 4.0, Binance Vision terms https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md — *academic noncommercial research only*, not eligible as a licensed commercial live-trading feed. Attribution and share-alike apply to distributed derivatives. Publication/storage may be subject to additional dataset terms.

Expected exact artifact: `data/btc_usdt_m15_2022_2024.parquet` and companion manifest. If absent, fetching failed; never replace with invented rows or interpolate missing bars.
