from __future__ import annotations

PERMISSION_SPECS = [
    ("telegram.accounts", "حسابات Telegram", "إضافة الحسابات، تسجيل QR، المزامنة، وعرض القروبات."),
    ("campaigns.manage", "الحملات والمهام", "إنشاء الحملات وإيقافها واستئنافها ومراجعة تقاريرها."),
    ("content.manage", "المحتوى الإعلاني", "إنشاء المحتوى والوسائط وجهات الاتصال والإرسال التجريبي."),
    ("channel.manage", "إدارة القناة", "إعدادات القناة والقوالب والمنشورات والفهرس."),
    ("search.manage", "البحث والاستكشاف", "البحث العام والداخلي وتصدير الروابط واستيرادها."),
    ("join.manage", "مدير الانضمام", "مصادر الروابط والفحص والدفعات والانضمام متعدد الحسابات."),
    ("whatsapp.extract", "قروبات واتساب", "استخراج روابط دعوة قروبات واتساب من القروبات والقنوات وتصديرها."),
    ("links.manage", "مدير الروابط", "إضافة وتنظيم الروابط المحفوظة."),
    ("reports.view", "التقارير", "عرض الإحصائيات والسجلات التشغيلية."),
    ("operations.manage", "التشغيل والإنتاج", "صحة الحسابات والتحليلات والجدولة والنسخ الاحتياطي."),
    ("settings.manage", "الإعدادات", "تعديل إعدادات التشغيل الخاصة بالمستخدم."),
    ("audit.view", "سجل التدقيق", "عرض سجل الإجراءات الخاص بالمستخدم."),
    ("users.manage", "إدارة المستخدمين", "إنشاء المستخدمين وتعطيلهم وتغيير كلمات المرور والصلاحيات."),
]

PERMISSION_GROUPS = [
    ("الحسابات", ["telegram.accounts"]),
    ("الاكتشاف والروابط", ["search.manage", "join.manage", "whatsapp.extract", "links.manage"]),
    ("النشر", ["campaigns.manage", "content.manage", "channel.manage"]),
    ("النظام والإدارة", ["reports.view", "operations.manage", "settings.manage", "audit.view", "users.manage"]),
]

PERMISSION_LABELS = {key: label for key, label, _ in PERMISSION_SPECS}
PERMISSION_DESCRIPTIONS = {key: desc for key, _, desc in PERMISSION_SPECS}
ALL_PERMISSIONS = [key for key, _, _ in PERMISSION_SPECS]

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


def grouped_permission_specs():
    specs = {key: (key, label, desc) for key, label, desc in PERMISSION_SPECS}
    groups = []
    used = set()
    for group_label, keys in PERMISSION_GROUPS:
        rows = [specs[key] for key in keys if key in specs]
        if rows:
            groups.append((group_label, rows))
            used.update(key for key, _, _ in rows)
    remaining = [row for row in PERMISSION_SPECS if row[0] not in used]
    if remaining:
        groups.append(("أخرى", remaining))
    return groups
