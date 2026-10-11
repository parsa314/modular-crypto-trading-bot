# گزارش ادغام پژوهشی دوره — ۱۰ اکتبر ۲۰۲۶

نتیجه: کد و بسته پژوهشی قابل‌بررسی ساخته و آزموده شد؛ پذیرش کامل مأموریت و ارتقای علمی همچنان **INTEGRATION_BLOCKED** است. داده واقعی معتبر، ثبت پیشینی آزمایش جدید، CPCV مدل‌های بازآموزی‌شده و شواهد prospective فراهم نشده‌اند. هیچ سفارش واقعی یا Paper اجرا نشده و GitHub تغییر نکرده است.

## A. Source Audit Report

شش نوت‌بوک به‌صورت JSON/AST، بدون اجرای سلول‌ها، بررسی شد: ۴۴۸ سلول، ۳۹۰ سلول کد و ۸۵ رخداد تعریف تابع. گزارش [A_SOURCE_AUDIT.md](A_SOURCE_AUDIT.md) و `evidence/source_audit.json` شامل هش هر فایل، محل تابع، وابستگی، اشکالات و طبقه‌بندی مهاجرت هستند. آموزش مقدماتی و مشتریان مرکز خرید به استراتژی مالی تبدیل نشد. مقدار واقعی SMA در کد ۵۰/۱۵۰ است، با وجود برچسب ۲۰/۵۰. Grid و مثلث آموزشی اثبات چرخه اجرا یا سود نیستند. توسعه‌های مهندسی مستقل برچسب ENGINEERING_EXTENSION دارند.

## B. Repository Compatibility Report

نسخه اصلی از GitHub خوانده و ۲۶۷ blob با SHA گیت تأیید شد: `main @ fa31e6f5ceb09f4f14ae13fef50706f2e10e1835`. این محیط snapshot است، نه checkout دارای شاخه/HEAD محلی. گزارش [B_REPOSITORY_COMPATIBILITY.md](B_REPOSITORY_COMPATIBILITY.md) قراردادها، PRهای باز و اختلاف‌ها را ثبت می‌کند. نام‌های CognitiveOrchestrator، MetaCognition و RiskSovereignGate در این main وجود ندارند؛ اجزای واقعی ResearchTradingOrchestrator، DecisionEngine و RiskEngine توسعه یافتند. وضعیت v50 منفیِ پشتیبانی‌شده و وضعیت v51 بدون تصمیم علمی canonical جدید حفظ شد. اختلاف ساعت شروع v51 در main صرفاً گزارش شد و تغییر نکرد.

## C. Architecture Decision Records

[تصمیم‌های معماری](C_ARCHITECTURE_DECISIONS.md) مسئله، گزینه‌ها، شواهد، انتخاب، هزینه انتخاب، امنیت/مالی، پذیرش و بازگشت را ثبت می‌کند. هسته و RiskEngine دیگری ایجاد نشده است. زنجیره افزوده: ورودی معتبر → HypothesisCandidate → SignalEvidence/DecisionEngine → arbitration علّی مخصوص آزمایش جدید → تصویر سرمایه و exposure → RiskEngine موجود → DENIED_RESEARCH_ONLY → NoOrderTransport. این arbitration جایگزین یا اصلاح CSM-DAP/v51 نیست.

## D. Source Code

کد افزوده در `research_bot/course/`: قراردادهای immutable و Decimal؛ آداپتر GET عمومی Bitpin؛ ریزساختار؛ ۹ مولد مستقل؛ بازپخش Entry/Exit شش قاعده تکنیکال؛ شبیه‌ساز چرخه سفارش؛ Grid inventory-aware؛ موتور گرافی/اجرایی مثلث؛ تطبیق با هسته و شواهد؛ ارزیابی و تنظیمات نسخه‌دار؛ و projection فقط‌خواندنی مانیتورینگ. مشخصات [استراتژی‌ها](D_STRATEGY_SPECIFICATIONS.md)، [حالت‌دارها](D_STATEFUL_SPECIFICATIONS.md) و [Bitpin](C_BITPIN_API_DECISION.md) فرمول و محدودیت را مشخص می‌کنند.

۱۱ خانواده پوشش داده شده‌اند؛ این به معنی ۱۱ استراتژی تأییدشده اقتصادی نیست. همه قواعد پیش‌فرض غیرفعال و RESEARCH_CANDIDATE هستند. شش قاعده تکنیکال خروج TP/SL/TIMEOUT/indicator را با فرض محافظه‌کارانه next-open مدل می‌کنند. ticker/book botهای آموزشی فاقد بازپخش تاریخی قابل‌اعتبارسنجی‌اند؛ proxy کندلی آن‌ها جداگانه علامت دارد. Limit fill تنها با لمس OHLC تضمین نمی‌شود. Stop-limit و private/live API قابلیت کامل معرفی نشده‌اند.

