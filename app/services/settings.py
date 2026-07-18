
from __future__ import annotations

from flask import current_app

from app.extensions import db
from app.models import AppSetting

SETTING_SPECS = {
    "MAX_TELEGRAM_ACCOUNTS": {"label": "حد حسابات تيليجرام", "type": "int", "default": "50", "group": "telegram"},
    "MESSAGE_DELAY_MIN_SECONDS": {"label": "أقل تأخير بين الرسائل", "type": "int", "default": "20", "group": "telegram"},
    "MESSAGE_DELAY_MAX_SECONDS": {"label": "أعلى تأخير بين الرسائل", "type": "int", "default": "30", "group": "telegram"},
    "DEFAULT_BATCH_SIZE": {"label": "حجم الدفعة الافتراضي", "type": "int", "default": "30", "group": "telegram"},
    "MAX_TARGETS_PER_ACCOUNT_TASK": {"label": "أقصى أهداف لكل حساب", "type": "int", "default": "500", "group": "telegram"},
    "FORWARD_SOURCE_REF": {"label": "مصدر تحويل آخر رسالة", "type": "string", "default": "", "group": "telegram"},
    "CAMPAIGN_RISK_SENT_LIMIT": {"label": "حد خطر الحساب في الحملة", "type": "int", "default": "120", "group": "telegram"},
    "CAMPAIGN_RISK_FAILURE_LIMIT": {"label": "حد فشل الحساب قبل الإيقاف", "type": "int", "default": "8", "group": "telegram"},
    "SEARCH_DEFAULT_MAX_RESULTS": {"label": "عدد نتائج البحث الافتراضي", "type": "int", "default": "250", "group": "search"},
    "SEARCH_MAX_RESULTS": {"label": "أقصى نتائج للبحث", "type": "int", "default": "1000", "group": "search"},
    "SEARCH_SAUDI_THRESHOLD": {"label": "حد ترجيح السعودية", "type": "int", "default": "25", "group": "search"},
    "SEARCH_GLOBAL_LIMIT": {"label": "حد نتائج البحث العام", "type": "int", "default": "100", "group": "search"},
    "JOIN_MAX_ITEMS_PER_JOB": {"label": "حد روابط الانضمام في المهمة", "type": "int", "default": "10", "group": "join"},
    "JOIN_DELAY_MIN_SECONDS": {"label": "أقل تأخير بين طلبات الانضمام", "type": "int", "default": "15", "group": "join"},
    "JOIN_DELAY_MAX_SECONDS": {"label": "أعلى تأخير بين طلبات الانضمام", "type": "int", "default": "30", "group": "join"},
    "JOIN_SCAN_MESSAGE_LIMIT": {"label": "حد رسائل قناة المصدر للفحص", "type": "int", "default": "1000", "group": "join"},
    "JOIN_CONTINUE_BATCHES": {"label": "الاستمرار تلقائياً للدفعات التالية", "type": "bool", "default": "false", "group": "join"},
    "JOIN_BATCH_PAUSE_SECONDS": {"label": "الانتظار بين دفعات الانضمام بالثواني", "type": "int", "default": "300", "group": "join"},
    "JOIN_MAX_BATCHES_PER_RUN": {"label": "أقصى عدد دفعات متتالية", "type": "int", "default": "5", "group": "join"},
    "JOIN_RESUME_AFTER_FLOODWAIT": {"label": "استئناف الانضمام تلقائياً بعد انتهاء حد تيليجرام", "type": "bool", "default": "true", "group": "join"},
    "JOIN_MAX_FLOODWAIT_SLEEP_SECONDS": {"label": "أقصى انتظار تلقائي لحد تيليجرام بالثواني", "type": "int", "default": "3600", "group": "join"},
    "JOIN_DYNAMIC_MONITOR_SECONDS": {"label": "فاصل مراقبة استئناف الانضمام", "type": "int", "default": "60", "group": "join"},
}


def get_setting(owner_id: int, key: str, default=None):
    row = AppSetting.query.filter_by(owner_id=owner_id, key=key).first()
    if row and row.value not in (None, ""):
        return row.value
    if key in current_app.config:
        return current_app.config[key]
    spec = SETTING_SPECS.get(key)
    if spec:
        return spec["default"]
    return default


def get_int(owner_id: int, key: str, default: int = 0) -> int:
    try:
        return int(get_setting(owner_id, key, default))
    except (TypeError, ValueError):
        return int(default)


def get_bool(owner_id: int, key: str, default: bool = False) -> bool:
    value = get_setting(owner_id, key, "true" if default else "false")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "نعم"}


def set_setting(owner_id: int, key: str, value: str, updated_by: int | None = None):
    spec = SETTING_SPECS.get(key)
    value_type = spec["type"] if spec else "string"
    row = AppSetting.query.filter_by(owner_id=owner_id, key=key).first()
    if not row:
        row = AppSetting(owner_id=owner_id, key=key, value_type=value_type)
        db.session.add(row)
    if value_type == "bool":
        row.value = "true" if str(value).strip().lower() in {"1", "true", "yes", "on", "نعم"} else "false"
    else:
        row.value = str(value).strip()
    row.updated_by = updated_by
    return row


def all_settings(owner_id: int):
    existing = {s.key: s for s in AppSetting.query.filter_by(owner_id=owner_id).all()}
    data = []
    for key, spec in SETTING_SPECS.items():
        row = existing.get(key)
        value = row.value if row else str(current_app.config.get(key, spec["default"]))
        data.append({"key": key, "label": spec["label"], "type": spec["type"], "group": spec["group"], "value": value})
    return data
