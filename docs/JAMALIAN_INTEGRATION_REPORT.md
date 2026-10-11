# گزارش بررسی و ادغام پژوهشی دورهٔ سجاد جمالیان

تاریخ بررسی: 2026-10-11. مخزن مرجع: `parsa314/modular-crypto-trading-bot`.
مبنای کد: `fa31e6f5ceb09f4f14ae13fef50706f2e10e1835` روی `main`.
وضعیت خروجی: **پیاده‌سازی پژوهشی و آزمون مهندسی؛ بدون تأیید اقتصادی**.

## دامنه و محدودیت شواهد

پنج نوت‌بوک ارسالی، سلول‌های کد و توضیحات آن‌ها به‌صورت ایستا بررسی شدند.
هیچ نوت‌بوک، فراخوانی صرافی یا حلقهٔ معاملاتی دوره اجرا نشد. خروجی‌های ذخیره‌شدهٔ
نوت‌بوک‌ها شواهد اجرای فعلی یا سودآوری نیستند. فایل‌های اصلی دوره، خروجی کیف پول،
تصاویر و تاریخچهٔ سفارش‌ها در این تغییر به مخزن عمومی منتقل نشده‌اند.

`Python_1`، `config_bitpin.py`، `Mall_Customers.csv` و فایل پکیج نهایی ادعاشده
دریافت نشده‌اند. متن ارسالی کاربر مشخصات هدف ادغام است؛ متن کامل Master Prompt
و §14 آن در دسترس نیست. بنابراین انطباق دقیق با آن بخش ادعا نمی‌شود.

شناسهٔ سلول‌ها در این گزارش **از صفر** و بر اساس آرایهٔ `cells` است.
نام فایل‌ها در ستون نخست بدون پیشوند شمارهٔ پیوست نمایش داده شده‌اند.

| فایل | سلول / کد | SHA-256 فایل ارسالی |
|---|---:|---|
| Python_6_Build_Bot.ipynb | 25 / 15 | `dc2fab1506425e1d184d8bdf4bbb050fbabb40110861e0d24b500631f722cd5b` |
| Python_5_Trade_Strategy.ipynb | 21 / 12 | `082b19d5bccc5eda3084eac30aae9fd12589130686b0ef10c3a9044672e8192b` |
| Python_4Connect_to_Bitpin.ipynb | 66 / 47 | `bc3a9fed89543123e5ee2943e8d8c8896e2436812c90edfef80c87f5b190d6ad` |
| Python_3.ipynb | 19 / 19 | `1b0f0002c25af7e14bf695b6aca5f80160407d02f876c473d5e6d7ce9e9b0c33` |
| Python_2_Mall_Customer.ipynb | 68 / 67 | `6c735f48b407ae9e100d80db37f0edc80b5a792aa39ad4365f63f92f19cd15bb` |

## یافته‌های بررسی کد دوره

