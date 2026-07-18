# v6.0 — Multi-Tenant Security & Permissions

## الجديد

- نظام أدوار وصلاحيات للمستخدمين: مدير عام، مدير، مشغل، مشاهد.
- صفحة مستخدمين وصلاحيات جديدة لإدارة التفعيل والتعطيل وتغيير كلمة المرور.
- عزل عرض القوائم حسب صلاحيات المستخدم.
- حماية مركزية على مستوى Blueprints تمنع فتح صفحات غير مصرح بها حتى لو عرف المستخدم الرابط.
- تحسين صفحة تسجيل الدخول بتصميم جديد، خيار تذكرني، وأيقونة إظهار/إخفاء كلمة المرور.
- قفل مؤقت للحساب لمدة 15 دقيقة بعد 5 محاولات دخول فاشلة.
- تسجيل تدقيق لعمليات الدخول، إنشاء المستخدمين، تغيير كلمات المرور، وتعديل الصلاحيات.
- ترحيل تلقائي لأعمدة المستخدمين القديمة عند تشغيل النسخة فوق قاعدة بيانات موجودة.

## الترقية

انسخ من نسختك السابقة:

```text
.env
instance/app.db
instance/uploads
```

ثم شغّل:

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py
```

لا تغيّر `SESSION_ENCRYPTION_KEYS` حتى تبقى جلسات Telegram القديمة تعمل.
