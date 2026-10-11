# پرامپت اصلاح‌شدهٔ Phase‑1 پژوهشی جمالیان

در مخزن canonical `parsa314/modular-crypto-trading-bot`، AGENTS، README و سند
وضعیت canonical و SHAهای علمی منجمد را بخوان. head مخزن و PR را از GitHub
تأیید کن. تغییر جدید main جای gate علمی را نمی‌گیرد. RESEARCH_ONLY، PAPER_OFF،
LIVE_OFF و sealed Kraken را حفظ کن؛ v17 milestones و آزمایش v0.51 تغییر نکنند.
متن کامل Master Prompt دریافت نشده است؛ انطباق با §14 ادعا نکن.

پنج نوت‌بوک و ZIP ارسالی را ایستا و با hash و محل دقیق یافته بررسی کن.
توصیف «تحویل شد» را وجود کد یا تست تلقی نکن. خروجی خصوصی یا سلول سفارش‌گذاری
را اجرا یا منتشر نکن. Python_1 و داده/تنظیمات غایب را صریح ثبت کن.

ترتیب علمی: Contracts → MA Cross / RSI / RSI+MA → Operational Event Ledger
→ Independent Portfolio Arbiter → ارزیابی اقتصادی Baselineها → سپس پروتکل
جداگانهٔ Grid/Arbitrage. به‌دلیل وجود نمونهٔ مهندسی قدیمی، gate علمی را باز نکن.

قرارداد HypothesisCandidate را مستقل از Decision/SignalEvidence نگه دار؛ در
این مخزن contracts.py فایل است، پس پوشهٔ contracts/hypothesis.py را کپی نکن.
داده، parameters و metadata immutable و detached؛ شناسهٔ قطعی شامل زمان
صریح، پارامتر و provenance. عدد بازده یا confidence از indicator یا حد سود
نساز. Adapter نازک از DecisionEngine موجود استفاده کند و تا نبود کالیبراسیون
مستقل فقط NO_TRADE، confidence=0 و forecast_available=false بسازد.

هر قیمت از candle بسته‌شدهٔ منظم با observed/available واقعی بیاید؛ future،
gap، duplicate، stale، warmup و timezone کنترل شوند. قیمت و حجم و reserve
Decimal باشند. RSI ثابت=۵۰ و روش RSI ثبت شود. زمان execution باید بعد از
تصمیم باشد؛ حد زمانی به‌تنهایی به معنی پرشدن یا صحت execution مدل نیست.

OperationalResearchEventLedger مستقل، experiment namespace و event ID روشن
داشته باشد؛ dedup، retry متعارض، JSON محدود، hash chain، chronology، restart
و append durable آزموده شوند. محدودیت اصالت و suffix truncation ثبت شود.
این دفتر Scientific Milestone Ledger یا شاهد prospective v0.51 نیست.

PortfolioConflictArbiter مستقل فقط feasibility آفلاین بسازد: سرمایهٔ آزاد
پس از رزروهای قبلی، exposure و equity همگی در quote یکسان؛ رتبه‌بندی قبل از
capacity filtering، کنترل سرمایه در هر پذیرش، conflict نماد و ID و state
برگشتی immutable. sizing/fee budget صریح از پروتکل جدا بیاید. admission به
معنی alpha، تأیید Decision یا مجوز execution نیست. policyهای اجرا نشده را
تبلیغ نکن و net-edge دلخواه مبنای رتبه‌بندی قرار نده.

Bitpin فقط public read و opt-in auth-read؛ write پیش از transport مسدود.
timeout و retry محدود، schema fail-closed، redirect و host fallback خاموش.
دامنهٔ رسمی و schema جاری فقط با منبع اصلی و contract check تأیید شوند؛
انتخاب صریح allowlisted host به معنی تأیید API جاری نیست.

پیش از ValidationHarness، داده/بازار/پنجره‌ها/هزینه/خروج/horizon/بودجه و تمام
trials و معیار رد preregister و freeze شوند. fixed-path CPCV با purge/embargo،
CSCV/PBO و DSR diagnostic هستند؛ refit و execution یا promotion را اثبات
نمی‌کنند. dependence با inference بلوکی بررسی شود. دادهٔ مصنوعی فقط smoke
است؛ نبود داده و هزینه را با آمار تصادفی یا ادعای آمادگی پایان‌نامه جبران نکن.

تست سازگاری import، PIT، immutability، replay identity، منع execution،
رزرو تجمیعی و عدم تغییر ماژول‌های منجمد را اجرا کن. نتیجهٔ واقعی و SHA CI
تازه را ثبت کن؛ CI قبلی به head جدید تعمیم داده نشود. تغییر قابل مرور را در
شاخهٔ مستقل و Draft PR ذخیره کن. merger یا فعال‌سازی paper/live انجام نده.