| اهمیت | محل | رفتار مشاهده‌شده و پیامد | اصلاح در این تغییر |
|---|---|---|---|
| بالا | Python_4:30–38،47–48،56–58؛ Python_6:1 و سلول‌های فراخوانی ربات | مسیرهای واقعی POST سفارش و DELETE لغو همراه با فراخوانی مستقیم وجود دارند. اجرای عمومی سلول‌ها می‌تواند حساب را تغییر دهد. | تمام متدهای متناظر آداپتر قبل از transport خطا می‌دهند؛ ایجاد آدرس واریز هم مسدود است. |
| بالا | Python_4:63؛ Python_6:4،8،11،22 | فروش هدف پس از دریافت شناسهٔ خرید ثبت می‌شود، بدون تأیید FILLED یا حجم واقعی خرید. | موجودی فقط با رویداد پرشدن شبیه‌ساز افزایش می‌یابد؛ فروش پیش از خرید پرشده رد می‌شود. |
| بالا | Python_6:14 | نبود شناسه در سفارش‌های فعال، FILLED تلقی می‌شود؛ سفارش لغوشده/ردشده هم می‌تواند جایگزین شود. پاسخ bulk نیز به‌عنوان شناسه استفاده می‌شود و `actice_grid`/`active_grid` ناسازگارند. | چرخهٔ عمر مستقل، پرشدن جزئی، لغو و انقضا؛ موجودی Grid از `inventory_from_simulator` می‌آید. |
| بالا | Python_6:18 | سه سفارش بازار پشت سر هم؛ حجم و قیمت واقعی پرشدن، عمق، عمر داده و ریسک شکست پای دوم/سوم کنترل نمی‌شود. | سناریوی سه‌پایه با عمق و هزینه، کنترل تازگی و اختلاف زمان، برچسب غیراتمیک و باقی‌ماندن دارایی؛ هیچ سفارشی ارسال نمی‌شود. |
| بالا | Python_5:4،6،9،11،13،16 | تصمیم با Close همان ردیف و ورود/خروج با همان Close؛ هزینهٔ لغزش، محدودیت نقدشوندگی و ترتیب درون کندل مدل نشده‌اند. | فرضیه فقط از کندل بسته‌شدهٔ قابل دسترس استفاده می‌کند؛ شبیه‌ساز روی تصویر سفارش‌نامهٔ بعدی کار می‌کند. موتور بک‌تست اصلی بازنویسی نشده است. |
| متوسط | Python_5:9،11،13،16 | در شورت، مبلغ پس از کسر کمیسیون برای اندازه استفاده می‌شود ولی margin کامل به حساب بازمی‌گردد؛ کارمزد ورود مستقل از سرمایه کسر نمی‌شود. هزینهٔ استقراض، funding و liquidation نیز مدل نشده‌اند. | SHORT فقط metadata پژوهشی است؛ شبیه‌ساز spot فروش بدون موجودی را نمی‌پذیرد. |
| متوسط | Python_5:4،6 | نام SMA20/SMA50 با پنجره‌های 50/150 برابر نیست. نوت‌بوک چند تنظیم و پنجرهٔ زمانی دارد، اما ثبت کامل جست‌وجو/OOS مستقل ندارد. | پارامترها و روش محاسبه در سیگنال ثبت می‌شوند؛ ارزیابی اقتصادی به پروتکل جداگانه وابسته است. |
| متوسط | Python_4:3،6؛ Python_6:1 | timeout حذف شده، 429 به بازگشت بازگشتی نامحدود منجر می‌شود؛ نتیجهٔ شکست login در ساخت هدر نادیده گرفته می‌شود. | timeout صریح، retry محدود، عدم تکرار POST احراز هویت، شکست احراز هویت قبل از GET کیف پول. |
| متوسط | Python_4:14،43،50،53 | دسترسی `[0]` روی نماد غایب و پردازش None در خطا؛ کمبود کنترل schema. | کنترل نوع و مقدار داده، نماد استاندارد و خطای صریح؛ تغییر schema خودکار حدس زده نمی‌شود. |
| متوسط | Python_6:11،22 | تاریخچهٔ polling قیمت جای کندل منظم استفاده می‌شود؛ RSI تاریخچهٔ ثابت را 100 می‌دهد؛ متغیر بی‌تعریف `a` هم در سلول11 وجود دارد. | ورودی کندل بسته‌شدهٔ منظم؛ RSI ثابت=50؛ گرم‌شدن ناکافی هیچ خروجی ایجاد نمی‌کند. |
| متوسط | Python_6:8 | حجم تمام ردیف‌های برگشتی جمع می‌شود؛ تعداد ردیف‌ها پیش‌فرض5 است، درحالی‌که متن از10 ردیف می‌گوید. پارامتر stop_loss استفاده نمی‌شود. | استراتژی imbalance در چهار فرضیهٔ درخواست‌شده نیست و به‌عنوان فرضیهٔ بعدی باقی مانده است؛ نتایج آن ادعا نمی‌شود. |

`Python_2` آموزش pandas، پاک‌سازی، نمودار و دسته‌بندی قاعده‌ای مشتریان است؛
مدل هوش مصنوعی معاملاتی ندارد. `Python_3` مثال عمومی HTTP GET/POST است.
آن‌ها مبنای مهندسی داده‌اند، نه شواهد سودآوری. scalper و order-book imbalance
بررسی شدند ولی در این تغییر به استراتژی تولیدی تبدیل نشده‌اند.

## تطبیق مشخصات ارسالی با معماری واقعی

1. مخزن دارای `research_bot/execution.py` است. ایجاد پوشهٔ `execution/`
   می‌تواند resolution import را عوض کند. شبیه‌ساز در `event_driven_simulator.py`
   قرار گرفت و ماژول اجرایی موجود تغییر نکرد.
