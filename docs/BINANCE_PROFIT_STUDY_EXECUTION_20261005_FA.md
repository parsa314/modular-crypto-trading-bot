# اجرای پروتکل Binance — ۵ اکتبر ۲۰۲۶

هدف شفاف: net geometric monthly return حداقل 2%، هدف توسعه‌ای 3%، MaxDD کمتر
از 15% و daily annualized Sharpe حداقل 1.5. سرمایه 10,000 USDT **شبیه‌سازی‌شده**،
Binance Spot BTC/USDT 1h، leverage=1، risk/trade=1%، daily=2% و operational DD halt=10%.
این اعداد هدف ارزیابی هستند؛ بازده تضمین‌شده یا سرمایهٔ واقعی حساب نیستند.

سه فاز خودکار کدنویسی و تست شده‌اند: official ZIP/checksum/Parquet، feature causal
و Logistic/Platt با 29 fold تقویمی 6/1/1 و purge/24h embargo، حسابداری next-open
با fee/slippage و cash/buy-hold/stress. capital بین foldهای OOS reset نمی‌شود.

اجرای دادهٔ واقعی 36 آرشیو ماهانه 2022–2024 را دریافت و CHECKSUM را تأیید کرد.
انتظار 26,304 کندل بود؛ 26,303 وجود دارد. در `2023-03-24T13:00Z` یک ساعت مفقود
است و کندل ساعت 12:00 همان روز زودتر تمام شده است. raw حفظ شده؛ imputed=0.
quality و اجرای pipeline بنابراین **BLOCKED_DATA_QUALITY_OR_INTEGRITY** هستند؛
metrics=NOT_EVALUATED و هیچ مدل مالی روی دادهٔ اصلاح‌نشده آموزش داده نشد.
این نتیجه، اثبات زیان مدل یا رد هدف سود نیست؛ نقص کیفیت منبع است.

```bash
python -m pip install -e '.[dev,data]'
python -m pytest -q tests/test_spot_history.py tests/test_profit_features.py \
  tests/test_profit_model.py tests/test_profit_backtest.py tests/test_profit_pipeline.py
python -m research_bot.research.profit_pipeline freeze --output artifacts/profit-v1-new
python -m research_bot.research.spot_history --start 2022-01-01T00:00:00Z \
  --end 2025-01-01T00:00:00Z --output artifacts/profit-history-new
python -m research_bot.research.profit_pipeline run --history artifacts/profit-history-new \
  --output artifacts/profit-v1-new
```

quality-block در این داده exit 2 است و گزارش حفظ می‌شود. هیچ report قبلی
بازنویسی نمی‌شود؛ pipeline تغییر source/config بعد از freeze را رد می‌کند.
75 تست موفق شامل prefix proof، gaps، label availability، stop گپ، شمارش هزینه،
daily/DD latch، سرمایهٔ اولیه در MaxDD، null ratio و جلوگیری از آموزش روی دادهٔ blocked است.

[رکورد واقعی، کیفیت، hash آرشیوها و freeze](../evidence/binance_profit_v1/report.json)
برای فصل سه و چهار قابل استفاده‌اند. مطالعهٔ مستقل Native V59 روی پنج دارایی
سال 2024، پروتکل و هویت دیگری دارد؛ نتایج آن جایگزین این شکست کیفیت نیستند.
هیچ کلید API، سفارش خصوصی، Paper یا LIVE فعال نشده است.
