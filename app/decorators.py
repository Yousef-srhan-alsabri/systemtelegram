from functools import wraps

from flask import abort
from flask_login import current_user


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.has_permission("users.manage"):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def permission_required(permission: str):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not current_user.has_permission(permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
