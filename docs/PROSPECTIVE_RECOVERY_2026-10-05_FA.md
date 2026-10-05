# اصلاح شواهد Forward — ۵ اکتبر ۲۰۲۶

این تغییر اصلاح مهندسی و ثبت شکست زیرساخت است؛ نتیجهٔ سودآوری یا مجوز معامله نیست.
شاخهٔ main در ممیزی: `876e229a8d3e73f2e9d646d4126c8ce08e4c11b4`.

## شواهد تازه

| مسیر | شاهد مستقل | نتیجه |
|---|---|---|
| V15 | [Run 37242622172](https://github.com/parsa314/modular-crypto-trading-bot/actions/runs/37242622172) | observations پاسخ 404؛ upload skipped |
| Phase-Q | [Run 37237008965](https://github.com/parsa314/modular-crypto-trading-bot/actions/runs/37237008965) | 142 artifact، صفر دانلود، 142 خطا؛ manifest منتشر نشده |
| V25 | [Run 37243266965](https://github.com/parsa314/modular-crypto-trading-bot/actions/runs/37243266965) | فاصلهٔ 177.209722 ساعت در مقابل حد ثابت 5.5 |
| V19 | [Run 37253489636](https://github.com/parsa314/modular-crypto-trading-bot/actions/runs/37253489636) | artifact `11321114955` قابل دریافت؛ digest رسمی `sha256:c02c2ab5a81ba8f4d262ba7e345b305a29d3ed146c3b8e68dd1facdd73663310` |

پیش‌بررسی HTTP مستقل در `2026-10-05T05:30:30Z` برای URL تاریخی Railway
حتی در health پاسخ 404 داد. وضعیت فعلی **BLOCKED_DEPLOYMENT_CONTRACT** است.
بازیابی deployment از روی گزارش قدیمی موفق فرض نشده است.

## اصلاحات

* V15 ابتدا health/research/paper contract را بررسی می‌کند. اگر سرویس صریحاً
  research-only و paper-off باشد، مسیر قدیمی deprecated است. endpoint مفقود
  خطای زیرساخت است. هیچ‌کدام snapshot اقتصادی قابل شمارش یا بازده صفر جعلی ندارند.
* پاسخ دائمی 404/403/423 retry نمی‌شود؛ خروجی پیشین بازنویسی نمی‌شود.
* Phase-Q redirect را صریح دنبال می‌کند؛ Bearer به storage خارجی نمی‌رود.
  digest رسمی ZIP، مسیر مخزن، یکتایی نام JSON و سقف حجم بازشده کنترل می‌شوند.
  خطاها signed URL و credential ندارند.
* Workflow با `always()` گزارش و manifest شکست را نیز ذخیره می‌کند.
  حد پوشش و gate آماری تغییر نکرده‌اند.

کد قدیمی با urllib هدر Authorization را در redirect حفظ می‌کرد؛ این نقص
transport مشخص است. علت قطعی هر 142 شکست قدیمی هنوز معلوم نیست، زیرا manifest
آن اجرا upload نشده است. موفقیت harvesting باید در CI با `actions:read` نیز
ثابت شود؛ تست آفلاین جایگزین آن نیست.

## تصمیم علمی V25 و پنجرهٔ بعدی

پنجرهٔ موجود: **BLOCKED_SCHEDULER_CONTINUITY / INVALID_CONTINUITY**.
این وضعیت رد استراتژی یا سود منفی نیست. workflow، collector اصلی
`fac456ccc0eb445ce7f2d8554840f37da9142ac7`، مرز `2026-09-11T16:00:00Z` و حد
5.5 ساعت دست‌نخورده‌اند. دادهٔ از دست رفته backfill نمی‌شود.

ماژول `prospective_protocol` ثبت immutable پنجرهٔ مستقل را فراهم می‌کند:
ثبت قبل از start، SHA غیر-placeholder، bootstrap از start، و failure latch
که با اجرای خوب بعدی پاک نمی‌شود. طرح جدید فقط **OKX swap orderbook quality**
است؛ OHLCV و ارزیابی اقتصادی ندارد و به CoinEx spot final holdout دست نمی‌زند.
ثبت protocol به معنی activation یا maturity نیست. collector و scheduler باید
روی SHA بازبینی‌شده canonical متصل شوند؛ تا آن زمان وضعیت
`PRE_REGISTERED_AWAITING_CANONICAL_SCHEDULER` حفظ می‌شود.

## اجرا و تست

```bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_forward_collectors.py tests/test_prospective_protocol.py \
  tests/test_forward_evidence_v15.py tests/test_phase_q_v20.py \
  tests/test_microstructure_quality_v19.py tests/test_forward_microstructure_v19.py \
  tests/test_trade_timestamp_v19.py
python scripts/export_production_evidence_v15.py --output-dir artifacts/forward-preflight-new
```

آخرین دستور در نبود سرویس exit 2 می‌دهد و status artifact را نگه می‌دارد.
`v15_collection_status.json` عمداً نام و schema snapshot اقتصادی قدیمی را ندارد.

## مبنای علمی و نگارش پایان‌نامه

[Han, Huang & Wang, ICML 2024](https://proceedings.mlr.press/v235/han24b.html)
ارزیابی مدل در temporal distribution shift را بررسی می‌کنند. کاربرد آن ضرورت
تفکیک شواهد تازه و تاریخی است؛ تضمین سود برای مدل این پروژه نیست.
[Hallberg Szabadváry, PMLR 2024](https://proceedings.mlr.press/v230/hallberg-szabadvary24a.html)
ACI چندگامی را بررسی می‌کند؛ فعلاً مسیر پژوهش uncertainty است، نه قابلیت
پیاده‌سازی‌شده یا تضمین پوشش تحت هر نوع shift.
[قرارداد رسمی GitHub artifacts](https://docs.github.com/en/rest/actions/artifacts)
redirect و مجوز read را توضیح می‌دهد.

در فصل چهار شکست deployment، harvesting و scheduler به‌عنوان شکست زیرساخت
ثبت می‌شوند. Holdout آیندهٔ V58 تا پایان ۲۴ دسامبر ۲۰۲۶ و Kraken sealed باقی می‌مانند.
