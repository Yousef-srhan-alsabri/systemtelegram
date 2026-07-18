from __future__ import annotations

import csv
import io
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app.background import submit_job
from app.extensions import db
from app.models import (
    ChannelPost,
    ChannelSettings,
    DiscoveredJoinLink,
    JoinJob,
    MessageCampaign,
    MessageLog,
    MessageTask,
    SearchJob,
    TelegramAccount,
    TelegramGroup,
    utcnow,
)
from app.services.audit import log_action
from worker_tasks import execute_message_task, publish_channel_post

bp = Blueprint("operations", __name__, url_prefix="/operations")


def _owned_account_ids() -> list[int]:
    return [row[0] for row in TelegramAccount.query.filter_by(owner_id=current_user.id).with_entities(TelegramAccount.id).all()]


def _sqlite_db_path() -> Path:
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if uri.startswith("sqlite:///"):
        raw = uri.replace("sqlite:///", "", 1)
        p = Path(raw)
        if not p.is_absolute():
            p = Path(current_app.instance_path) / raw if raw == "app.db" else Path(current_app.root_path).parent / raw
        return p
    return Path(current_app.instance_path) / "app.db"


def _backups_dir() -> Path:
    p = Path(current_app.instance_path) / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


@bp.get("")
@login_required
def index():
    account_ids = _owned_account_ids()
    tasks_q = MessageTask.query.filter(MessageTask.account_id.in_(account_ids)) if account_ids else MessageTask.query.filter(False)
    sent = MessageLog.query.join(MessageTask, MessageLog.task_id == MessageTask.id).filter(MessageTask.account_id.in_(account_ids), MessageLog.status == "sent").count() if account_ids else 0
    failed = MessageLog.query.join(MessageTask, MessageLog.task_id == MessageTask.id).filter(MessageTask.account_id.in_(account_ids), MessageLog.status == "failed").count() if account_ids else 0
    scheduled_campaigns = MessageCampaign.query.filter_by(owner_id=current_user.id, status="scheduled").count()
    scheduled_posts = ChannelPost.query.filter_by(owner_id=current_user.id, status="scheduled").count()
    summary = {
        "accounts": len(account_ids),
        "campaigns": MessageCampaign.query.filter_by(owner_id=current_user.id).count(),
        "tasks": tasks_q.count(),
        "sent": sent,
        "failed": failed,
        "success_rate": round((sent / (sent + failed) * 100), 1) if (sent + failed) else 0,
        "scheduled_campaigns": scheduled_campaigns,
        "scheduled_posts": scheduled_posts,
        "join_jobs": JoinJob.query.filter_by(owner_id=current_user.id).count(),
        "search_jobs": SearchJob.query.filter_by(owner_id=current_user.id).count(),
    }
    return render_template("operations/index.html", summary=summary)


@bp.get("/account-health")
@login_required
def account_health():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id).order_by(TelegramAccount.id.asc()).all()
    rows = []
    for a in accounts:
        tasks = MessageTask.query.filter_by(account_id=a.id)
        logs = MessageLog.query.join(MessageTask, MessageLog.task_id == MessageTask.id).filter(MessageTask.account_id == a.id)
        sent = logs.filter(MessageLog.status == "sent").count()
        failed = logs.filter(MessageLog.status == "failed").count()
        latest_error = logs.filter(MessageLog.status.in_(["failed", "skipped"])).order_by(MessageLog.id.desc()).first()
        flood = logs.filter(MessageLog.error_text.like("%Flood%")).order_by(MessageLog.id.desc()).first()
        group_count = TelegramGroup.query.filter_by(account_id=a.id).count()
        score = 100
        if a.status != "active":
            score -= 45
        if failed > 0:
            score -= min(30, int((failed / max(sent + failed, 1)) * 100))
        if flood:
            score -= 15
        if group_count == 0:
            score -= 10
        score = max(score, 0)
        label = "سليم" if score >= 80 else "يحتاج انتباه" if score >= 50 else "خطر"
        rows.append({
            "account": a,
            "group_count": group_count,
            "tasks": tasks.count(),
            "sent": sent,
            "failed": failed,
            "success_rate": round((sent / (sent + failed) * 100), 1) if sent + failed else 0,
            "latest_error": latest_error,
            "latest_flood": flood,
            "score": score,
            "label": label,
        })
    return render_template("operations/account_health.html", rows=rows)


