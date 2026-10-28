# گزارش increment دوم بازیابی — ریسک، خروج و کنترل انتشار

## ۱. هدف فاز

رفع دو گیت پذیرش باز (ریسک ۵٪ و خروج هنگام توقف ورود)، سپس ایجاد ابزار اجرایی بررسی انتشار و برنامه بازگشت مطابق متن کامل فاز ۹. این increment جایگزین تمام فازهای ۲ تا ۱۰ نیست.

## ۲. وضعیت اولیه

main در بررسی تازه GitHub همچنان `7db5643f24bb713ba1df2abb9a2989f73d56a8c6` است. patch محلی قبلی OMS سه آزمون موفق و دو گیت انتشار ناموفق داشت. clone کامل موجود نیست؛ فایل‌های مبنا از SHA ثابت بازیابی شده‌اند. نسخه‌ای با شواهد عملیاتی کافی برای نام‌گذاری «آخرین انتشار سالم» شناسایی نشده است.

## ۳. تغییرات انجام‌شده

- `research_bot/operational_risk.py`: پروفایل opt-in با RiskDecision موجود؛ کنترل ۰٫۲۵٪ stop+cost، توقف ورود روزانه ۲٪ و peak DD برابر ۵٪، سقف وزن/Exposure پس از سفارش، شمارش موقعیت و cooldown؛ کاهش موقعیت فقط با ورودی reconciled، قیمت تازه و held-reserved معتبر.
- `research_bot/orchestrator.py`: factory نام‌دار و اتصال بررسی مستقل سفارش به همان Decision/SignalEvidence و اجرای شبیه‌سازی‌شده موجود. مسیر legacy پژوهش تغییر رفتار ندارد.
- `research_bot/release_control.py`: بررسی مدارک وابسته به SHA/hash؛ رد شواهد synthetic برای گیت‌های خارجی؛ کد خروج غیرصفر برای FAIL/BLOCKED/INCONCLUSIVE/ROLLBACK_REQUIRED؛ سه برنامه بازگشت مستقل. هیچ دستور Git یا کارگزار اجرا نمی‌کند.
- سه فایل unit/acceptance و `tests/checks_pg_recovery.py`: آزمون‌های جدید؛ فایل integration فقط صریح اجرا می‌شود و نبود setup خطاست، نه skip.
- `.github/workflows/recovery-safety.yml`: CI پیشنهادی قابل اجرا روی PR، با PostgreSQL16 موقت و DB به نام recovery_test، ثبت SHA/محیط/لاگ حتی هنگام failure. رمز ثابت آن صرفاً credential مصرف‌شدنی fixture CI است، نه حساب واقعی. فایل هنوز به GitHub ارسال نشده و workflow فعال نشده است.
- artifactهای audit/research/operations/release و ADR به‌روزرسانی شدند. خروجی‌های نسخه اول و نتایج منفی حفظ شدند.

## ۴. شواهد اجرا

```bash
# فقط در محیط snapshot محلی؛ clone کامل به PYTHONPATH اضافی نیاز ندارد.
PYTHONPATH=tests:/workspace/scratch/recovery-audit-20261028/sources/7db5643f24bb713ba1df2abb9a2989f73d56a8c6 \
python -m unittest test_recovery_acceptance test_operational_risk test_release_control -v
```

نتیجه نهایی: **۲۳ آزمون موفق، صفر failure و صفر skip، exit=0**. ورودی‌ها مصنوعی و مختص آزمون نرم‌افزارند. برچسب آن‌ها NON-PROSPECTIVE است.

آزمون integration در همین محیط با `python -m unittest discover -s tests -p checks_pg_recovery.py -v` تلاش شد: **صفر آزمون اجراشده، یک خطای setup، exit=5**؛ پایگاه محلی recovery_test و psycopg در دسترس نیستند. دسترسی Docker socket نیز با `operation not permitted` شکست خورد. این نتیجه BLOCKED زیرساختی است، نه FAIL علمی یا PASS PostgreSQL.

Python AST فایل‌های تغییرکرده و YAML workflow بررسی شدند. CI واقعی و مجموعه کامل pytest اجرا نشده‌اند. baseline و logهای محلی در `/workspace/scratch/recovery-stage2-20261028/` موجودند. گزارش نهایی در `artifacts/recovery_20261028_final/` است.

## ۵. نتیجه علمی

هیچ فرضیه یا مدل جدید آزمایش/ارتقا نیافت. نتیجه v0.50 و وضعیت BLOCKED/SCIENTIFIC_DECISION_DEFERRED برای v0.51 حفظ شدند. آزمایش اقتصادی تازه n=0؛ هزینه، mean_net، PBO و DSR این increment برابر N/A/NOT_RUN هستند. مقایسه‌های ML/RL داخل artifactها صرفاً نقل نتایج تاریخی با SHA اصلی‌اند، نه اعتبارسنجی تازه.