## E. Automated Test Suite

اجرای واقعی کل suite: **۳۹۳ passed، ۱ skipped، ۳ warnings؛ ۷۸٫۰۳ ثانیه**. تست PostgreSQL موجود به دلیل نبود TEST_DATABASE_URL رد نشده، بلکه skip شده است. ۱۹۵ تست قبلی نیز در اجرای مستقل پاس شده بود. Ruff روی فایل‌های افزوده/ویرایش‌شده و تست‌های دوره موفق؛ Mypy محدود به ۱۳ فایل دوره/runner با follow-imports=skip و ignore-missing-imports موفق است؛ این ادعای type-check کل مخزن نیست.

Wheel ساخته شد؛ هر سه JSON تنظیمات در آن وجود دارد و بارگذاری قواعد غیرفعال و تنظیمات از بیرون درخت source آزموده شد. [ماتریس پوشش و محدودیت تست](E_TEST_COVERAGE_AND_LIMITS.md). خروجی واقعی نهایی در `evidence/final-full-tests.log`، `ruff.log`، `mypy.log` و `verification_summary.json` بسته تحویل قرار دارد. هیچ آزمون اصلی به کلید یا موجودی واقعی وابسته نبود. اجرای ASGI tests بیرون sandbox لازم شد چون محدودیت thread محیط موجب hang می‌شد؛ این تغییر flags یا اتصال حساب نبود.

## F. Scientific Evaluation Package

[پروتکل](F_SCIENTIFIC_PROTOCOL.md)، سه config نسخه‌دار، runner `scripts/run_course_research.py` و `evidence/final-verified-offline-run/` قابل بازتولید هستند. اجرای seed42: ۳۶۰ کندل مصنوعی، ۲۰۸ رویداد یک‌کندلی جهت بررسی مکانیک ارزیابی، ۱۱ hypothesis/NO_TRADE، یک fill صرفاً شبیه‌سازی، و ۴۱ رویداد خروج تکمیل‌شده برای شش قاعده تکنیکال؛ شمارش به‌ترتیب SMA۲، Bollinger۶، RSI۰، Donchian۲، MACD۲۰، confluence۱۱. صف واقعی، اثر بازار و سودآوری از این اعداد استنتاج نمی‌شوند.

بازده net هر رویداد بر حسب notional اولیه برابر gross منهای هزینه round-trip/10000 است؛ چهار هزینه ۰/۲۴/۳۶/۵۰ bps گزارش می‌شود. هزینه یک بار محاسبه می‌شود. CVaR میانگین tail loss تعریف‌شده است، نه تضمین حد زیان آتی. Average uniqueness از انتگرال زمان‌وزنی 1/concurrency روی بازه نیمه‌باز ورود تا دسترس‌پذیری label محاسبه می‌شود. مقدار۱ در fixture یک‌کندلی تنها پیامد عدم همپوشانی آن fixture است.

تقسیم زمانی، fitting صرفاً train، purge بازه‌های اطلاعات، embargo و محدودیت holdout آزموده شده‌اند. ترکیبات baseline CPCV چهار گروه/دو test تولید می‌شود (۶ split و۳ path). این **CPCV کامل مدل‌های retrained نیست** و در JSON blocked است. PBO در fixture به دلیل نبود خانواده ثبت‌شده کامل/پنل قابل‌اعمال NOT_APPLICABLE است. DSR به دلیل نبود اطلاعات مستقل trial قابل تأیید نیست. Sequential bootstrap، mean-CVaR optimizer، ML shadow promotion، تمام feature-ablationهای واقعی و capacity/market-impact research تکمیل نشده‌اند.

معیار پذیرش اقتصادی باید پیش از داده آزمون freeze شود: اختلاف paired خالص نسبت به baseline با فاصله اطمینان block-bootstrap و اصلاح multiplicity؛ پایداری cost stress؛ tail/trade support کافی؛ بودجه turnover/CVaR و افت سرمایه در risk profile مصوب؛ تکرار مستقل prospective. آستانه جهان‌شمول PBO/DSR یا ادعای بهینه‌بودن پارامتر ارائه نشده است. دقت ML به‌تنهایی promotion ایجاد نمی‌کند.

## G. Execution Safety Audit

[ممیزی ایمنی](G_EXECUTION_SAFETY_AUDIT.md) و تست‌ها default NoOrderTransport، رد nonfinite، عدم تأثیر توکن محیطی، عدم تزریق paper به مسیر دوره، سرمایه/موجودی/partial/restart و رد داده آینده را پوشش می‌دهند. Bitpin book رسمی فاقد زمان و sequence لازم است؛ observations آن DATA_UNAVAILABLE می‌مانند. حداقل notional قابل تأیید و server time نیز UnsupportedCapability هستند. Auth/session/token-expiry، سفارش واقعی و تطبیق حساب پیاده‌سازی و تأیید نشده‌اند.

