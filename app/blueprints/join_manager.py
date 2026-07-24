import csv
import io
import random

from flask import Blueprint, Response, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.background import submit_job
from app.extensions import db
from app.models import (
    DiscoveredJoinLink,
    DiscoveredLink,
    JoinJob,
    JoinJobItem,
    JoinScanJob,
    JoinSource,
    SearchJob,
    TelegramAccount,
    WhatsAppLink,
)
from app.services.audit import log_action
from app.services.discovery import parse_telegram_link
from app.services.settings import get_bool, get_int
from worker_tasks import execute_join_job, scan_join_source

bp = Blueprint("join_manager", __name__, url_prefix="/join-manager")

JOINABLE_POOL_STATUSES = [
    "valid_public",
    "valid_invite",
    "already_member",
    "joined",
    "join_request_pending",
]


def _dedupe_by_hash(rows):
    seen = set()
    result = []
    for row in rows:
        if row.url_hash in seen:
            continue
        seen.add(row.url_hash)
        result.append(row)
    return result


def _source_rows_for_mode(owner_id: int, mode: str, selected_ids: list[int], max_items: int, link_order: str = "newest", source_ids: list[int] | None = None, include_imported: bool = True):
    """Return link rows from the owner's global discovered pool.

    The returned rows are used as a source pool and are cloned per target account.
    This makes one discovered list usable by many accounts without forcing the user
    to rescan the same source channel for every account.
    """
    query = DiscoveredJoinLink.query.filter(
        DiscoveredJoinLink.owner_id == owner_id,
        DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES),
    )
    if source_ids:
        # Links obtained from a source channel are tied to its scan job.  Keep
        # search-imported links optional because they have no JoinScanJob.
        query = query.join(JoinScanJob, DiscoveredJoinLink.scan_job_id == JoinScanJob.id).filter(JoinScanJob.source_id.in_(source_ids))
    elif not include_imported:
        query = query.filter(DiscoveredJoinLink.scan_job_id.isnot(None))

    if mode == "selected":
        if not selected_ids:
            return []
        query = query.filter(DiscoveredJoinLink.id.in_(selected_ids))
    elif mode == "all_valid":
        pass
    elif mode == "groups":
        query = query.filter(DiscoveredJoinLink.entity_type == "group")
    elif mode == "channels":
        query = query.filter(DiscoveredJoinLink.entity_type == "channel")
    elif mode == "invites":
        query = query.filter(DiscoveredJoinLink.invite_hash.isnot(None))
    elif mode == "approval_required":
        query = query.filter(DiscoveredJoinLink.requires_approval.is_(True))
    else:
        return []

    # Pull more than max_items because duplicate URLs may exist for different accounts.
    order_column = DiscoveredJoinLink.id.asc() if link_order == "oldest" else DiscoveredJoinLink.id.desc()
    rows = query.order_by(order_column).limit(max_items * 5).all()
    rows = _dedupe_by_hash(rows)
    if link_order == "random":
        random.shuffle(rows)
    return rows[:max_items]


def _ensure_link_for_account(owner_id: int, account_id: int, source: DiscoveredJoinLink) -> DiscoveredJoinLink:
    """Create an account-specific copy of a discovered link if needed.

    Join status is account-specific. A link that is already joined by account A
    may still be joinable by account B. Therefore each account gets its own row,
    and the worker re-checks the row before attempting to join.
    """
    row = DiscoveredJoinLink.query.filter_by(
        owner_id=owner_id,
        account_id=account_id,
        url_hash=source.url_hash,
    ).first()
    if row:
        return row

    row = DiscoveredJoinLink(
        owner_id=owner_id,
        account_id=account_id,
        scan_job_id=source.scan_job_id,
        source_message_id=source.source_message_id,
        source_message_url=source.source_message_url,
        url=source.url,
        url_hash=source.url_hash,
        invite_hash=source.invite_hash,
        username=source.username,
        entity_type=source.entity_type,
        entity_title=source.entity_title,
        entity_id=source.entity_id,
        # Force a fresh account-specific inspection in the worker.
        status="discovered",
        requires_approval=source.requires_approval,
        is_already_member=False,
    )
    db.session.add(row)
    db.session.flush()
    return row


