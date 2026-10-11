# بررسی و اصلاح اجرایی پکیج V3 جمالیان

تاریخ: 2026-10-11. وضعیت: **Phase‑1 مهندسی، بدون تأیید اقتصادی**.
این سند معماری مسیر فعال جمالیان را جایگزین توصیف قبلی ادغام می‌کند؛
گزارش قبلی همچنان ردیابی نوت‌بوک‌ها و محدودیت‌های شبیه‌ساز را نگه می‌دارد.

## آنچه واقعاً تحویل شده بود

SHA-256 فایل ZIP ارسالی:
`6eac39025eca8b9989eb42ede326c4046d11cd7e26ad7018afd17d24e9d5811b`.
آرشیو فقط چهار فایل داشت: گزارش، قرارداد، Baselineها و داور. پوشه‌های
`tests/`، `adr/`، `evidence/` و `exchanges/` خالی بودند. Adapter، Event Ledger،
تست پذیرش ADR و خروجی ValidationHarness در بسته نبودند. امتیاز ۷۸/۱۰۰ در
متن کاربر نقل شده است؛ ارزیابی مستقل یا دادهٔ پشتیبان آن دریافت نشده است.

## یافته‌ها و اصلاحات

| اهمیت | یافتهٔ کد V3 ارسالی | اصلاح قابل بررسی |
|---|---|---|
| بحرانی | `research_bot.contracts.hypothesis` از `contracts.py` موجود قابل import نیست. | قرارداد مستقل `research_bot/hypothesis.py`؛ قراردادهای فعلی تغییر نکردند. |
| بحرانی | RSI/RSI+MA بازده ثابت و confidence قاعده‌ای دارند؛ Adapter ساده می‌تواند بدون شواهد اقتصادی BUY_CANDIDATE بسازد. | حذف تخمین‌های دلخواه از HypothesisCandidate؛ Adapter فعلاً confidence=0، forecast unavailable و NO_TRADE تولید می‌کند. |
| بحرانی | داور تنها پیش از حلقه affordability را بررسی می‌کند؛ پذیرش تجمیعی می‌تواند از سرمایهٔ باقی‌مانده بیشتر شود. | کنترل Decimal سرمایه در هر پذیرش و بازگرداندن state رزرو‌شده. |
| بالا | ظرفیت موقعیت پیش از رتبه‌بندی و براساس ترتیب ورودی مصرف می‌شود؛ `CAPITAL_WEIGHTED` نیز پیاده‌سازی نشده است. | سیاست صریح و واحد EARLIEST_FIRST سپس ID؛ رتبه‌بندی قبل از فیلتر و نتیجهٔ مستقل از ترتیب ورودی. |
| بالا | dataclass frozen، دیکشنری‌های تودرتو را immutable نمی‌کند؛ ID پارامتر و provenance را در بر ندارد و تابع UUID هم موجود است. | freeze عمیق، نسخهٔ detached برای export و hash قطعیِ رخداد/پارامتر/شواهد/قیمت‌های ورودی. |
| بالا | تاریخچهٔ قیمت بدون timestamp رشد می‌کند؛ stamp هنگام generate ساخته می‌شود؛ زمان اجرا +۱ ثانیه به‌تنهایی نشت Close همان کندل را رفع نمی‌کند. | کندل بسته‌شدهٔ دارای observed/available واقعی، warmup، ترتیب منظم، تازگی و زمان صریح؛ bound بعد از تصمیم، بدون ادعای اجرای واقعی. |
| متوسط | RSI ثابت در `_rsi` عدد۱۰۰ می‌دهد؛ دوره‌ها، NaN و penaltyهای منفی کنترل کافی ندارند. | RSI ثابت=۵۰، اعتبارسنجی دوره و ورودی Decimal محدود؛ هیچ penalty تجربی در generator ساخته نمی‌شود. |
| بالا | تفکیک دو ledger فقط توصیف شده؛ اجرای دفتر رویداد و تست‌ها موجود نیستند. | JSONL مستقل با event ID، dedup، زنجیرهٔ hash، قفل advisory، flush/fsync و کنترل بازگشایی. |

