from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from app.extensions import db
from app.models import User, utcnow
from app.services.audit import log_action

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "1"
        user = User.query.filter_by(email=email).first()
        now = utcnow()
        if user and user.locked_until and user.locked_until > now:
            flash("تم قفل الحساب مؤقتاً بسبب محاولات دخول فاشلة. جرّب لاحقاً.", "danger")
            return render_template("login.html")
        if user and user.check_password(password):
            if not user.is_active:
                flash("هذا المستخدم معطل من لوحة الإدارة", "danger")
                return render_template("login.html")
            user.failed_login_count = 0
            user.locked_until = None
            user.last_login_at = now
            db.session.commit()
            login_user(user, remember=remember)
            log_action("auth.login_success", "user", user.id, owner_id=user.id)
            db.session.commit()
            next_url = request.args.get("next")
            return redirect(next_url or url_for("main.dashboard"))
        if user:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            user.last_failed_login_at = now
            if user.failed_login_count >= 5:
                user.locked_until = now + timedelta(minutes=15)
            db.session.commit()
        flash("بيانات الدخول غير صحيحة", "danger")
    return render_template("login.html")


@bp.get("/register")
def register_disabled():
    flash("إنشاء المستخدمين متاح للمدير فقط", "warning")
    return redirect(url_for("auth.login"))


@bp.post("/logout")
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