@bp.get("")
@login_required
def index():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id).all()
    sources = JoinSource.query.filter_by(owner_id=current_user.id).order_by(JoinSource.id.desc()).all()
    links = DiscoveredJoinLink.query.filter_by(owner_id=current_user.id).order_by(DiscoveredJoinLink.id.desc()).limit(1000).all()
    jobs = JoinJob.query.filter_by(owner_id=current_user.id).order_by(JoinJob.id.desc()).limit(20).all()
    scans = JoinScanJob.query.filter_by(owner_id=current_user.id).order_by(JoinScanJob.id.desc()).limit(20).all()
    join_stats = {
        "all_valid": DiscoveredJoinLink.query.filter(
            DiscoveredJoinLink.owner_id == current_user.id,
            DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES),
        ).count(),
        "groups": DiscoveredJoinLink.query.filter_by(owner_id=current_user.id, entity_type="group").filter(DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES)).count(),
        "channels": DiscoveredJoinLink.query.filter_by(owner_id=current_user.id, entity_type="channel").filter(DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES)).count(),
        "invites": DiscoveredJoinLink.query.filter(DiscoveredJoinLink.owner_id == current_user.id, DiscoveredJoinLink.invite_hash.isnot(None), DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES)).count(),
        "approval_required": DiscoveredJoinLink.query.filter_by(owner_id=current_user.id, requires_approval=True).filter(DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES)).count(),
    }
    return render_template(
        "join_manager/index.html",
        accounts=accounts,
        sources=sources,
        links=links,
        jobs=jobs,
        scans=scans,
        join_stats=join_stats,
        max_items=get_int(current_user.id, "JOIN_MAX_ITEMS_PER_JOB", current_app.config["JOIN_MAX_ITEMS_PER_JOB"]),
        continue_batches=get_bool(current_user.id, "JOIN_CONTINUE_BATCHES", current_app.config.get("JOIN_CONTINUE_BATCHES", False)),
        batch_pause_seconds=get_int(current_user.id, "JOIN_BATCH_PAUSE_SECONDS", current_app.config.get("JOIN_BATCH_PAUSE_SECONDS", 300)),
        max_batches=get_int(current_user.id, "JOIN_MAX_BATCHES_PER_RUN", current_app.config.get("JOIN_MAX_BATCHES_PER_RUN", 5)),
        resume_after_floodwait=get_bool(current_user.id, "JOIN_RESUME_AFTER_FLOODWAIT", current_app.config.get("JOIN_RESUME_AFTER_FLOODWAIT", True)),
        max_floodwait_sleep=get_int(current_user.id, "JOIN_MAX_FLOODWAIT_SLEEP_SECONDS", current_app.config.get("JOIN_MAX_FLOODWAIT_SLEEP_SECONDS", 3600)),
    )


@bp.get("/exports/<string:link_kind>.csv")
@login_required
def export_links_csv(link_kind):
    """Download independently filtered Telegram or WhatsApp link inventories."""
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["link", "type", "status", "title", "source", "date"])
    if link_kind == "telegram":
        rows = DiscoveredJoinLink.query.filter_by(owner_id=current_user.id).order_by(DiscoveredJoinLink.id.desc()).all()
        for row in rows:
            writer.writerow([row.url, row.entity_type or "", row.status, row.entity_title or "", row.source_message_url or "", row.checked_at or ""])
    elif link_kind == "whatsapp":
        rows = WhatsAppLink.query.filter_by(owner_id=current_user.id).order_by(WhatsAppLink.id.desc()).all()
        for row in rows:
            writer.writerow([row.url, "whatsapp_group", "discovered", row.source_title or "", row.source_message_url or "", row.message_date or ""])
    else:
        return Response("Unknown export type", status=404)
    filename = f"{link_kind}-links.csv"
    return Response(stream.getvalue().encode("utf-8-sig"), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.post("/links/reprepare")
@login_required
def reprepare_failed_links():
    rows = DiscoveredJoinLink.query.filter_by(owner_id=current_user.id, status="check_failed").all()
    repaired = 0
    for row in rows:
        target = parse_telegram_link(row.url)
        if target.kind == "invite":
            row.invite_hash, row.status, row.error_text = target.value, "valid_invite", None
            repaired += 1
        elif target.kind == "username":
            row.username, row.status, row.error_text = target.value, "valid_public", None
            repaired += 1
    db.session.commit()
    flash(f"تمت إعادة تجهيز {repaired} رابطاً. سيُتحقق منها بأمان عند الانضمام.", "success")
    return redirect(url_for("join_manager.index"))


@bp.post("/sources")
@login_required
def save_source():
    account_id = request.form.get("account_id", type=int)
    source_ref = request.form.get("source_channel_ref", "").strip()
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id, status="active").first()
    if not account or not source_ref:
        flash("اختر حساباً نشطاً وأدخل رابط قناة المصدر", "danger")
    else:
        source = JoinSource(owner_id=current_user.id, account_id=account.id, source_channel_ref=source_ref)
        db.session.add(source)
        log_action("join_source.saved", "join_source", source.id)
        db.session.commit()
        flash("تم حفظ قناة المصدر", "success")
    return redirect(url_for("join_manager.index"))