2. `SignalEvidence` واقعی دارای فیلدهای float است. قیمت/حجم/رزرو/هزینهٔ
   شبیه‌ساز Decimal هستند؛ قرارداد موجود بازتعریف نشده است. اعداد صفر در فیلدهای
   پیش‌بینی همراه با `forecast_available=false` و confidence=0 به معنای
   **نبود پیش‌بینی معتبر** هستند.
3. قرارداد Decision، گزینهٔ SHORT_CANDIDATE ندارد. جهت LONG/SHORT در metadata
   ثبت می‌شود و تمام تصمیم‌ها `NO_TRADE` می‌مانند. هدف سود به‌عنوان expected_return
   وارد سیستم نمی‌شود.
4. v0.51 یک مقایسهٔ منجمد با predictor، دارایی‌ها، venueها، هزینه و قواعد ثابت است.
   افزودن فرضیه‌های دوره به آن آزمایش مجاز نیست. رجیستری مستقل با
   `eligible_for_v51=false` ایجاد شد؛ `overlap_arbitration_v51.py` تغییر نکرد.
5. `evidence_ledger_v17` تاریخچهٔ milestones است، نه storage عمومی سیگنال.
   تاریخچه بازنویسی نشد؛ `JamalianResearchLedger` مستقل، خروجی را deduplicate
   می‌کند، hash می‌دهد و قبل از ثبت به مسیر فقط‌پژوهشی Orchestrator می‌فرستد.
6. `process_hypothesis` می‌تواند snapshot ریسک مستقل را بررسی کند، اما حتی
   تأیید ریسک هم به اجرا منجر نمی‌شود. `process_forecast` قبلی حفظ شده است.

قراردادهای مرجع خوانده‌شده: `AGENTS.md`، README، وضعیت canonical و سندهای منجمد
v0.50/v0.51 در SHAهای `9b3f8d1b...`، `d8ee4576...`، `4838c0d9...` و `da5dcf5c...`.
PR فعال #62 با head `cd897d9ac367e1f3cbead94e720efcaf949093c1` مشاهده شد؛
این تغییر روی آن PR یا lineage انباشتهٔ پژوهشی اعمال نشده است.

## خروجی‌های پیاده‌سازی

| مؤلفه | رفتار قابل بررسی | محدودیت |
|---|---|---|
| `exchanges/bitpin_adapter.py` | public GET قیمت و سفارش‌نامه؛ Auth-Read کیف پول با opt-in؛ Decimal، timeout، allowlist، عدم دنبال‌کردن redirect، schema check، hash پاسخ | schema v1 از نوت‌بوک؛ سازگاری جاری صرافی به‌صورت live تأیید نشده است. |
| `jamalian_data.py` | MarketDataPort، Candle، Ticker، OrderBook؛ دادهٔ مثبت و محدود، UTC/aware، قیمت مرتب و غیرمتقاطع | صحت timestamp ورودی نیازمند provenance بیرونی است. |
| `RsiMaHypothesis` | همان قاعدهٔ RSI rolling ساده + MA، ورودی کندل منظم و بسته‌شده | تبدیل polling به کندل، تغییر عمدی ورودی نسبت به دوره است. |
| `MacdRsiEmaHypothesis` | EMA/MACD recursive و RSI Wilder با seed میانگین؛ تقاطع LONG/SHORT | RSI دوره از ewm با adjust پیش‌فرض استفاده می‌کند؛ variant جدید دقیقاً replay دوره نیست. |
| `GridHypothesis` | شبکهٔ مبتنی بر tick، بودجهٔ موجودی آزاد، تعلیق کاندیدها پس از breakout | سفارش‌های شبیه‌سازی‌شدهٔ قبلی خودکار لغو نمی‌شوند؛ باید با رویداد cancel و latency مستقل بررسی شوند. |
| `TriangularArbitrageHypothesis` | محاسبهٔ عمق چندسطحی سه‌پایه، fees/slippage سناریویی، IRT صریح، کنترل عمر/اختلاف زمان | فرض کسر fee از دارایی دریافتی؛ rounding/min-notional واقعی سه بازار و اجرای غیراتمیک مدل کامل ندارند؛ سود قابل اجرا ادعا نمی‌شود. |
| `EventDrivenExecutionSimulator` | CREATED/WORKING/PARTIALLY_FILLED/CANCEL_PENDING/FILLED/CANCELLED/EXPIRED/REJECTED؛ reserve، latency، idempotency، fee، نقدشوندگی مشترک | آفلاین؛ fill تنها هنگام عبور از سمت مقابل book؛ queue، hidden liquidity و price impact مدل نشده‌اند. |
| `jamalian_integration.py` + Orchestrator | registry، دفتر شواهد مستقل، موجودی مبتنی بر fill و خروجی risk بدون execution | پیش‌بینی کالیبره یا promotion تولید نمی‌کند. |
| `jamalian_validation.py` | CPCV با purge/embargo روی label intervals، CSCV/PBO و DSR موجود مخزن با کنترل ورودی و حمایت نمونه | تشخیص انتخاب روی مسیرهای بازدهٔ ثابت؛ جایگزین refit داخل fold یا آزمون مستقل اقتصادی نیست. |