@bp.get("/analytics")
@login_required
def analytics():
    account_ids = _owned_account_ids()
    if account_ids:
        logs_q = MessageLog.query.join(MessageTask, MessageLog.task_id == MessageTask.id).filter(MessageTask.account_id.in_(account_ids))
        by_status = dict(logs_q.with_entities(MessageLog.status, func.count(MessageLog.id)).group_by(MessageLog.status).all())
        by_error = logs_q.filter(MessageLog.error_code.isnot(None)).with_entities(MessageLog.error_code, func.count(MessageLog.id)).group_by(MessageLog.error_code).order_by(func.count(MessageLog.id).desc()).limit(12).all()
        top_tasks = MessageTask.query.filter(MessageTask.account_id.in_(account_ids)).order_by(MessageTask.id.desc()).limit(20).all()
    else:
        by_status, by_error, top_tasks = {}, [], []
    join_by_status = dict(JoinJob.query.filter_by(owner_id=current_user.id).with_entities(JoinJob.status, func.count(JoinJob.id)).group_by(JoinJob.status).all())
    search_by_status = dict(SearchJob.query.filter_by(owner_id=current_user.id).with_entities(SearchJob.status, func.count(SearchJob.id)).group_by(SearchJob.status).all())
    return render_template("operations/analytics.html", by_status=by_status, by_error=by_error, top_tasks=top_tasks, join_by_status=join_by_status, search_by_status=search_by_status)