@bp.post("/sources/<int:source_id>/scan")
@login_required
def scan(source_id):
    source = JoinSource.query.filter_by(id=source_id, owner_id=current_user.id).first_or_404()
    if request.form.get("full_scan") == "1":
        source.last_scanned_message_id = 0
    scan_job = JoinScanJob(owner_id=current_user.id, account_id=source.account_id, source_id=source.id)
    db.session.add(scan_job)
    db.session.flush()
    log_action("join_source.scan_started", "join_scan_job", scan_job.id, details="full_scan=1" if request.form.get("full_scan") == "1" else None)
    db.session.commit()
    submit_job(current_app._get_current_object(), scan_join_source, scan_job.id)
    flash("بدأ فحص الرسائل واستخراج روابط Telegram", "info")
    return redirect(url_for("join_manager.index"))


@bp.post("/sources/<int:source_id>/delete")
@login_required
def delete_source(source_id):
    source = JoinSource.query.filter_by(id=source_id, owner_id=current_user.id).first_or_404()
    db.session.delete(source)
    db.session.commit()
    flash("تم حذف مصدر الروابط", "success")
    return redirect(url_for("join_manager.index"))


@bp.post("/import-search/<int:job_id>")
@login_required
def import_search(job_id):
    job = SearchJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    account_id = request.form.get("account_id", type=int)
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id, status="active").first()
    if not account:
        flash("اختر حساباً نشطاً لإدارة الانضمام", "danger")
        return redirect(url_for("search.results", job_id=job.id))
    added = 0
    for link in DiscoveredLink.query.filter_by(search_job_id=job.id, link_type="telegram").all():
        if parse_telegram_link(link.url).kind == "unsupported":
            continue
        row = DiscoveredJoinLink.query.filter_by(owner_id=current_user.id, account_id=account.id, url_hash=link.url_hash).first()
        target = parse_telegram_link(link.url)
        if not row:
            db.session.add(DiscoveredJoinLink(
                owner_id=current_user.id,
                account_id=account.id,
                url=link.url,
                url_hash=link.url_hash,
                source_message_url=link.source_message_url,
                invite_hash=target.value if target.kind == "invite" else None,
                username=target.value if target.kind == "username" else None,
                status="valid_invite" if target.kind == "invite" else "valid_public",
            ))
            added += 1
    log_action("join_links.added_from_search", "search_job", job.id, details=f"added={added}")
    db.session.commit()
    flash(f"تمت إضافة {added} رابطاً إلى مدير الانضمام وتجهيزها للانضمام.", "success")
    return redirect(url_for("join_manager.index"))


