from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.decorators import admin_required
from app.extensions import db
from app.models import User, utcnow
from app.permissions import PERMISSION_SPECS, ROLE_DEFAULTS, ROLE_LABELS
from app.services.audit import log_action

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/users")
@login_required
@admin_required
def users():
    rows = User.query.order_by(User.id.asc()).all()
    return render_template(
        "admin_users.html",
        users=rows,
        permission_specs=PERMISSION_SPECS,
        role_labels=ROLE_LABELS,
        role_defaults=ROLE_DEFAULTS,
    )


@bp.post("/users/create")
@login_required
@admin_required
def create_user():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "operator")
    if role not in ROLE_LABELS:
        role = "operator"
    is_admin = role == "super_admin"
    selected_permissions = request.form.getlist("permissions")
    if not selected_permissions:
        selected_permissions = ROLE_DEFAULTS.get(role, [])

    if not email or "@" not in email:
        flash("أدخل بريداً صحيحاً", "danger")
    elif len(password) < 8:
        flash("كلمة المرور يجب ألا تقل عن 8 أحرف", "danger")
    elif User.query.filter_by(email=email).first():
        flash("البريد مستخدم بالفعل", "danger")
    else:
        user = User(email=email, is_admin=is_admin, role=role, is_active=True)
        user.set_password(password)
        user.set_permissions(selected_permissions)
        db.session.add(user)
        db.session.flush()
        log_action("admin.user_created", "user", user.id, details=f"role={role}", owner_id=current_user.id)
        db.session.commit()
        flash("تم إنشاء المستخدم وتطبيق الصلاحيات", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/password")
@login_required
@admin_required
def reset_password(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("المستخدم غير موجود", "danger")
        return redirect(url_for("admin.users"))
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("كلمة المرور يجب ألا تقل عن 8 أحرف", "danger")
    else:
        user.set_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        log_action("admin.password_reset", "user", user.id, owner_id=current_user.id)
        db.session.commit()
        flash("تم تغيير كلمة المرور", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/permissions")
@login_required
@admin_required
def update_permissions(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("المستخدم غير موجود", "danger")
        return redirect(url_for("admin.users"))
    role = request.form.get("role", user.role or "operator")
    if role not in ROLE_LABELS:
        role = "operator"
    user.role = role
    user.is_admin = role == "super_admin"
    user.set_permissions(request.form.getlist("permissions"))
    log_action("admin.permissions_updated", "user", user.id, details=f"role={role}", owner_id=current_user.id)
    db.session.commit()
    flash("تم تحديث الدور والصلاحيات", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/status")
@login_required
@admin_required
def update_status(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("المستخدم غير موجود", "danger")
        return redirect(url_for("admin.users"))
    if user.id == current_user.id:
        flash("لا يمكن تعطيل حسابك الحالي", "warning")
        return redirect(url_for("admin.users"))
    action = request.form.get("action")
    if action == "disable":
        user.is_active = False
        log_action("admin.user_disabled", "user", user.id, owner_id=current_user.id)
        flash("تم تعطيل المستخدم", "success")
    elif action == "enable":
        user.is_active = True
        user.failed_login_count = 0
        user.locked_until = None
        log_action("admin.user_enabled", "user", user.id, owner_id=current_user.id)
        flash("تم تفعيل المستخدم", "success")
    else:
        flash("إجراء غير معروف", "danger")
    db.session.commit()
    return redirect(url_for("admin.users"))
