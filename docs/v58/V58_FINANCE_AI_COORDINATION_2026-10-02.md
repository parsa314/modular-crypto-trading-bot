# V58 Finance ↔ AI Coordination Contract — 2026-10-02

## هدف

در V58 هوش مصنوعی و سیستم مالی دیگر دو بخش جدا و مبهم نیستند. همکاری آن‌ها به‌صورت یک قرارداد مرحله‌ای و قابل ممیزی تعریف شده است:

```text
Market/Event Features
        ↓
AI Forecast
P(TP), P(SL), P(TIMEOUT), entropy, shift
        ↓
Economic Gate
Expected Utility - Cost - Uncertainty
        ↓
Financial Control
Cash / Position Sizing / Exposure / Drawdown / CVaR
        ↓
Independent Risk Veto
        ↓
Research Portfolio Simulator
```

اصل مهم این طراحی این است که **هوش مصنوعی حق کنترل سرمایه را ندارد** و **سیستم مالی نیز حق تغییر خروجی مدل برای نجات یک سیگنال ردشده را ندارد**.

## تقسیم مسئولیت

### 1. AI layer — Forecast only

AI فقط موارد زیر را تولید می‌کند:

- احتمال TP
- احتمال SL
- احتمال TIMEOUT
- entropy
- distribution-shift score

AI اجازه ندارد مقدار سرمایه، notional، quantity، leverage، risk-per-trade، سقف exposure یا دستور سفارش تعیین کند.

### 2. Economic layer — Cost-aware utility

این لایه خروجی AI را با هندسه معامله و هزینه تراکنش ترکیب می‌کند و Expected Utility را می‌سازد. اگر uncertainty، shift، regime confidence یا utility از Gate عبور نکند، نتیجه NO-TRADE/ABSTAIN باقی می‌ماند و هیچ لایه بعدی اجازه احیای آن را ندارد.

### 3. Financial layer — Independent veto and sizing

سیستم مالی تنها بعد از عبور از AI/Economic Gate وارد می‌شود و مسئول موارد زیر است:

- risk budget per trade
- shared cash
- asset concentration
- gross exposure
- transaction fees
- drawdown kill switch
- historical CVaR
- turnover/cash capacity
- final notional sizing

سقف‌های فعلی V58 همچنان حداکثرهای ثابت هستند:

| کنترل | سقف |
|---|---:|
| Risk per trade | 0.25% equity |
| Max asset weight | 35% |
| Max gross exposure | 70% |
| Drawdown kill | 5% |
| CVaR95 | 3.5% |

تنظیمات می‌توانند سخت‌گیرانه‌تر شوند ولی نمی‌توانند این سقف‌ها را شل کنند.

## قانون دو-کلیدی

برای ایجاد exposure جدید هر دو شرط لازم است:

1. AI/Economic gate: `ADMITTED`
2. Financial/Risk gate: `APPROVED`

رد شدن هر کدام مساوی است با NO TRADE.

```text
AI says YES + Finance says NO  => NO TRADE
AI says NO  + Finance says YES => NO TRADE
AI says YES + Finance says YES => eligible for research simulation
```

این معماری مانع می‌شود که Confidence بالای مدل، Drawdown/CVaR/Exposure را دور بزند یا سیستم مالی با تغییر threshold یک مدل ضعیف را به‌صورت مصنوعی سودده نشان دهد.

## Execution firewall

این تغییر فقط معماری پژوهشی و شبیه‌ساز را هماهنگ می‌کند:

```text
PAPER_EXECUTION = FALSE
LIVE_EXECUTION  = FALSE
order_creation_authorized = FALSE
```

هیچ API خصوصی صرافی، سفارش واقعی یا اهرم واقعی با این تغییر فعال نشده است.

## Evidence

در اجرای synthetic integrated V58 برای هر cost scenario یک فایل هماهنگی مستقل ذخیره می‌شود:

```text
finance_ai_coordination_0bps.json
finance_ai_coordination_24bps.json
finance_ai_coordination_36bps.json
finance_ai_coordination_50bps.json
```

این فایل‌ها نشان می‌دهند برای هر event چه چیزی توسط AI پیش‌بینی شده، Economic Gate چه تصمیمی گرفته و Financial Layer چه محدودیت‌هایی را برای مرحله Portfolio اعمال می‌کند.
