# v6.4 — Full Production Suite

هذا الإصدار يدمج إضافات v6.1 + v6.2 + v6.3 في نسخة واحدة فوق v6.0:

- Reports, Analytics & Account Health
- Backup Center
- Scheduler & Channel Calendar
- Production Deployment & Hardening
- استمرار عزل بيانات المستخدمين والصلاحيات من v6.0

## الصفحات الجديدة

- التشغيل والإنتاج `/operations`
- صحة الحسابات `/operations/account-health`
- التحليلات `/operations/analytics`
- الجدولة والتقويم `/operations/scheduler`
- النسخ الاحتياطي `/operations/backups`

## الجدولة

يمكن جدولة حملة من صفحة إنشاء حملة أو من مركز الجدولة. ويمكن جدولة منشور قناة من صفحة تفاصيل المنشور أو من مركز الجدولة.

لتشغيل الجدولة بشكل دائم محلياً:

```powershell
python scheduler_worker.py
```

أو عبر Docker Compose توجد خدمة `scheduler`.

## النسخ الاحتياطي

ينشئ نسخة ZIP تشمل:

- `instance/app.db`
- `instance/uploads`

مهم: ملف `.env` و `SESSION_ENCRYPTION_KEYS` لا يدخلان في النسخة ويجب حفظهما بشكل منفصل.

الاستعادة معطلة افتراضياً. لتفعيلها:

```env
ALLOW_BACKUP_RESTORE=true
```

ثم أعد تشغيل التطبيق.

## الإنتاج

استخدم PostgreSQL في `DATABASE_URL`، وشغّل الخدمات عبر Docker Compose عند الحاجة. لا تستخدم SQLite لمستخدمين كثيرين أو إنتاج عالي الحمل.

