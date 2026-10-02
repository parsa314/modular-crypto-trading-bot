# V58 — 10 Combined Trading Hypotheses

این ده روش به‌عنوان **فرضیه‌های معاملاتی از پیش ثبت‌شده** تعریف شده‌اند، نه «استراتژی‌های اثبات‌شده سودده».

هر روش از چهار خانواده استفاده می‌کند:

- Ichimoku
- ICT
- SMC
- Al Brooks-inspired price action

## قانون ورود مشترک

سیگنال فقط در پایان کندل بسته‌شده ساخته می‌شود. ورود اولیه در V58 در **Open کندل بعدی** انجام می‌شود؛ بنابراین همان کندلی که شرایط را ایجاد کرده نمی‌تواند با قیمت Close به‌صورت غیرواقعی معامله شود.

## قانون خروج مرحله اول

برای مقایسه علمی کیفیت Signal، تست اولیه هر ده روش با Barrier مشترک V58 انجام می‌شود:

- Stop = 1 ATR
- Target = 1.5R
- Horizon = 12 bars
- Intrabar ambiguity = STOP_FIRST

این کار جلوی این را می‌گیرد که برای هر Strategy بعد از دیدن Backtest یک Exit سفارشی ساخته شود.

بعد از مقایسه اولیه، Candidate Exit هر Strategy که از قبل در Registry فریز شده است به‌صورت **Ablation جداگانه** تست می‌شود.

## ده فرضیه

1. **C10_01 Trend Pullback Rejection**  
   روند بالای Cloud + TK bullish + MSS/FVG اخیر + Brooks pullback signal.  
   Candidate exit: 1.0 ATR / 1.75R / 12 bars.

2. **C10_02 Liquidity Sweep Reversal**  
   Sweep نقدینگی + reclaim/MSS + بازپس‌گیری Kijun + failed breakout.  
   Candidate exit: 1.1 ATR / 2.0R / 10 bars.

3. **C10_03 Cloud Break FVG Continuation**  
   شکست تازه Kumo + displacement/FVG + structure break + follow-through.  
   Candidate exit: 1.0 ATR / 2.0R / 12 bars.

4. **C10_04 Kijun Pullback H2**  
   Pullback نزدیک Kijun در روند مثبت + structure hold + FVG اخیر + H2-style proxy.  
   Candidate exit: 0.9 ATR / 1.5R / 8 bars.

5. **C10_05 Range Edge Failed Breakout**  
   بازار Range + Sweep لبه Range + Cloud فشرده + failed breakout.  
   Candidate exit: 0.8 ATR / 1.5R / 8 bars.

6. **C10_06 Microchannel FVG Re-entry**  
   microchannel صعودی + Ichimoku trend + FVG + pullback کنترل‌شده.  
   Candidate exit: 0.9 ATR / 1.8R / 10 bars.

7. **C10_07 Squeeze Expansion Breakout**  
   فشردگی Range/Kumo + نزدیکی Liquidity + displacement + breakout follow-through.  
   Candidate exit: 1.2 ATR / 2.2R / 12 bars.

8. **C10_08 Deep Pullback Reversal**  
   Pullback عمیق در روند + sweep/MSS recovery + Kijun support + Brooks reversal.  
   Candidate exit: 1.2 ATR / 2.0R / 14 bars.

9. **C10_09 Regime-Aligned Continuation**  
   slow trend مثبت + Ichimoku alignment + SMC structure + Brooks continuation.  
   Candidate exit: 1.0 ATR / 1.8R / 12 bars.

10. **C10_10 Max Confluence Selective**  
    نسخه کم‌تعداد و سخت‌گیرانه که هم‌زمان trend قوی Ichimoku، displacement/FVG، MSS و Brooks strong signal/follow-through می‌خواهد.  
    Candidate exit: 1.0 ATR / 2.5R / 16 bars.

## Financial Constitution

هیچ Strategy مستقیماً مجاز به تخصیص سرمایه نیست.

پس از تولید Candidate:

```text
Strategy Signal
    ↓
AI Probability / Calibration / Uncertainty
    ↓
Economic Utility + Cost Gate
    ↓
Finance-AI Handoff
    ↓
Independent Financial Risk Veto
    ↓
Shared-Capital Portfolio Simulator
```

قوانین اجباری فعلی:

- risk per trade <= 0.25%
- max asset weight <= 35%
- max gross exposure <= 70%
- drawdown kill = 5%
- CVaR95 <= 3.5%
- shared cash
- no future financing
- fees/slippage/cost stress
- AI cannot size
- Finance cannot resurrect an abstained signal
- PAPER/LIVE remain disabled

## برنامه تست

ابتدا هر ده Strategy جداگانه و سپس در Portfolio مشترک گزارش شوند:

- Event count
- TP / SL / TIMEOUT prevalence
- Net return after 0/24/36/50 bps
- Sharpe / Sortino
- MDD / Calmar
- Profit Factor / Expectancy
- Coverage / Abstention
- Brier / LogLoss after AI layer
- performance by regime
- performance by asset
- moving-block bootstrap CI
- walk-forward stability
- strategy-only vs AI+Finance vs no-trade

هر Strategy که فقط روی یک Asset، یک Fold یا هزینه صفر نتیجه مثبت داشته باشد نباید Promotion شود.
