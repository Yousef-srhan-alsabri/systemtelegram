
from flask import Blueprint, render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from app.models import (
    DiscoveredJoinLink,
    DiscoveredLink,
    JoinJob,
    ManagedLink,
    MessageCampaign,
    MessageLog,
    MessageTask,
    SearchJob,
    TelegramAccount,
    TelegramGroup,
)

bp = Blueprint("reports", __name__, url_prefix="/reports")


@bp.get("")
@login_required
def index():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id).all()
    account_ids = [a.id for a in accounts]
    task_query = MessageTask.query.filter(MessageTask.account_id.in_(account_ids)) if account_ids else MessageTask.query.filter(False)
    log_query = MessageLog.query.join(MessageTask, MessageLog.task_id == MessageTask.id).filter(MessageTask.account_id.in_(account_ids)) if account_ids else MessageLog.query.filter(False)
    log_status = dict(log_query.with_entities(MessageLog.status, func.count(MessageLog.id)).group_by(MessageLog.status).all())

    status_counts = dict(
        TelegramAccount.query.filter_by(owner_id=current_user.id)
        .with_entities(TelegramAccount.status, func.count(TelegramAccount.id))
        .group_by(TelegramAccount.status).all()
    )
    task_status = dict(task_query.with_entities(MessageTask.status, func.count(MessageTask.id)).group_by(MessageTask.status).all())
    join_status = dict(
        JoinJob.query.filter_by(owner_id=current_user.id)
        .with_entities(JoinJob.status, func.count(JoinJob.id))
        .group_by(JoinJob.status).all()
    )
    latest_errors = log_query.filter(MessageLog.status.in_(["failed", "skipped"])).order_by(MessageLog.id.desc()).limit(20).all()
    stats = {
        "accounts": len(accounts),
        "groups": TelegramGroup.query.filter(TelegramGroup.account_id.in_(account_ids)).count() if account_ids else 0,
        "campaigns": MessageCampaign.query.filter_by(owner_id=current_user.id).count(),
        "tasks": task_query.count(),
        "sent": log_status.get("sent", 0),
        "failed": log_status.get("failed", 0),
        "skipped": log_status.get("skipped", 0),
        "search_jobs": SearchJob.query.filter_by(owner_id=current_user.id).count(),
        "discovered_links": DiscoveredLink.query.filter_by(owner_id=current_user.id).count(),
        "managed_links": ManagedLink.query.filter_by(owner_id=current_user.id).count(),
        "join_links": DiscoveredJoinLink.query.filter_by(owner_id=current_user.id).count(),
    }
    return render_template("reports.html", stats=stats, status_counts=status_counts, task_status=task_status, join_status=join_status, latest_errors=latest_errors)
