# پرامپت اصلاح‌شدهٔ ادغام پژوهشی دورهٔ جمالیان

این متن مشخصات قابل اجرا برای ادامهٔ توسعه است؛ جایگزین متن کامل Master Prompt
ارسال‌نشده نیست. پیاده‌سازی فعلی و محدودیت‌های آن در گزارش همراه ثبت شده‌اند.

در مخزن canonical `parsa314/modular-crypto-trading-bot` کار کن. ابتدا AGENTS،
وضعیت canonical، README و قراردادهای واقعی SignalEvidence/EvidenceStamp را
بخوان؛ وضعیت branch، SHA و PRها را از GitHub تأیید کن. به deployment shell
یا پروژهٔ مستقل Crypto Intelligence Studio کد علمی منتقل نکن.

نوت‌بوک‌های دوره را به‌صورت ایستا و با SHA-256 و شمارهٔ سلول بررسی کن.
نبود فایل، تنظیمات، داده یا Master Prompt را صریح ثبت کن. هیچ کد سفارش‌گذاری
یا خروجی خصوصی notebook را اجرا یا منتشر نکن. متن توصیفی پکیج را وجود فایل
پیاده‌سازی‌شده تلقی نکن.

چهار فرضیهٔ مستقل RSI+MA، MACD+RSI+EMA، Grid و Triangle ایجاد یا تکمیل کن.
جهت و ویژگی‌های سناریویی را در metadata بنویس. اگر بازده مورد انتظار و
confidence کالیبره از شواهد معتبر نداریم، SignalEvidence باید confidence=0،
forecast_available=false و decision=NO_TRADE داشته باشد. حد سود، RSI یا
سود هندسی مثلث جای پیش‌بینی بازده معتبر نیست. SHORT به معنی مجوز فروش spot
یا ایجاد موقعیت مشتقه نیست.

زمان observed_at در candle زمان بسته‌شدن آن است؛ available_at زمان اولین
دسترسی واقعی است. کندل نامنظم، غایب یا تکراری، timestamp ساده و دادهٔ آینده
را کنترل کن. source/hash/time هر ورودی در شواهد مشتق حفظ شود. receipt time
را به‌عنوان exchange event time جا نزن. Decimal برای قیمت، حجم، fee و رزرو؛
قراردادهای float موجود را بدون بررسی producer/consumer تغییر نده.

آداپتر Bitpin باید شبکه را پشت port/transport قابل آزمون نگه دارد. public GET
و auth-read صریح را allowlist کن؛ POST فقط برای احراز هویت. سفارش، لغو، bulk،
ایجاد آدرس و هر mutation دیگر باید پیش از transport fail-closed باشد. timeout،
retry محدود، کنترل schema/مقادیر محدود و عدم انتقال token در redirect را تست کن.
سازگاری جاری API را فقط پس از بررسی مستندات رسمی و contract check گزارش کن.

شبیه‌ساز صرفاً آفلاین است: CREATED، WORKING، PARTIALLY_FILLED، CANCEL_PENDING،
FILLED، CANCELLED، EXPIRED و REJECTED؛ رزرو سرمایه و موجودی، fee، tick/lot،
latency، protection price و idempotency صریح. ثبت سفارش fill نیست. خرید پرنشده
نباید موجودی برای فروش بسازد. نقدشوندگی بین سفارش‌ها مشترک و هر snapshot
فقط یک بار مصرف شود. لغو قبل از ack می‌تواند با fill رقابت کند. نبود سفارش
در فهرست فعال به معنی fill نیست. فرض‌های queue/impact/trigger را مستند کن.

Grid را با موجودی آزادِ مبتنی بر fill/cancel/expiry بساز؛ در breakout کاندید
جدید را متوقف کن و نیاز به بررسی لغو سفارش‌های شبیه‌سازی‌شدهٔ قبلی را ثبت کن.
Triangle باید واحدهای IRT/IRR/USDT، عمق، هزینه، تازگی و اختلاف زمان، rounding
و min-notional، residual inventory و شکست هر leg را صریح داشته باشد. خروجی
آن تا مدل‌شدن این محدودیت‌ها سناریو است، نه سود قابل اجرا.

از Orchestrator مسیر non-actionable جدا داشته باش؛ ارزیابی risk هم اجازهٔ
execution ایجاد نمی‌کند. تاریخی evidence_ledger_v17 را دست‌نخورده نگه دار و
دفتر مستقل برای فرضیه‌ها بساز. v0.51 predictor، universe، هزینه و policy منجمد
دارد؛ فرضیه‌های جدید را داخل آن ثبت نکن. scientific-status، eligibility و
provenance را صریح نگه دار. مسیرهای واقعی PAPER/TESTNET/LIVE این ادغام خاموش‌اند.

برای ارزیابی، پروتکل تازه و freezeشده شامل baseline، پنجره‌های داده، خروج‌ها،
بودجه، هزینه و معیار رد لازم است. همهٔ configurations و نتایج منفی ثبت شوند.
CPCV روی label intervals با purge/embargo، CSCV/PBO روی ماتریس هم‌زمان تمام
trials و DSR با تعداد واقعی trials به کار بروند؛ این‌ها با هم یکی نیستند و
هیچ‌کدام به‌تنهایی promotion نیست. Grid/Triangle بدون دادهٔ لازم DATA_UNAVAILABLE
می‌مانند. دادهٔ مصنوعی فقط engineering smoke است و inference دارای dependence
به آزمون بلوکی نیاز دارد. Kraken باز نشود؛ same-test rescue tuning انجام نشود.

تست‌ها باید عدم mutation شبکه، causality، state transitions، محافظت سرمایه،
هزینه، replay/idempotency و import compatibility را نشان دهند. دستور و نتیجهٔ
واقعی هر تست و محدودیت محیط را ثبت کن. پس از بررسی diff، تغییر را در شاخهٔ
مستقل GitHub و PR قابل مرور ذخیره کن؛ شاخهٔ علمی منجمد و main را خودکار ادغام نکن.