@bp.post("/execute")
@login_required
def execute():
    # Backward compatible with earlier single-select account_id and new multi-account account_ids.
    account_ids = [int(v) for v in request.form.getlist("account_ids") if str(v).isdigit()]
    single_account_id = request.form.get("account_id", type=int)
    if single_account_id and single_account_id not in account_ids:
        account_ids.append(single_account_id)

    join_mode = request.form.get("join_mode", "selected")
    source_ids = [int(v) for v in request.form.getlist("source_ids") if str(v).isdigit()]
    include_imported = request.form.get("include_imported") == "1"
    distribution_mode = request.form.get("distribution_mode", "shared")
    link_order = request.form.get("link_order", "newest")
    if distribution_mode not in {"shared", "round_robin", "chunks", "random"}:
        distribution_mode = "shared"
    if link_order not in {"newest", "oldest", "random"}:
        link_order = "newest"
    selected_ids = [int(v) for v in request.form.getlist("link_ids") if v.isdigit()]
    max_items = get_int(current_user.id, "JOIN_MAX_ITEMS_PER_JOB", current_app.config["JOIN_MAX_ITEMS_PER_JOB"])

    accounts = TelegramAccount.query.filter(
        TelegramAccount.owner_id == current_user.id,
        TelegramAccount.status == "active",
        TelegramAccount.id.in_(account_ids or [-1]),
    ).order_by(TelegramAccount.id.asc()).all()

    if not accounts:
        flash("اختر حساباً نشطاً واحداً على الأقل", "danger")
        return redirect(url_for("join_manager.index"))

    pool_size = max_items if distribution_mode == "shared" else max_items * len(accounts)
    source_rows = _source_rows_for_mode(current_user.id, join_mode, selected_ids, pool_size, link_order, source_ids, include_imported)
    if not source_rows:
        flash("لا توجد روابط صالحة حسب الخيار المحدد. افحص الروابط أولاً أو غيّر خيار الانضمام.", "danger")
        return redirect(url_for("join_manager.index"))

    auto_continue = get_bool(current_user.id, "JOIN_CONTINUE_BATCHES", current_app.config.get("JOIN_CONTINUE_BATCHES", False))
    resume_after_floodwait = get_bool(current_user.id, "JOIN_RESUME_AFTER_FLOODWAIT", current_app.config.get("JOIN_RESUME_AFTER_FLOODWAIT", True))
    batch_pause_seconds = get_int(current_user.id, "JOIN_BATCH_PAUSE_SECONDS", current_app.config.get("JOIN_BATCH_PAUSE_SECONDS", 300))
    max_batches = get_int(current_user.id, "JOIN_MAX_BATCHES_PER_RUN", current_app.config.get("JOIN_MAX_BATCHES_PER_RUN", 5))
    if join_mode == "selected":
        # A selected job must never pick unrelated links in a following batch.
        # FloodWait monitoring/resume remains enabled independently in the worker.
        auto_continue = False
        max_batches = 1

    assignments = {}
    if distribution_mode == "random":
        random.shuffle(source_rows)
    for index, account in enumerate(accounts):
        if distribution_mode == "shared":
            chosen = source_rows[:max_items]
        elif distribution_mode in {"chunks", "random"}:
            chosen = source_rows[index * max_items:(index + 1) * max_items]
        else:
            chosen = source_rows[index::len(accounts)][:max_items]
        assignments[account.id] = chosen

    created_jobs = []
    for account in accounts:
        account_rows = [_ensure_link_for_account(current_user.id, account.id, row) for row in assignments[account.id]]
        if not account_rows:
            continue
        job = JoinJob(
            owner_id=current_user.id,
            account_id=account.id,
            total_links=len(account_rows),
            selection_mode=join_mode,
            auto_continue=auto_continue,
            auto_resume=resume_after_floodwait,
            batch_pause_seconds=batch_pause_seconds,
            max_batches=max(1, max_batches),
            batch_index=1,
        )
        db.session.add(job)
        db.session.flush()
        db.session.add_all([JoinJobItem(join_job_id=job.id, discovered_link_id=row.id) for row in account_rows])
        created_jobs.append(job)

    if not created_jobs:
        flash("لم يتم إنشاء أي مهمة انضمام.", "danger")
        return redirect(url_for("join_manager.index"))

    log_action(
        "join_jobs.started_multi_account",
        "join_job",
        details=f"mode={join_mode}; order={link_order}; distribution={distribution_mode}; accounts={len(created_jobs)}; pool={len(source_rows)}; auto_continue={auto_continue}; pause={batch_pause_seconds}; max_batches={max_batches}",
    )
    db.session.commit()

    for job in created_jobs:
        submit_job(current_app._get_current_object(), execute_join_job, job.id)

    if len(created_jobs) == 1:
        flash(f"بدأت مهمة الانضمام: {len(source_rows)} رابط. الحساب: {created_jobs[0].account_id}.", "success")
    else:
        flash(f"بدأت {len(created_jobs)} مهام انضمام على {len(created_jobs)} حسابات. كل حساب سيعالج حتى {len(source_rows)} رابط في الدفعة الأولى.", "success")
    # Open the live status screen immediately, rather than leaving the user on
    # the list page where a background join can look like it never started.
    return redirect(url_for("join_manager.job_detail", job_id=created_jobs[0].id))