### قراردادهای شبیه‌سازی

- Limit و Market هر دو protection price صریح دارند؛ Market در این مدل محدود
  به همان قیمت است و بازار واقعی را شبیه‌سازی کامل نمی‌کند.
- fee شبیه‌ساز از quote پرداخت می‌شود؛ سناریوی مثلثی fee را از دارایی دریافتی
  کم می‌کند. این دو فرض متفاوت‌اند و به‌عنوان pipeline یکسان جمع زده نمی‌شوند.
- stop-limit از best ask برای BUY و best bid برای SELL تحریک می‌شود؛ trigger
  واقعی صرافی تأیید نشده است.
- خرید با سقف قیمت و fee رزرو می‌شود، فروش با موجودی base. partial fill بخشی
  از رزرو را مصرف می‌کند؛ cancel acknowledgement و expiry باقی رزرو را آزاد می‌کنند.
- لغو و expiry در timestamp یکسان پیش از matching پردازش می‌شوند. قبل از ack
  لغو، fill هنوز ممکن است. حذف‌شدن سفارش از لیست هرگز رویداد fill نیست.
- snapshot زمان تصمیم خرید را پر نمی‌کند؛ snapshot قدیمی که دیر رسیده نیز
  بعد از تصمیم قابل استفاده برای پرکردن آن سفارش نیست.
- تصویر book تنها یک بار مصرف می‌شود و نقدشوندگی بین سفارش‌ها مشترک است.
- مقادیر tick/lot پیش‌فرض شبیه‌ساز آموزشی‌اند؛ در replay واقعی باید از مشخصات
  ثبت‌شدهٔ بازار دریافت شوند. هیچ metadata بازار صرافی حدس زده نشده است.

## تحقیق منابع اصلی

