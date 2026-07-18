
from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.background import executor_snapshot
from app.models import (
    DiscoveredJoinLink,
    DiscoveredLink,
    JoinJob,
    ManagedLink,
    MessageCampaign,
    MessageTask,
    SearchJob,
    TelegramAccount,
    TelegramGroup,
)

bp = Blueprint("main", __name__)


@bp.get("/")
@login_required
def dashboard():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id).all()
    account_ids = [account.id for account in accounts]
    task_query = MessageTask.query.filter(MessageTask.account_id.in_(account_ids)) if account_ids else MessageTask.query.filter(False)
    stats = {
        "accounts": len(account_ids),
        "active_accounts": sum(1 for a in accounts if a.status == "active"),
        "restricted_accounts": sum(1 for a in accounts if a.status in {"restricted", "unauthorized", "disconnected"}),
        "groups": TelegramGroup.query.filter(TelegramGroup.account_id.in_(account_ids)).count() if account_ids else 0,
        "campaigns": MessageCampaign.query.filter_by(owner_id=current_user.id).count(),
        "running_tasks": task_query.filter(MessageTask.status.in_(["running", "queued", "pending"])).count(),
        "search_jobs": SearchJob.query.filter_by(owner_id=current_user.id).count(),
        "join_pending": DiscoveredJoinLink.query.filter_by(owner_id=current_user.id, status="join_request_pending").count(),
        "links": ManagedLink.query.filter_by(owner_id=current_user.id).count() + DiscoveredLink.query.filter_by(owner_id=current_user.id).count(),
    }
    recent_campaigns = MessageCampaign.query.filter_by(owner_id=current_user.id).order_by(MessageCampaign.id.desc()).limit(8).all()
    recent_searches = SearchJob.query.filter_by(owner_id=current_user.id).order_by(SearchJob.id.desc()).limit(5).all()
    recent_join_jobs = JoinJob.query.filter_by(owner_id=current_user.id).order_by(JoinJob.id.desc()).limit(5).all()
    return render_template("dashboard.html", stats=stats, recent_campaigns=recent_campaigns, recent_searches=recent_searches, recent_join_jobs=recent_join_jobs, worker=executor_snapshot())
