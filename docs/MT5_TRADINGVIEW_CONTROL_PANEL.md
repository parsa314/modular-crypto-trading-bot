# TradingView + MetaTrader 5 DEMO Control Panel

## هدف

این پنل برای استفاده عملی از مسیر زیر ساخته شده است:

    TradingView -> HTTPS Webhook -> Python Bridge -> MetaTrader 5 DEMO

هدف، تست Forward، مستندسازی پایان‌نامه، بررسی اجرای استراتژی و جمع‌آوری تجربه برای Shadow Learning است. حساب واقعی MT5 توسط کد رد می‌شود.

## اجرای یک‌کلیکی در Windows

در ریشه مخزن روی فایل زیر دوبار کلیک کنید:

    START_TRADINGVIEW_MT5_DEMO_PANEL.bat

این فایل:

1. Python را پیدا می‌کند.
2. در صورت نیاز محیط مجازی پروژه را می‌سازد.
3. پروژه و Python integration متاتریدر را نصب می‌کند.
4. در صورت نبود cloudflared، با اجازه خود کاربر پیشنهاد نصب می‌دهد.
5. پنل را روی آدرس زیر اجرا می‌کند و مرورگر را باز می‌کند:

    http://127.0.0.1:8000/ui

پنل مدیریت عمداً فقط از localhost قابل دسترسی است.

## بخش اتصال MT5 Demo

در پنل این موارد را وارد کنید:

- Demo Login
- Demo Password
- نام دقیق Demo Server
- مسیر terminal64.exe
- حداکثر notional
- حداکثر spread
- لیست symbolهای canonical
- mapping بین symbol پروژه و symbol بروکر

نمونه mapping:

    {
      "BTC/USDT": "BTCUSD",
      "ETH/USDT": "ETHUSD",
      "SOL/USDT": "SOLUSD",
      "XRP/USDT": "XRPUSD",
      "DOGE/USDT": "DOGEUSD"
    }

نام دقیق symbol به بروکر بستگی دارد. با بخش «جستجوی نمادهای بروکر» می‌توان نام‌های واقعی را پیدا کرد.

Password در فایل پروژه یا GitHub ذخیره نمی‌شود؛ فقط برای login همان session استفاده می‌شود.

## Dry-Run و Demo Submission

پس از اتصال، حالت پیش‌فرض DRY RUN است. در این حالت TradingView signal می‌تواند دریافت و ثبت شود ولی order_send اجرا نمی‌شود.

بعد از تست Webhook، Symbol Mapping، Spread، Stop/Target و Journal می‌توان از داخل پنل «فعال‌سازی سفارش Demo» را زد. فعال‌سازی دوباره بررسی می‌کند حساب هنوز DEMO است.

## اتصال TradingView

برای TradingView یک URL عمومی HTTPS لازم است.

### روش سریع برای DEMO

در پنل روی «ساخت لینک عمومی موقت» کلیک کنید.

اگر cloudflared نصب باشد، یک Cloudflare Quick Tunnel ساخته می‌شود و URL مشابه زیر ایجاد می‌شود:

    https://example-random.trycloudflare.com

سپس پنل Webhook URL کامل را می‌سازد:

    https://example-random.trycloudflare.com/webhooks/tradingview/<ROUTE_TOKEN>

این URL را در TradingView Alert -> Webhook URL وارد کنید.

Quick Tunnel برای تست و توسعه است؛ URL آن با restart تغییر می‌کند. برای اجرای پایدار از Cloudflare Tunnel با hostname ثابت استفاده کنید.

## TradingView Pine

فایل تست transport:

    tradingview/mt5_demo_bridge_test.pine

این فایل strategy نهایی پایان‌نامه نیست؛ فقط مسیر Alert/Webhook را تست می‌کند.

## امنیت

لایه‌های فعلی:

- MT5 real account refusal
- local-only admin UI
- random route token
- TradingView source IP allowlist
- trusted proxy headers فقط زمانی که peer محلی باشد
- duplicate event journal
- max notional
- spread guard
- broker volume constraints
- server-side stop
- order_check before order_send
- Demo submission defaults OFF

اطلاعات login/password را داخل TradingView Alert JSON قرار ندهید.

## مانیتورینگ

پنل وضعیت اتصال MT5، login/server/balance/equity حساب Demo، وضعیت Demo submission، وضعیت Tunnel، Webhook URL، broker symbols و eventهای TradingView را نمایش می‌دهد.

## محدودیت فعلی

نسخه فعلی BUY/SELL entry با server-side SL و optional TP را پشتیبانی می‌کند.

CLOSE و REDUCE هنوز generic اجرا نمی‌شوند؛ چون در حساب‌های MT5 hedging و netting semantics متفاوت است. قبل از اضافه شدن CLOSE باید ticket/magic-aware reconciliation تکمیل شود.

## مرز علمی

نتایج MT5 broker/CFD با نتایج Spot/Perpetual صرافی یکی در نظر گرفته نمی‌شوند.

    MT5 Demo = execution / forward validation domain
    Exchange = canonical production / exchange research domain


## اجرای مستقیم Strategy روی MT5 Demo — بدون TradingView

بعد از اتصال حساب MT5 Demo در پنل، بخش «اجرای مستقیم استراتژی ربات روی MT5 Demo» قابل استفاده است.

