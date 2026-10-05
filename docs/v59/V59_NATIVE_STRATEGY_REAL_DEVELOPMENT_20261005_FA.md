# V59 — موتور بومی و آزمایش واقعیِ توسعه

**نتیجه: DEVELOPMENT_EVALUATED_NO_MODEL_PROMOTED.**
این آزمایش مستقل ۲۰۲۴ برای تکمیل مهندسی V59 است؛ جایگزین مطالعهٔ سه‌سالهٔ
Logistic، ارزیابی نهایی یا Holdout آینده نیست. مدل یا خدمت LIVE فعال نشده است.

## تغییرات پیاده‌سازی

ده تعریف Confluence از V58 به‌صورت انتخابی منتقل شدند؛ V58 و نتایجش تغییر نکردند.
موتور native هیچ import از V58 ندارد. تبدیل cloud به **ابر قابل مشاهده با
displacement=26** صریح است؛ هر دو span پیش از استفاده باید موجود باشند.
شناسهٔ strategy version جدید `V59_NATIVE_CAUSAL_1` مانع یکی‌گرفتن این آزمایش
با نسخهٔ projected-cloud قبلی می‌شود. آستانه‌های ده predicate retune نشده‌اند.

در لحظهٔ close فقط OHLCV بسته‌شده و featureهای causal در دسترس‌اند. entry reference
همان close معلوم است؛ fill آینده به feature یا SignalCandidate تزریق نمی‌شود.
چون V59 شرط `decision_at < entry_time` دارد، اجرای مبتنی بر bar-open یک کندل کامل
پس از decision close برنامه‌ریزی می‌شود. هیچ timestamp یک میکروثانیه‌ای جعلی
برای دورزدن این شرط کم نمی‌شود. این فرض اجرای محافظه‌کارانهٔ پژوهش است، نه اندازه‌گیری latency.

Event ID، feature snapshot و زنجیرهٔ hash گذشته با append دادهٔ آینده ثابت می‌مانند.
`decision_at`, `entry_time`, `information_start/end`, `strategy_arm`, `regime`,
data/code hashes و snapshot id ذخیره می‌شوند. برچسب‌سازی آینده در ماژول جداست؛
RIGHT_CENSORED و ENTRY_OUTSIDE_BARRIERS حفظ و از fit حذف می‌شوند. SL/TP هم‌زمان
STOP_FIRST هستند؛ gap-stop از open نامطلوب پر می‌شود. مدل نمی‌تواند سایز یا order بسازد.
Regime هنوز `UNKNOWN/0` است و gate پیش‌فرض finance آن را veto می‌کند؛ probability
ساختگی برای بازکردن آن وارد نشده است.

در tournament واقعی، **information_end + 24h embargo** باید پیش از شروع
validation/test باشد. نمادهای هم‌زمان یک گروه غیرقابل تقسیم‌اند. test window
هم‌پوشان رد می‌شود. scaler فقط train و isotonic فقط validation را می‌بینند.
TIMEOUT loss در expected utility یک فرض ex-ante است؛ realized timeout return
تنها پس از admission برای scoring خوانده می‌شود. columns outcome وارد features نمی‌شوند.

## شواهد واقعی

داده: Binance Spot، 1h، از 2024-01-01 تا 2025-01-01 exclusive، پنج دارایی.
60 ZIP رسمی با CHECKSUM؛ هر دارایی 8,784 کندل معتبر، مجموع **43,920**.
دادهٔ Parquet به raw ZIP بازخوانی‌شده تطبیق داده شد؛ gap/imputation/clipping صفر.
پروتکل و hash کد قبل از outcome ثبت شدند؛ seed=59 و cost=0/24/36/50bps ثابت‌اند.

**17,788 رویداد** از 11 خانواده ثبت شد. فقط C10_09 و Ichimoku تعداد کافی
unique decision clock برای foldهای ثبت‌شده داشتند. برای هرکدام سه مدل PRIOR،
LOGISTIC و HIST_GRADIENT_BOOSTING، 12 fold و چهار cost scenario اجرا شد: **24 trial**.
نه خانوادهٔ کم‌نمونه حذف‌شده از تاریخچه نیستند؛ count آن‌ها در verification ثبت است.