@bp.get("/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    job = JoinJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    items = (
        db.session.query(JoinJobItem, DiscoveredJoinLink)
        .join(DiscoveredJoinLink, JoinJobItem.discovered_link_id == DiscoveredJoinLink.id)
        .filter(JoinJobItem.join_job_id == job.id)
        .order_by(JoinJobItem.id.asc())
        .all()
    )
    return render_template("join_manager/job_detail.html", job=job, items=items)


@bp.get("/jobs/<int:job_id>/status")
@login_required
def job_status(job_id):
    """Small polling endpoint so the join screen never looks frozen while a worker runs."""
    job = JoinJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    processed = job.joined_count + job.request_pending_count + job.already_member_count + job.failed_count
    state = {
        "queued": "بانتظار عامل التنفيذ.",
        "running": "الانضمام مستمر ويتم تحديث النتائج تلقائياً.",
        "paused_rate_limit": "أوقف Telegram الطلبات مؤقتاً؛ ستُستأنف المهمة تلقائياً عند انتهاء المهلة.",
        "paused": "المهمة متوقفة مؤقتاً من لوحة التحكم.",
        "stopped": "المهمة متوقفة وتحتاج إلى استئناف يدوي.",
        "completed": "اكتملت عملية الانضمام.",
        "cancelled": "أُلغيت عملية الانضمام.",
    }.get(job.status, job.stopped_reason or "تتم متابعة حالة المهمة.")
    return jsonify({
        "id": job.id, "status": job.status, "message": job.stopped_reason or state,
        "processed": processed, "total": job.total_links, "joined": job.joined_count,
        "pending": job.request_pending_count, "already_member": job.already_member_count,
        "failed": job.failed_count,
        "rate_limited_until": job.rate_limited_until.isoformat() if job.rate_limited_until else None,
        "terminal": job.status in {"completed", "cancelled", "stopped", "failed"},
    })


@bp.post("/jobs/<int:job_id>/pause")
@login_required
def job_pause(job_id):
    job = JoinJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    if job.status in {"queued", "running", "paused_rate_limit"}:
        job.status = "stopped" if job.status == "running" else "paused"
        job.stopped_reason = "تم إيقاف حملة الانضمام مؤقتاً من لوحة التحكم"
        db.session.commit()
        flash("تم إيقاف حملة الانضمام مؤقتاً. إذا كانت تعمل حالياً ستتوقف عند أول نقطة تحقق آمنة.", "warning")
    return redirect(url_for("join_manager.job_detail", job_id=job.id))


@bp.post("/jobs/<int:job_id>/resume")
@login_required
def job_resume(job_id):
    job = JoinJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    if job.status in {"paused", "stopped", "paused_rate_limit", "failed"}:
        job.status = "queued"
        job.stopped_reason = None
        job.rate_limited_until = None
        job.auto_resume = True
        db.session.commit()
        submit_job(current_app._get_current_object(), execute_join_job, job.id)
        flash("تم استئناف حملة الانضمام.", "success")
    return redirect(url_for("join_manager.job_detail", job_id=job.id))


@bp.post("/jobs/<int:job_id>/cancel")
@login_required
def job_cancel(job_id):
    job = JoinJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    job.status = "cancelled"
    job.auto_resume = False
    job.stopped_reason = "تم إلغاء حملة الانضمام من لوحة التحكم"
    db.session.commit()
    flash("تم إلغاء حملة الانضمام.", "danger")
    return redirect(url_for("join_manager.job_detail", job_id=job.id))


@bp.post("/jobs/<int:job_id>/retry-failed")
@login_required
def job_retry_failed(job_id):
    job = JoinJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    items = JoinJobItem.query.filter_by(join_job_id=job.id).filter(JoinJobItem.status.in_(["failed", "rate_limited"])).all()
    for item in items:
        item.status = "approved"
        item.error_code = None
        item.error_text = None
        item.next_attempt_at = None
        item.completed_at = None
    if items:
        job.status = "queued"
        job.stopped_reason = None
        job.auto_resume = True
        db.session.commit()
        submit_job(current_app._get_current_object(), execute_join_job, job.id)
        flash(f"تمت إعادة محاولة {len(items)} رابط/روابط فاشلة.", "success")
    else:
        flash("لا توجد روابط فاشلة لإعادة المحاولة.", "info")
    return redirect(url_for("join_manager.job_detail", job_id=job.id))