این مسیر:

    MT5 closed bars
        -> Strategy Registry
        -> ATR / Stop / Target
        -> Risk sizing
        -> Spread / broker checks
        -> persistent intent journal
        -> MT5 DEMO order_send

است و به TradingView وابسته نیست.

قواعد مهم:

- فقط کندل کامل‌شده خوانده می‌شود؛ کندل در حال تشکیل وارد Strategy نمی‌شود.
- ریسک پیش‌فرض هر معامله 0.25% است.
- Notional علاوه بر Risk sizing با سقف MT5 adapter محدود می‌شود.
- اگر Bot با magic خودش روی همان symbol پوزیشن باز داشته باشد، ورود جدید رد می‌شود.
- SL سمت broker اجباری است و TP بر اساس RR همان Strategy ساخته می‌شود.
- Intent قبل از network submission روی دیسک ثبت می‌شود تا restart باعث تکرار همان signal نشود.
- حساب Real همچنان توسط MT5DemoExecutor رد می‌شود.

Strategyهای قابل انتخاب از همان \`STRATEGY_REGISTRY\` پروژه می‌آیند؛ از جمله:

    H4_S6_BREAKOUT
    H1_ICHIMOKU_PULLBACK
    M15_CONFIRMED_ORDER_BLOCK
    M15_SILVER_BULLET
    M5_UNICORN
    M1_FVG_RETRACE

لیست دقیق را خود پنل از backend می‌خواند.

### Headless / VPS

برای اجرای بدون مرورگر:

    python scripts/run_mt5_direct_demo_strategy.py --list-strategies

Dry-run مستقیم:

    python scripts/run_mt5_direct_demo_strategy.py ^
      --canonical-symbol BTC/USDT ^
      --venue-symbol BTCUSD ^
      --strategy H4_S6_BREAKOUT

برای ارسال سفارش DEMO دو opt-in لازم است:

    set MT5_DEMO_SUBMIT_ENABLED=1

و:

    python scripts/run_mt5_direct_demo_strategy.py ... --submit-demo

Credentials از متغیرهای زیر خوانده می‌شوند یا password به صورت interactive prompt گرفته می‌شود:

    MT5_LOGIN
    MT5_PASSWORD
    MT5_SERVER
    MT5_TERMINAL_PATH

هیچ‌کدام نباید داخل Git commit شوند.



## استراتژی پیش‌فرض Demo: H4_V59_CONFLUENCE_DEMO

پنل و Headless Runner اکنون به صورت پیش‌فرض از این Strategy استفاده می‌کنند.

### لایه 1 — Ichimoku

Long:

- قیمت بالای cloud
- Tenkan بالای Kijun
- Kijun slope مثبت

Short معکوس همین شروط است.

### لایه 2 — ICT / SMC

رأی ساختاری از یکی از این خانواده‌ها ساخته می‌شود:

- liquidity sweep -> BOS
- FVG recent + midpoint rejection
- BOS recent + Order Block mitigation/rejection

### لایه 3 — Al Brooks / Price Action proxy

ویژگی‌ها کاملاً الگوریتمی و causal هستند:

- body ratio
- close location
- bar overlap
- EMA20/EMA50 trend strength normalized by ATR
- breakout strength normalized by ATR
- signal-bar quality
- follow-through
- three-bar micro-channel proxy

این پیاده‌سازی یک proxy پژوهشی از مفاهیم Price Action است و ادعا نمی‌کند discretionary chart reading آل بروکس را عیناً بازسازی می‌کند.

### لایه 4 — Regime / Trend

- price vs EMA200
- EMA200 slope
- EMA20 vs EMA50

### لایه 5 — S6 / Breakout

- close بالاتر از previous 20-bar high برای Long
- close پایین‌تر از previous 20-bar low برای Short
- EMA200 slope هم‌جهت

### قانون Confluence

برای ورود:

    directional_score >= 3

و همچنین:

    directional_score - opposite_score >= 2

بنابراین حالت 3-vs-2 معامله نمی‌شود.

### AI Confirmation Gate

AI فقط بعد از تشکیل Confluence signal اجرا می‌شود.

مدل Demo:

    HistGradientBoostingClassifier

قواعد causal:

- latest closed bar هرگز target آموزشی ندارد.
- فقط rowهایی که next-bar outcome آنها در decision time معلوم است وارد آموزش می‌شوند.
- train/validation chronological است.
- validation Brier محاسبه می‌شود.
- اگر Brier از سقف عبور کند، معامله رد می‌شود.
- Long پیش‌فرض به probability_up >= 0.56 نیاز دارد.
- Short پیش‌فرض به probability_up <= 0.44 نیاز دارد.
- hurdle پیش‌فرض 24 bps است.
- fail-closed است: insufficient history / class collapse / bad validation => NO ORDER.

### Risk / Execution

پس از تأیید AI:

- risk per trade پیش‌فرض 0.25%
- stop = 1.5 ATR برای H4 V59
- target = 2.5R
- max-notional guard
- spread guard
- one bot-owned position per symbol
- persistent signal idempotency
- order_check قبل از order_send
- broker-side SL/TP
- real-money MT5 account refusal

### مسیر نهایی

    MT5 closed H4 bars
      -> V59 Confluence
      -> AI Confirmation
      -> Position / Spread / Risk gates
      -> Persistent INTENT
      -> MT5 order_check
      -> MT5 DEMO order_send
      -> Execution Journal
      -> Shadow-learning evidence

