from __future__ import annotations

PERMISSION_SPECS = [
    ("telegram.accounts", "حسابات Telegram", "إضافة الحسابات، QR، المزامنة، عرض الجروبات"),
    ("campaigns.manage", "الحملات والمهام", "إنشاء الحملات، الإيقاف، الاستئناف، الإلغاء والتقارير الخاصة بها"),
    ("content.manage", "المحتوى الإعلاني", "إنشاء وتعديل المحتوى، الوسائط، جهات الاتصال والإرسال التجريبي"),
    ("channel.manage", "إدارة القناة", "إعدادات القناة، القوالب، المنشورات والفهرس"),
    ("search.manage", "البحث والاستكشاف", "البحث العام/الداخلي، تصدير الروابط واستيرادها"),
    ("join.manage", "مدير الانضمام", "مصادر الروابط، الفحص، الدفعات والانضمام متعدد الحسابات"),
    ("links.manage", "مدير الروابط", "إضافة وتنظيم الروابط المحفوظة"),
    ("reports.view", "التقارير", "عرض الإحصائيات والسجلات التشغيلية"),
    ("operations.manage", "التشغيل والإنتاج", "صحة الحسابات، التحليلات، الجدولة، النسخ الاحتياطي والتصدير"),
    ("settings.manage", "الإعدادات", "تعديل إعدادات التشغيل الخاصة بالمستخدم"),
    ("audit.view", "سجل التدقيق", "عرض سجل الإجراءات الخاص بالمستخدم"),
    ("users.manage", "إدارة المستخدمين", "إنشاء المستخدمين، تعطيلهم، تغيير كلمات المرور والصلاحيات"),
]

PERMISSION_LABELS = {key: label for key, label, _ in PERMISSION_SPECS}
ALL_PERMISSIONS = [key for key, _, _ in PERMISSION_SPECS]
PERMISSION_SPECS.insert(
    10,
    ("whatsapp.extract", "استخراج قروبات واتساب", "استخراج روابط دعوة قروبات واتساب من القروبات والقنوات وتصديرها إلى PDF أو قناة"),
)
PERMISSION_LABELS["whatsapp.extract"] = "استخراج قروبات واتساب"
ALL_PERMISSIONS.insert(10, "whatsapp.extract")

ROLE_LABELS = {
    "super_admin": "مدير عام",
    "admin": "مدير",
    "operator": "مشغل",
    "viewer": "مشاهد",
}

ROLE_DEFAULTS = {
    "super_admin": ALL_PERMISSIONS,
    "admin": [p for p in ALL_PERMISSIONS if p != "users.manage"],
    "operator": [
        "telegram.accounts",
        "campaigns.manage",
        "content.manage",
        "channel.manage",
        "search.manage",
        "join.manage",
        "whatsapp.extract",
        "links.manage",
        "reports.view",
    ],
    "viewer": ["reports.view", "audit.view"],
}

BLUEPRINT_PERMISSIONS = {
    "accounts": "telegram.accounts",
    "groups": "telegram.accounts",
    "api": "telegram.accounts",
    "messages": "campaigns.manage",
    "content": "content.manage",
    "channel": "channel.manage",
    "search": "search.manage",
    "join_manager": "join.manage",
    "whatsapp": "whatsapp.extract",
    "links": "links.manage",
    "reports": "reports.view",
    "settings": "settings.manage",
    "operations": "operations.manage",
    "activity": "audit.view",
    "admin": "users.manage",
}