## ۶. نتیجه مهندسی

گیت‌های واحد ریسک، خروج و بررسی انتشار PASS هستند. دو آزمون پذیرش قبلی اکنون به‌صورت صریح مسیر عملیاتی نام‌دار را می‌سنجند؛ آزمون جدا پیش‌فرض legacy را ثابت نگه می‌دارد. آزمون خروج تقویت شده و تأیید RiskDecision و جهت SELL را نیز بررسی می‌کند.

با نبود مدارک PostgreSQL/کارگزار و نبود commit انتشار، CLI دروازه انتشار **BLOCKED، exit=2** می‌دهد. این ابزار validator/plan است؛ وضعیت runtime را تغییر نمی‌دهد و به‌تنهایی ارسال سفارش جدید را متوقف یا سفارش باز را مدیریت نمی‌کند. اتصال actuator و branch protection تحویل نشده است.

## ۷. مشکلات باقی‌مانده

- Git clone و محیط وابستگی/پایگاه کامل در دسترس نیست؛ branch/commit/PR جدید ساخته نشده است. این بخش از الزام Git فاز ۹ هنوز BLOCKED است.
- نبود اتصال واقعی Demo، fill، تطبیق موجودی و drill قطع ارتباط/crash؛ هیچ مشاهده ۲۴ساعته وجود ندارد.
- latch توقف پایدار، day/peak anchor مستقل و پایدار، رزرو اتمیک پرتفوی بین workerها، خروج ۷۲ساعته و Decimal سرتاسری هنوز کامل نیستند.
- اصالت معنایی گزارش کارگزار صرفاً با hash ثابت نمی‌شود؛ منشأ گزارش باید مستقل بررسی شود. validator جایگزین ممیزی مدارک نیست.
- پیش‌نیازهای معماری یکپارچه، آلفای اقتصادی مستقل و ارتقای ML/RL هنوز برقرار نیستند. گزارش شکست‌ها حذف نشده است.

## ۸. تصمیم فاز

`evidence_state=ENGINEERING`.

| دامنه | حکم |
|---|---|
| admission عملیاتی آفلاین و عدم تغییر legacy | PASS |
| منطق دروازه انتشار و برنامه rollback در آزمون واحد | PASS |
| PostgreSQL ایزوله | BLOCKED |
| Git/CI واقعی و rollback actuator | BLOCKED |
| فاز ۶ کامل، فاز ۹ کامل و تحویل عملیاتی فاز ۱۰ | BLOCKED |

LIVE و زمان‌بندی production خاموش مانده‌اند. معاملات واقعی صفر؛ سفارش Demo واقعی صفر. fillهای unit test فقط fixture‌اند.

## ۹. نقطه بازگشت

فایل‌های پیش از تغییر و hash آن‌ها در `/workspace/scratch/recovery-stage2-20261028/baseline/checkpoint.json` ثبت شده‌اند. SHA مبنا فقط **reference علمی/کد** است؛ آخرین SHA عملیاتی سالم `null` باقی می‌ماند. rollback مهندسی بدون هدف سالم و تطبیق موقعیت‌ها BLOCKED است. هیچ reset/revert/merge روی main اجرا نشده است.

## ۱۰. مرحله بعد

گام بعدی واحد: اجرای `checks_pg_recovery.py` و regression OMS در محیط PostgreSQL ایزوله قابل دسترس؛ سپس ساخت branch/commit و بازبینی PR محدود، مطابق مجوزها و گیت‌های قبلی. نتیجه محتمل یا اجرای آینده، PASS گزارش نشده است.

برای بررسی مجدد آمادگی، این فرمان تنها گزارش تولید می‌کند و هیچ ربات یا scheduler را شروع نمی‌کند:

```bash
python -m research_bot.release_control \
  artifacts/recovery_20261028_final/release/input_manifest.json \
  --root artifacts/recovery_20261028_final \
  --source-sha 7db5643f24bb713ba1df2abb9a2989f73d56a8c6 \
  --output artifacts/recovery_20261028_final/release/acceptance_recheck.json
```

در وضعیت حاضر خروج مورد انتظار BLOCKED و exit=2 است. output باید نام تازه داشته باشد؛ گزارش قبلی بازنویسی نمی‌شود. این فرمان «دستور اجرای ربات عملیاتی نهایی» نیست؛ چنین دستوری تا مشاهده Demo و recovery قابل تأیید نیست.