## H. Migration & Rollback

[گزارش مهاجرت](H_MIGRATION_ROLLBACK.md) و [مانیتورینگ](H_MONITORING_MIGRATION.md) تغییرات و بازگشت را ثبت می‌کنند. تنها شش فایل موجود تغییر کرده‌اند؛ frozen evidence و limits دست‌نخورده‌اند. main DD۱۲٪ و gross۱۰۰٪ با نمونه‌های DD۵٪/gross۷۰٪ و ریسک معامله۰٫۲۵٪ اختلاف دارد؛ این اختلاف با عوض‌کردن خاموش اعداد حل نشد. Default paper simulator تغییر رفتار مهم مهندسی است و باید در PR روشن باشد. سه config داخل package توزیع می‌شود و وابستگی runtime جدیدی اضافه نشد.

## I. Thesis-Ready Technical Report

مسئله پژوهش: آیا بازسازی مستقل قواعد آموزشی، پس از هزینه، در داده point-in-time و بدون نقض risk/execution sovereignty، نسبت به baseline ارزش افزوده دارد؟ فرضیه صفر این است که برتری خالص پایدار و مستقل ندارند. روش پیشنهادی: ثبت پیشینی خانواده قواعد/هزینه/label/calendar، holdout دست‌نخورده، مقایسه paired، block bootstrap، ablation با مجموعه ثابت و جمع‌آوری first-seen واقعی برای دفتر سفارش/Grid/triangle.

یافته تأییدشده این نوبت مهندسی است: source extraction، isolation، invariance، conservation، restart، schema failure و بازپخش fixture طبق خروجی تست. یافته آماری و اقتصادی درباره بازار واقعی نداریم. سه سفارش triangle اتمیک نیست؛ partial/failed leg می‌تواند inventory باز و هزینه unwind ایجاد کند. Range شاهد fixture است، نه کشف regime واقعی. ثبت candidate با edge نامعلوم عمداً به NO_TRADE می‌رسد. دفتر رویداد در store مشترک revision را رد می‌کند؛ کپی‌کردن storeهای مستقل تضمین uniqueness سراسری ایجاد نمی‌کند. stale lock نیازمند بررسی عملیاتی است.

| مؤلفه | مرحله اثبات‌شده | مانع باقی‌مانده |
|---|---|---|
| منبع شش نوت‌بوک | STATIC_EVIDENCE_VERIFIED | انتساب خارج از فایل‌ها ممنوع |
| قرارداد/مولدها/قواعد خروج | IMPLEMENTED → TESTED → INTEGRATED در fixture | real-data و economic validation |
| Grid/triangle/simulator | مهندسی آفلاین آزموده | queue/latency/cost واقعی و prospective |
| Bitpin public adapter | schema/retry fixture آزموده | زمان/sequence/metadata اجرای معتبر |
| Decision/Risk/NoOrder/evidence | یکپارچه، شواهد مهندسی ثبت‌شده | constitution reconciliation و risk review مستقل |
| Validation | مکانیک شرطی آزموده | full learned CPCV، trial universe، داده و ablations |
| Monitoring | GET-only، ۱۵ panel، whitelist آزموده | گزارش واقعی بازار و عملیات مستقر |
| عملکرد اقتصادی/اجرای حساب | UNVERIFIED | داده، ثبت پیشینی، مجوز مستقل و محیط اجرا |

## J. GitHub Delivery Report

هیچ remote branch، commit، PR یا CI run ایجاد نشد. Branch پیشنهادی: `engineering/course-integration-research-only-v2`. Base SHA بالا معتبر است؛ patch و source package قابل اعمال به همان base تهیه شده‌اند. Commit SHA جدید **وجود ندارد**؛ SHA فایل‌های تغییر یافته و صحت اعمال patch در DELIVERY_MANIFEST.json است. آزمون‌ها local هستند، نه GitHub CI. main، PRهای علمی و حساب MT5/Bitpin تغییر نکرده‌اند.

بازآفرینی: وابستگی‌های پروژه و pytest را نصب کنید، `python -m pytest tests -q -rs`، سپس `python scripts/run_course_research.py --output /tmp/new-course-run`. مسیر خروجی باید تازه باشد. برای مانیتورینگ فقط `COURSE_RESEARCH_REPORT=/tmp/new-course-run/report.json` تنظیم می‌شود؛ این متغیر مجوز اجرا نیست. موفقیت exit code runner به معنی تکمیل exercise است؛ وضعیت پذیرش را از report.status بخوانید که INTEGRATION_BLOCKED باقی می‌ماند.