| خانواده، هزینه 24bps | مدل | OOS events | admitted | coverage | mean net / همهٔ OOS events | Brier |
|---|---|---:|---:|---:|---:|---:|
| C10_09 | PRIOR | 1,337 | 0 | 0% | 0% | 0.56564 |
| C10_09 | LOGISTIC | 1,337 | 143 | 10.70% | +0.01261% | 0.60044 |
| C10_09 | HIST_GRADIENT_BOOSTING | 1,337 | 191 | 14.29% | +0.03520% | 0.59743 |
| Ichimoku | PRIOR | 2,734 | 0 | 0% | 0% | 0.57594 |
| Ichimoku | LOGISTIC | 2,734 | 437 | 15.98% | −0.05334% | 0.64487 |
| Ichimoku | HIST_GRADIENT_BOOSTING | 2,734 | 562 | 20.56% | −0.05571% | 0.63928 |

میانگین جدول به events غیرپذیرفته‌شده بازده صفر می‌دهد. این اعداد **Sharpe، بازده
ماهانه یا بازده یک حساب واقعی نیستند**. eventهای هم‌پوشان و چند دارایی هنوز
به یک ledger سرمایه تبدیل نشده‌اند. هزینه‌ها scenario هستند، نه execution calibration.
C10_09 یک candidate برای آزمایش بعدی است، نه Alpha تأییدشده: admitted کمتر از
200 است، Brier از prior ضعیف‌تر است، داده تنها یک سال است، multiple-testing
inference و prospective evidence این آزمایش انجام نشده‌اند. هیچ انتخاب پسینی
به‌عنوان مدل Promote شده ثبت نشد. مقدار `promotion_review_eligible` در tournament
فقط پشتیبانی event برای بررسی مستقل است؛ summary مجوز آن review را نیز false نگه می‌دارد.

## بازتولید و تست

```bash
python -m pip install -e '.[dev,data]'
python -m pytest -q tests/v59 tests/v58
python -m research_bot.v59.native_study freeze --output artifacts/native-reproduction
for symbol in BTCUSDT ETHUSDT SOLUSDT XRPUSDT DOGEUSDT; do
  python -m research_bot.research.spot_history --symbol "$symbol" \
    --start 2024-01-01T00:00:00Z --end 2025-01-01T00:00:00Z \
    --output "artifacts/native-history/$symbol"
done
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python -m research_bot.v59.native_study run \
  --output artifacts/native-reproduction --inputs \
  artifacts/native-history/BTCUSDT artifacts/native-history/ETHUSDT \
  artifacts/native-history/SOLUSDT artifacts/native-history/XRPUSDT artifacts/native-history/DOGEUSDT
```

تأیید نهایی کل مخزن: **613 passed / 1 PostgreSQL integration skip / 12 subtests**.
تأیید متمرکز V58/V59 برابر 421 passed و 12 subtests؛ 29 تست archive نیز موفق.
برای replay باید source و package versions همان registration حفظ شوند؛ تغییر
کد بعد از freeze به trial تازه نیاز دارد. full events CSV در artifact محلی است؛
[summary، همهٔ 24 trial و hashهای بازتولید](../../evidence/v59_native_2024/verification.json)
در مخزن نگه‌داری می‌شوند. این مطالعه هیچ دادهٔ CoinEx holdout را دریافت نکرد.

## دامنهٔ باقی‌مانده و مبنای علمی

FVG/ICT/TSI چندتایم‌فریمی در مسیر موجود V58 حفظ شده؛ این patch ادعای انتقال آن
به native V59 ندارد. ریسک/portfolio ledger، execution calibration واقعی،
ارزیابی چندرژیمی و Forward هنوز برای promotion لازم‌اند؛ RL/DL با این جدول
به‌طور خودکار مجاز نمی‌شوند.

[Han, Huang & Wang (ICML 2024)](https://proceedings.mlr.press/v235/han24b.html)
دلیل به‌کارگیری زمان و shift در assessment/selection را روشن می‌کند؛ نتیجهٔ
خود این پروژه باید از artifact واقعی به دست آید. روش‌های purging/embargo و
baseline comparison در protocol پروژه حفظ شده‌اند. شرط causal و تفکیک
outcome از admission با تست‌های prefix، availability و interval اثبات مهندسی دارند؛
از یک مقاله یا تعداد فایل‌ها سودآوری استنتاج نمی‌کنیم.

این بخش به فصل سه (PIT، هویت event و معماری دو کلید) و فصل چهار (نتایج مثبت
محدود، منفی و کمبود پشتیبانی) وارد می‌شود؛ عددهای جدول مستقیم از all_trials.csv هستند.
