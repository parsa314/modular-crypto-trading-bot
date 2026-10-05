# اصلاح gate شواهد V54 — Issue #78

این patch روی source ثابت `f234a85028ed2d86cd3bc672e7491f72b0245b9b`
ساخته شده و مدل، ویژگی، threshold، کارمزد و پنجرهٔ ارزیابی را تغییر نمی‌دهد.

* experiment باید `V54_REAL_COINEX_FEATURE_AUDIT` باشد.
* requested universe دقیقاً پنج نماد BTC/ETH/SOL/XRP/DOGE در USDT است؛ CLI
  درخواست ناقص یا نماد دیگر را قبل از network رد می‌کند و ترتیب را canonical می‌کند.
* completed symbol خارج از universe یا تکراری پذیرفته نمی‌شود؛ حداقل سه نماد قبلی حفظ شد.
* SHA کد 40 hex و hash داده/schema هرکدام 64 hex غیر-placeholder لازم‌اند.
* protocol/source/symbol/safety manifest و rows نیز کنترل می‌شوند؛ replay هویت
  manifest را به دادهٔ بازخوانی‌شده تطبیق می‌دهد.
* provenance از checkout واقعی گرفته می‌شود؛ `GITHUB_SHA` مربوط به scheduler
  جای SHA علمی collector نمی‌نشیند و `SOURCE_COMMIT` ناسازگار رد می‌شود.
* policy نمی‌تواند چک provenance را خاموش کند؛ execution flags همیشه false هستند.

```bash
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_v54_provenance_gate.py tests/test_v54_integrity.py tests/test_feature_audit_v54.py
```

تأیید محلی: 33 passed. source gateهای جعلی، universe اشتباه، placeholder،
manifest ناامن و تفاوت scheduler/checkout با تست‌های adversarial پوشش داده شدند.
این کار به معنی اجرای دوبارهٔ آزمایش علمی یا رفع اقتصادی نتایج منفی نیست.
پنجره‌های sealed و تمام شواهد پیشین حفظ شده‌اند. برای فصل سه، artifact provenance
و برای فصل چهار، محدودیت اعتبار report قدیمی تا اجرای gate جدید را ثبت کنید.