مثال نقض داور ارسالی: `free_capital=100`، `reserved_capital=50`، دو درخواست۴۰
و سقف exposure پیش‌فرض۱؛ هر دو ابتدا affordable تلقی می‌شوند، exposure نهایی۸۰
از سقف۱۰۰ عبور نمی‌کند، اما بودجهٔ باقی‌مانده به منفی۳۰ می‌رسد. در پیاده‌سازی
اصلاح‌شده، available capital=۵۰ صریح است و درخواست دوم رد می‌شود.

## وضعیت مخزن و دامنهٔ Bitpin

GitHub دوباره بررسی شد: main برابر
`fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`، تاریخ commit:
`2026-10-05T15:20:22Z`. PR پژوهشی #115 پیش از اصلاحات V3 در head
`ff7871dace5d4aeb5c44d96eda48c1b2e9051f3b` بود. commit مهندسی جدید به‌تنهایی
تصمیم علمی یا مجوز paper/live ایجاد نمی‌کند. سند canonical سپتامبر همچنان
طبق AGENTS محل ناوبری حاکمیتی است و به‌دلیل تاریخ قدیمی کنار گذاشته نشده است.

[مستندات رسمی Bitpin](https://docs.bitpin.ir/v1/) و
[اعلان منسوخ‌شدن](https://docs.bitpin.ir/v1/docs/deprecations) در این بررسی
فقط هشدار منسوخ‌شدن برخی APIها را در محتوای قابل واکشی نشان دادند. تغییر
«دامنهٔ اصلی رسمی API به .org» تأیید نشد. آداپتر انتخاب صریح یکی از
`https://api.bitpin.ir` و `https://api.bitpin.org` را پشتیبانی می‌کند؛ default
نوت‌بوک .ir حفظ شده و هیچ fallback یا انتقال token بین میزبان‌ها انجام نمی‌شود.
هیچ API صرافی در این بررسی فراخوانی نشد؛ schema جاری همچنان UNVERIFIED است.

## معماری مسیر فعال Phase‑1

```text
Closed PIT Candles → MA Cross / RSI / RSI+MA → HypothesisCandidate
    → abstention adapter → unchanged DecisionEngine → SignalEvidence(NO_TRADE)
    → optional independent risk diagnostics → OperationalResearchEventLedger

HypothesisCandidate + explicit quote-valued sizing proposals
    → independent PortfolioConflictArbiter → offline reservation plan only
```

ثبت Hypothesis و Decision در ledger خودکار است. داور مستقل است و در CLI
هیچ درخواست رزرو پیش‌فرض ایجاد نمی‌شود. برای ثبت برنامهٔ داوری یا رویداد
شبیه‌ساز می‌توان `ARBITRATION`/`SIMULATOR` را به همان دفتر مستقل افزود؛ این
اتصال به‌صورت خودکار به شبیه‌ساز وصل نشده و هیچ اجرای سفارش ایجاد نمی‌کند.

زنجیرهٔ hash اصالت یا حذف انتهای معتبر فایل را بدون checkpoint بیرونی ثابت
نمی‌کند. persistence روی Unix با قفل advisory بین نویسندگان همکار پشتیبانی
می‌شود؛ حالت حافظه فقط run-local است. دفتر scientific milestones v17 تغییر
نکرده و این رویدادها نه milestone علمی هستند و نه شواهد prospective v0.51.

[ADR-JAM-001](ADR-JAM-001.md) و [ADR-JAM-002](ADR-JAM-002.md) اکنون فایل واقعی
دارند. Grid، Triangle و MACD قبلی برای تست رگرسیونِ نمونه‌های مهندسی باقی
مانده‌اند؛ هنوز SignalEvidence غیرقابل‌اجرا می‌سازند و API قدیمی حذف نشده است.
آن‌ها در registry جدید یا CLI Phase‑1 پذیرفته نمی‌شوند. این مرزبندی به معنی
مهاجرت تمام APIهای قدیمی به قرارداد جدید یا عبور Gate علمی نیست.

## اجرای بازتولیدپذیر و مرحلهٔ اعتبارسنجی

```bash
python -m scripts.run_jamalian_research --input closed_bars.json --output phase_one.json --events phase_one_events.jsonl
python -m unittest discover -s tests -p 'test_jamalian*.py' -q
```

strategy یکی از `ma_cross`، `rsi` یا `rsi_ma` است. decision_time صریح و aware؛
close به‌شکل decimal string؛ هر candle شامل symbol، source، closed_at و
available_at است. experiment_id اختیاری در این CLI به namespace مهندسی
پیش‌فرض منتهی می‌شود؛ نام‌گذاری experiment به معنی preregistration نیست.
خروجی جدید با mode=x ایجاد می‌شود؛ دفتر JSONL قابلیت ادامهٔ همان experiment
و dedup دارد. زمان دیگری برای تصمیم، رخداد دیگری محسوب می‌شود.

تست‌های synthetic هر سه Baseline موفق بودند. **ValidationHarness روی مسیرهای
بازدهٔ واقعی این سه Baseline اجرا نشده است**: dataset منجمد، مشخصات اجرا/خروج،
هزینه، بازار، sampling و ثبت تمام trials تحویل نشده‌اند. ساخت ماتریس تصادفی
و نام‌گذاری ستون‌ها به‌نام Baselineها، شاهد اقتصادی محسوب نمی‌شود.

پیش از ارزیابی اقتصادی باید universe/venue، پنجرهٔ development و OOS، ورودی
و خروج، horizon، سرمایه، کارمزد و لغزش، هزینهٔ گردش، روش اجرا در رخداد بعدی،
همهٔ تنظیم‌های آزموده‌شده و معیار رد تعیین و freeze شوند. سپس مسیرهای
هم‌زمان و هزینه‌شده تولید شوند، CPCV با purge/embargo روی label intervals،
CSCV/PBO و DSR به‌عنوان diagnostic به کار روند و dependence با inference
بلوکی بررسی شود. این پروتکل از v0.51 و Kraken holdout مستقل خواهد بود.

منابع روش‌شناختی:
[Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)،
[Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
Harness موجود fixed-path diagnostic است و refit داخل fold، بازسازی execution
یا promotion انجام نمی‌دهد. هیچ Gate اقتصادی پاس نشده و هیچ ادعای alpha
مثبت، اعتبارسنجی دانشگاهی یا آماده‌بودن نتایج برای دفاع پایان‌نامه صادر نمی‌شود.

## شواهد مهندسی

۶۴ آزمون محلی Jamalian شامل ۲۶ آزمون جدید Phase‑1/host/capital موفق شدند؛
fixtures مهندسی‌اند. ۱۱ تابع regression موجود (orchestrator، milestone ledger،
pipeline و risk calibration) مستقیم اجرا و موفق شدند؛ این اجرای کامل pytest
نیست. `compileall`، syntax نسخهٔ۳.۱۱ و `git diff --check` موفق بودند. هر سه
Baseline با CLI واقعی و فایل‌های جداگانهٔ synthetic اجرا شدند؛ هر اجرا یک
Hypothesis، یک Decision=NO_TRADE و دفتر JSONL با fsync ایجاد کرد.
CI کامل باید روی head جدید PR ارزیابی شود؛ نتیجهٔ سبز commit قبلی، نتیجهٔ
اصلاحات V3 نیست. هیچ تغییری در `contracts.py`، `decision.py`، v17/v51،
scientific status، execution firewall یا dependency manifest ایجاد نشده است.
