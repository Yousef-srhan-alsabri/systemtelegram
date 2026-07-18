
from __future__ import annotations

from flask import request
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog


def log_action(action: str, entity_type: str | None = None, entity_id=None, details: str | None = None, owner_id: int | None = None):
    try:
        user_id = current_user.id if getattr(current_user, "is_authenticated", False) else None
        owner = owner_id or user_id or 0
        row = AuditLog(
            owner_id=owner,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details,
            ip_address=request.headers.get("X-Forwarded-For", request.remote_addr) if request else None,
            user_agent=(request.headers.get("User-Agent", "")[:500] if request else None),
        )
        db.session.add(row)
    except Exception:
        pass