@bp.get("/export/messages.csv")
@login_required
def export_messages_csv():
    account_ids = _owned_account_ids()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["log_id", "task_id", "account_id", "group_title", "status", "telegram_message_id", "error_code", "error_text", "created_at"])
    if account_ids:
        logs = (MessageLog.query.join(MessageTask, MessageLog.task_id == MessageTask.id)
                .filter(MessageTask.account_id.in_(account_ids)).order_by(MessageLog.id.desc()).limit(5000).all())
        for log in logs:
            task = db.session.get(MessageTask, log.task_id)
            writer.writerow([log.id, log.task_id, task.account_id if task else "", log.group.title if log.group else "", log.status, log.telegram_message_id or "", log.error_code or "", log.error_text or "", log.created_at.isoformat() if log.created_at else ""])
    return Response(output.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=message_logs.csv"})


@bp.route("/backups", methods=["GET", "POST"])
@login_required
def backups():
    if request.method == "POST":
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = _backups_dir() / f"telegram-dashboard-backup-{current_user.id}-{ts}.zip"
        db_path = _sqlite_db_path()
        uploads_dir = Path(current_app.instance_path) / "uploads"
        db_uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            if db_uri.startswith("sqlite") and db_path.exists():
                zf.write(db_path, "instance/app.db")
            if uploads_dir.exists():
                for path in uploads_dir.rglob("*"):
                    if path.is_file():
                        zf.write(path, "instance/uploads/" + str(path.relative_to(uploads_dir)))
            if db_uri.startswith("postgresql"):
                readme = (
                    "This Railway/PostgreSQL backup contains uploaded files only.\n"
                    "Use Railway PostgreSQL backups or pg_dump for the database.\n"
                    "Keep .env and SESSION_ENCRYPTION_KEYS separately.\n"
                )
            else:
                readme = "Backup includes SQLite app.db and uploads. Keep .env and SESSION_ENCRYPTION_KEYS separately.\n"
            zf.writestr("BACKUP-README.txt", readme)
        log_action("backup.created", "backup", dest.name)
        flash("تم إنشاء نسخة احتياطية مضغوطة.", "success")
        return redirect(url_for("operations.backups"))
    backups = sorted(_backups_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    restore_enabled = os.getenv("ALLOW_BACKUP_RESTORE", "false").lower() in {"1", "true", "yes", "on"}
    return render_template("operations/backups.html", backups=backups, restore_enabled=restore_enabled)


@bp.get("/backups/<path:filename>")
@login_required
def download_backup(filename):
    path = (_backups_dir() / filename).resolve()
    if not str(path).startswith(str(_backups_dir().resolve())) or not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.post("/backups/restore")
@login_required
def restore_backup():
    if os.getenv("ALLOW_BACKUP_RESTORE", "false").lower() not in {"1", "true", "yes", "on"}:
        abort(403)
    file = request.files.get("backup_file")
    if not file or not file.filename.endswith(".zip"):
        flash("ارفع ملف ZIP صالح.", "danger")
        return redirect(url_for("operations.backups"))
    temp = _backups_dir() / ("restore-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".zip")
    file.save(temp)
    db_path = _sqlite_db_path()
    uploads_dir = Path(current_app.instance_path) / "uploads"
    with zipfile.ZipFile(temp) as zf:
        if "instance/app.db" in zf.namelist():
            zf.extract("instance/app.db", _backups_dir() / "restore_tmp")
            extracted = _backups_dir() / "restore_tmp" / "instance" / "app.db"
            shutil.copy2(extracted, db_path)
        for name in zf.namelist():
            if name.startswith("instance/uploads/") and not name.endswith("/"):
                target = uploads_dir / name.replace("instance/uploads/", "", 1)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    log_action("backup.restored", "backup", temp.name)
    flash("تم الاستيراد. أعد تشغيل التطبيق لضمان تحميل قاعدة البيانات المستعادة.", "warning")
    return redirect(url_for("operations.backups"))


@bp.get("/scheduler")
@login_required
def scheduler():
    campaigns = MessageCampaign.query.filter_by(owner_id=current_user.id).filter(MessageCampaign.status.in_(["scheduled", "queued", "running", "paused"])).order_by(MessageCampaign.id.desc()).limit(50).all()
    posts = ChannelPost.query.filter_by(owner_id=current_user.id).filter(ChannelPost.status.in_(["scheduled", "draft", "publishing"])).order_by(ChannelPost.id.desc()).limit(50).all()
    settings = ChannelSettings.query.filter_by(owner_id=current_user.id).first()
    return render_template("operations/scheduler.html", campaigns=campaigns, posts=posts, settings=settings, now=utcnow())


@bp.post("/scheduler/run-due")
@login_required
def run_due():
    now = utcnow()
    app = current_app._get_current_object()
    account_ids = _owned_account_ids()
    tasks = MessageTask.query.filter(MessageTask.account_id.in_(account_ids), MessageTask.status == "scheduled", MessageTask.schedule_time <= now).all() if account_ids else []
    for task in tasks:
        task.status = "queued"
    posts = ChannelPost.query.filter(ChannelPost.owner_id == current_user.id, ChannelPost.status == "scheduled", ChannelPost.scheduled_at <= now).all()
    for post in posts:
        post.status = "queued"
    db.session.commit()
    for task in tasks:
        submit_job(app, execute_message_task, task.id)
    settings = ChannelSettings.query.filter_by(owner_id=current_user.id).first()
    published_posts = 0
    for post in posts:
        if settings and settings.publisher_account_id and settings.channel_ref:
            submit_job(app, publish_channel_post, post.id, settings.publisher_account_id, settings.channel_ref)
            published_posts += 1
        else:
            post.status = "failed"
            post.last_error = "إعدادات القناة أو حساب النشر غير مكتملة"
            db.session.commit()
    flash(f"تم تشغيل {len(tasks)} مهمة حملة و {published_posts} منشور مجدول مستحق.", "success")
    return redirect(url_for("operations.scheduler"))


def _parse_dt(value: str | None):
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@bp.post("/campaigns/<int:campaign_id>/schedule")
@login_required
def schedule_campaign(campaign_id):
    campaign = MessageCampaign.query.filter_by(id=campaign_id, owner_id=current_user.id).first_or_404()
    scheduled_at = _parse_dt(request.form.get("scheduled_at"))
    if not scheduled_at:
        flash("حدد تاريخ ووقت صحيحين.", "danger")
        return redirect(url_for("operations.scheduler"))
    campaign.scheduled_at = scheduled_at
    campaign.send_window_start = (request.form.get("send_window_start") or "").strip() or None
    campaign.send_window_end = (request.form.get("send_window_end") or "").strip() or None
    campaign.repeat_rule = (request.form.get("repeat_rule") or "none").strip()
    campaign.status = "scheduled"
    for link in campaign.task_links:
        link.task.status = "scheduled"
        link.task.schedule_time = scheduled_at
        link.task.repeat_rule = campaign.repeat_rule or "none"
    db.session.commit()
    log_action("campaign.scheduled", "campaign", campaign.id, details=scheduled_at.isoformat())
    flash("تمت جدولة الحملة. استخدم مشغّل المستحقات من صفحة الجدولة أو شغّل worker دوري لاحقاً.", "success")
    return redirect(url_for("operations.scheduler"))


@bp.post("/channel-posts/<int:post_id>/schedule")
@login_required
def schedule_channel_post(post_id):
    post = ChannelPost.query.filter_by(id=post_id, owner_id=current_user.id).first_or_404()
    scheduled_at = _parse_dt(request.form.get("scheduled_at"))
    if not scheduled_at:
        flash("حدد تاريخ ووقت صحيحين.", "danger")
        return redirect(url_for("operations.scheduler"))
    post.scheduled_at = scheduled_at
    post.auto_pin = bool(request.form.get("auto_pin"))
    post.status = "scheduled"
    db.session.commit()
    log_action("channel_post.scheduled", "channel_post", post.id, details=scheduled_at.isoformat())
    flash("تمت جدولة منشور القناة.", "success")
    return redirect(url_for("operations.scheduler"))