- [مستندات رسمی Bitpin](https://docs.bitpin.ir/v1/docs/Introduction/bitpin-api-documentation)
  و [اعلان منسوخ‌شدن](https://docs.bitpin.ir/v1/docs/deprecations) در 2026-10-11
  بررسی شدند. صفحهٔ قابل واکشی فقط هشدار منسوخ‌شدن برخی APIها را نشان داد؛
  جزئیات جاری endpointها قابل تأیید نبود. بنابراین adapter دقیقاً با عنوان
  «schema نوت‌بوک، UNVERIFIED برای API جاری» ارائه شده است.
- [مستندات رسمی دادهٔ بازار Binance](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints)
  ساختار دادهٔ book و candle را از مسیرهای trade جدا می‌کند. منبع دادهٔ بک‌تست
  Binance را نمی‌توان بدون بررسی واحد، ساختار بازار و هزینه‌ها به Bitpin تعمیم داد.
- [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)
  بخش2.2/Algorithm2.3، نیاز به ماتریس هم‌زمان بازدهٔ تمام configurations را توضیح
  می‌دهد. PBO از CSCV به دست می‌آید و با CPCV یکی نیست.
- [Bailey & López de Prado, The Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
  اصلاح انتخاب چندگانه و غیرنرمال‌بودن بازده را بررسی می‌کند. موفقیت یک تست
  مهندسی، آمار معتبر DSR یا بازده اقتصادی ایجاد نمی‌کند.

## شواهد تازهٔ تست و محدودیت محیط

Python محیط: 3.12.14؛ نسخهٔ canonical مخزن: 3.11. هیچ وابستگی جدیدی به
`pyproject.toml` اضافه نشده است. notebookها dependencies آموزشی اختیاریِ
requests/binance/gradio دارند؛ آن dependencies برای مؤلفهٔ جدید لازم نیستند.

| دستور | نتیجه |
|---|---|
| `python -m unittest discover -s tests -p 'test_jamalian*.py' -q` | 38 آزمون جدید موفق؛ fixtures کاملاً آفلاین/مصنوعی |
| فراخوانی مستقیم test functions در `test_v08_orchestrator.py`، `test_evidence_ledger_v17.py`، `test_pipeline.py`، `test_risk_calibration_v52.py` | 11 regression موجود موفق؛ این روش اجرای کامل pytest نیست. |
| `python -m compileall -q research_bot scripts` | موفق |
| `git diff --check` | موفق |
| `python -m pytest -q` | اجرا نشد: `No module named pytest` |
| نصب pytest/ccxt/psycopg در target محلی | ناموفق؛ index در محیط بسته‌ای ارائه نکرد. |

تست‌ها رزرو، fill جزئی، رقابت لغو/fill، عدم مصرف دوبارهٔ book، عدم موجودی منفی،
بدون موجودی‌بودن فروش، عدم اجرا در Orchestrator، جداسازی v0.51، هزینهٔ quote،
warmup، دادهٔ آینده/غایب، واحد IRT، هم‌زمانی مثلث، allowlist و شکست احراز هویت
را بررسی می‌کنند. harness روی دادهٔ مصنوعی فقط رفتار مهندسی را نشان می‌دهد؛
هیچ شاخص عملکرد مصنوعی در این گزارش به‌عنوان نتیجهٔ مالی ارائه نشده است.

## اجرای مسیر پژوهشی

```bash
python -m scripts.run_jamalian_research --input closed_bars.json --output research_run.json
python -m unittest discover -s tests -p 'test_jamalian*.py' -q
```

CLI برای RSI+MA و MACD+RSI+EMA است. ورودی شامل `strategy`، `decision_time`،
`parameters` اختیاری و آرایهٔ `candles` با فیلدهای `symbol`، `close` به صورت
رشتهٔ decimal، `closed_at`، `available_at` و `source` است. timestampها باید
timezone داشته باشند؛ `closed_at` زمان بسته‌شدن کندل است. خروجی در فایل جدید
نوشته می‌شود و فایل موجود بازنویسی نمی‌شود. Grid و Triangle از API تایپ‌شدهٔ
پایتون با OrderBook و InventoryState استفاده می‌کنند.

## قبل از هر ادعای ارزش افزوده

این بخش **پیشنهاد پروتکل بعدی** است و آزمایش انجام‌شده یا preregistration
تکمیل‌شده نیست:

1. schema، timestamp، fee convention، tick/lot/min-notional و واحد قیمت بازار
   Bitpin در یک بررسی فقط‌خواندنی مستقل ثبت و freeze شوند.
2. برای هر فرضیه comparator ساده، development/validation/test تازه، خروج‌ها،
   سرمایه، هزینه، stress و معیار رد از پیش تعریف شوند؛ Kraken دست‌نخورده بماند.
3. RSI/MACD با دادهٔ closed-bar و execution بعدی؛ Grid با inventory/MTM و
   سفارش‌های outstanding؛ Triangle با book واقعی هم‌زمان و شکست/تاخیر هر leg
   replay شوند. دادهٔ OHLCV جای عمق تاریخی مصنوعی استفاده نشود.
4. تمام configurations و حتی نتایج منفی ثبت شوند؛ CPCV/CSCV/PBO/DSR تشخیص
   هستند و برای دادهٔ دارای dependence باید با inference بلوکی تکمیل شوند.
5. پس از تأیید اقتصادی مستقل، promotion با predictor/cost/risk admissible
   در پروتکل جدید تعریف شود. این تغییر PAPER، TESTNET یا LIVE را باز نمی‌کند.

متن پیشنهادیِ اصلاح‌شدهٔ پرامپت در [JAMALIAN_INTEGRATION_PROMPT.md](JAMALIAN_INTEGRATION_PROMPT.md)
وجود دارد. ادعای «همهٔ Python_1 تا Python_6 نهایی شدند» با فایل‌های دریافت‌شده
قابل تأیید نیست؛ این گزارش فقط پنج فایل و مؤلفه‌های مشخص‌شدهٔ بالا را پوشش می‌دهد.
