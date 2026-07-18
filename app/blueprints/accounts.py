import uuid

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.background import submit_job
from app.models import TelegramAccount
from app.services.settings import get_int
from app.services.audit import log_action
from worker_tasks import qr_login_task, sync_groups_task

bp = Blueprint("accounts", __name__, url_prefix="/accounts")


@bp.get("")
@login_required
def index():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id).order_by(TelegramAccount.id.desc()).all()
    return render_template("accounts.html", accounts=accounts)


@bp.post("/add")
@login_required
def add():
    limit = get_int(current_user.id, "MAX_TELEGRAM_ACCOUNTS", current_app.config["MAX_TELEGRAM_ACCOUNTS"])
    if TelegramAccount.query.filter_by(owner_id=current_user.id).count() >= limit:
        return jsonify({"error": f"تم بلوغ الحد الأقصى المضبوط: {limit} حساباً"}), 400
    if not current_app.config["TELEGRAM_API_ID"] or not current_app.config["TELEGRAM_API_HASH"]:
        return jsonify({"error": "أدخل TELEGRAM_API_ID وTELEGRAM_API_HASH في ملف البيئة"}), 400
    account = TelegramAccount(owner_id=current_user.id, status="pending_qr")
    db.session.add(account)
    log_action("telegram_account.created", "telegram_account", account.id)
    db.session.commit()
    token = uuid.uuid4().hex
    submit_job(current_app._get_current_object(), qr_login_task, account.id, token)
    return jsonify({"account_id": account.id, "token": token})


@bp.post("/<int:account_id>/sync")
@login_required
def sync(account_id):
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id).first_or_404()
    log_action("telegram_account.sync_started", "telegram_account", account.id)
    db.session.commit()
    submit_job(current_app._get_current_object(), sync_groups_task, account.id)
    flash("بدأ تحديث المجموعات تلقائياً", "info")
    return redirect(url_for("groups.index", account_id=account.id))


@bp.post("/<int:account_id>/delete")
@login_required
def delete(account_id):
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id).first_or_404()
    log_action("telegram_account.deleted", "telegram_account", account.id)
    db.session.delete(account)
    db.session.commit()
    flash("تم حذف الحساب والجلسة", "success")
    return redirect(url_for("accounts.index"))
