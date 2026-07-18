# Telegram Dashboard v6.8 — Railway + PostgreSQL Ready

هذه نسخة مجهزة للرفع على Railway مع PostgreSQL بدل SQLite.

## الخدمات المطلوبة داخل Railway

أنشئ مشروع Railway يحتوي على:

1. **PostgreSQL Database**
2. **Web Service** لتشغيل Flask/Gunicorn
3. **Scheduler Service** لتشغيل `scheduler_worker.py`
4. **Volume** اختياري للملفات المرفوعة على المسار:
   ```text
   /app/instance/uploads
   ```

## أمر تشغيل خدمة الويب

Railway سيقرأ `railway.json`، لكن إذا احتجت ضبطه يدوياً استخدم:

```bash
gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 180
```

## أمر تشغيل خدمة المجدول

أنشئ خدمة ثانية من نفس الريبو، وغير Start Command إلى:

```bash
python scheduler_worker.py
```

هذه الخدمة ضرورية من أجل:

- جدولة الحملات.
- جدولة منشورات القناة.
- استئناف الانضمام بعد FloodWait.
- تشغيل المهام المستحقة تلقائياً.

## متغيرات Railway المطلوبة

انسخ من `.env.railway.example` إلى Variables داخل Railway.

الأهم:

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
SECRET_KEY=...
SESSION_ENCRYPTION_KEYS=نفس_مفتاحك_القديم
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
ADMIN_EMAIL=...
ADMIN_PASSWORD=...
SESSION_COOKIE_SECURE=true
REMEMBER_COOKIE_SECURE=true
APP_TIMEZONE=Asia/Aden
```

## PostgreSQL

تم تعديل التطبيق ليتعامل مع روابط Railway بصيغة:

```text
postgresql://...
postgres://...
```

ويحوّلها داخلياً إلى:

```text
postgresql+psycopg://...
```

لأن المشروع يستخدم `psycopg` v3.

## ملاحظات مهمة

- لا ترفع ملف `.env` إلى GitHub.
- لا ترفع `instance/app.db` إلى GitHub.
- لا تغيّر `SESSION_ENCRYPTION_KEYS` إذا تريد الحفاظ على جلسات Telegram القديمة.
- عند أول تشغيل، التطبيق ينشئ الجداول تلقائياً في PostgreSQL.
- إذا كانت لديك بيانات قديمة في SQLite، استخدم سكربت الترحيل الاختياري أدناه.

## ترحيل اختياري من SQLite إلى PostgreSQL

على جهازك المحلي، بعد وضع رابط PostgreSQL في `DATABASE_URL`:

### Windows PowerShell

```powershell
$env:DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DB"
$env:SQLITE_PATH="instance/app.db"
python tools/migrate_sqlite_to_postgres.py --wipe
```

### Linux/macOS

```bash
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:PORT/DB"
export SQLITE_PATH="instance/app.db"
python tools/migrate_sqlite_to_postgres.py --wipe
```

بعد الترحيل، تأكد أن `SESSION_ENCRYPTION_KEYS` في Railway هو نفس المفتاح القديم.

## فحص الصحة

بعد النشر افتح:

```text
/health
```

يجب أن يرجع:

```json
{"status":"ok","service":"telegram-dashboard","version":"6.8-railway-postgres"}
```
