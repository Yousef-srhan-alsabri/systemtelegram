from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.background import submit_job
from app.extensions import db
from app.models import (
    CampaignTask,
    MessageCampaign,
    MessageLog,
    MessageTask,
    MessageTaskTarget,
    TelegramAccount,
    TelegramGroup,
    ContentItem,
)
from worker_tasks import execute_message_task
from app.services.settings import get_int, get_setting, set_setting
from app.services.audit import log_action

bp = Blueprint("messages", __name__, url_prefix="/messages")


def _parse_local_datetime(value, timezone_name="Asia/Aden"):
    """Parse an HTML datetime-local value using the selected timezone and store it as UTC.

    HTML datetime-local has no timezone information. The dashboard treats it as the
    operator's selected timezone, then converts it to UTC for consistent scheduler checks.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        return None

    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)

    tz = timezone.utc
    if ZoneInfo is not None:
        try:
            tz = ZoneInfo(timezone_name or "Asia/Riyadh")
        except Exception:
            tz = timezone.utc
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def _parse_12h_datetime(date_value, hour_value, minute_value, ampm_value, timezone_name="Asia/Aden"):
    """Parse split 12-hour date/time controls and store UTC."""
    date_value = (date_value or "").strip()
    if not date_value:
        return None
    try:
        hour = int(hour_value or 12)
        minute = int(minute_value or 0)
    except ValueError:
        return None
    if hour < 1 or hour > 12 or minute < 0 or minute > 59:
        return None
    ampm = (ampm_value or "AM").upper()
    if ampm == "PM" and hour != 12:
        hour += 12
    if ampm == "AM" and hour == 12:
        hour = 0
    return _parse_local_datetime(f"{date_value}T{hour:02d}:{minute:02d}", timezone_name)


def _resolve_schedule(prefix, timezone_name):
    """Accept new 12-hour controls and old datetime-local fallback."""
    if prefix:
        date_key = f"{prefix}_date"
        hour_key = f"{prefix}_hour"
        minute_key = f"{prefix}_minute"
        ampm_key = f"{prefix}_ampm"
        fallback_key = prefix
    else:
        date_key = "scheduled_date"
        hour_key = "scheduled_hour"
        minute_key = "scheduled_minute"
        ampm_key = "scheduled_ampm"
        fallback_key = "scheduled_at"
    split_dt = _parse_12h_datetime(
        request.form.get(date_key),
        request.form.get(hour_key),
        request.form.get(minute_key),
        request.form.get(ampm_key),
        timezone_name,
    )
    return split_dt or _parse_local_datetime(request.form.get(fallback_key), timezone_name)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id).all()
    account_group_counts = {
        account.id: TelegramGroup.query.filter_by(account_id=account.id, can_publish=True).count()
        for account in accounts
    }
    content_items = ContentItem.query.filter_by(owner_id=current_user.id).filter(ContentItem.status != "archived").order_by(ContentItem.id.desc()).all()

    if request.method == "POST":
        selected_ids = {int(value) for value in request.form.getlist("account_ids") if value.isdigit()}
        selected_accounts = [account for account in accounts if account.id in selected_ids]
        content_strategy = (request.form.get("content_strategy") or "uniform").strip()
        uniform_source_mode = (request.form.get("source_mode") or "content").strip()
        uniform_forward_source_ref = (request.form.get("forward_source_ref") or get_setting(current_user.id, "FORWARD_SOURCE_REF", "") or "").strip()
        if uniform_source_mode == "forward_last" and uniform_forward_source_ref:
            set_setting(current_user.id, "FORWARD_SOURCE_REF", uniform_forward_source_ref, current_user.id)
        uniform_text = request.form.get("text", "").strip()
        uniform_content_item_id = request.form.get("content_item_id", type=int)
        uniform_content_item = None
        if uniform_content_item_id:
            uniform_content_item = ContentItem.query.filter_by(id=uniform_content_item_id, owner_id=current_user.id).first()
        if uniform_content_item and not uniform_text:
            uniform_text = uniform_content_item.body_html or uniform_content_item.body_plain or uniform_content_item.title

        name = request.form.get("name", "").strip() or (uniform_content_item.title if uniform_content_item else "حملة جديدة")
        timezone_name = (request.form.get("timezone_name") or "Asia/Aden").strip()
        scheduled_at = _resolve_schedule("", timezone_name)
        send_window_start = (request.form.get("send_window_start") or "").strip() or None
        send_window_end = (request.form.get("send_window_end") or "").strip() or None
        repeat_rule = (request.form.get("repeat_rule") or "none").strip()

        try:
            batch_size = int(request.form.get("batch_size", get_int(current_user.id, "DEFAULT_BATCH_SIZE", current_app.config["DEFAULT_BATCH_SIZE"])))
        except ValueError:
            batch_size = get_int(current_user.id, "DEFAULT_BATCH_SIZE", current_app.config["DEFAULT_BATCH_SIZE"])
        batch_size = min(max(batch_size, 1), 100)

        if not selected_accounts:
            flash("اختر حساباً واحداً على الأقل", "danger")
        elif content_strategy == "uniform" and uniform_source_mode == "forward_last" and not uniform_forward_source_ref:
            flash("أدخل معرف/يوزر مصدر التحويل مرة واحدة على الأقل.", "danger")
        elif content_strategy == "uniform" and uniform_source_mode != "forward_last" and not uniform_text:
            flash("نص الرسالة أو المحتوى المحفوظ مطلوب", "danger")
        else:
            campaign = MessageCampaign(
                owner_id=current_user.id,
                name=name,
                text=uniform_text or " ",
                content_item_id=uniform_content_item.id if uniform_content_item else None,
                target_mode="all_groups",
                batch_size=batch_size,
                status="queued",
                scheduled_at=scheduled_at,
                send_window_start=send_window_start,
                send_window_end=send_window_end,
                repeat_rule=repeat_rule,
                source_mode=uniform_source_mode,
                forward_source_ref=uniform_forward_source_ref if uniform_source_mode == "forward_last" else None,
            )
            db.session.add(campaign)
            db.session.flush()

            created_tasks = []
            skipped_accounts = []
            validation_errors = []
            max_targets = get_int(current_user.id, "MAX_TARGETS_PER_ACCOUNT_TASK", current_app.config["MAX_TARGETS_PER_ACCOUNT_TASK"])

            for account in selected_accounts:
                groups = (
                    TelegramGroup.query
                    .filter_by(account_id=account.id, can_publish=True)
                    .order_by(TelegramGroup.id.asc())
                    .limit(max_targets)
                    .all()
                )
                if not groups:
                    skipped_accounts.append(account.display_name or str(account.id))
                    continue

                if content_strategy == "per_account":
                    task_source_mode = (request.form.get(f"account_source_mode_{account.id}") or "content").strip()
                    task_forward_source_ref = (request.form.get(f"account_forward_source_ref_{account.id}") or uniform_forward_source_ref or "").strip()
                    task_text = (request.form.get(f"account_text_{account.id}") or "").strip()
                    task_content_item_id = request.form.get(f"account_content_item_id_{account.id}", type=int)
                    task_content_item = ContentItem.query.filter_by(id=task_content_item_id, owner_id=current_user.id).first() if task_content_item_id else None
                    if task_content_item and not task_text:
                        task_text = task_content_item.body_html or task_content_item.body_plain or task_content_item.title
                else:
                    task_source_mode = uniform_source_mode
                    task_forward_source_ref = uniform_forward_source_ref
                    task_text = uniform_text
                    task_content_item = uniform_content_item

                if task_source_mode == "forward_last" and not task_forward_source_ref:
                    validation_errors.append(f"{account.display_name or account.id}: مصدر التحويل غير محدد")
                    continue
                if task_source_mode != "forward_last" and not task_text:
                    validation_errors.append(f"{account.display_name or account.id}: المحتوى غير محدد")
                    continue
                if task_source_mode == "forward_last" and task_forward_source_ref:
                    set_setting(current_user.id, "FORWARD_SOURCE_REF", task_forward_source_ref, current_user.id)

                timing_mode = (request.form.get(f"timing_mode_{account.id}") or "campaign").strip()
                if timing_mode == "custom":
                    account_schedule = _resolve_schedule(f"account_schedule_{account.id}", timezone_name) or scheduled_at
                elif timing_mode == "campaign":
                    account_schedule = scheduled_at
                elif timing_mode == "now":
                    account_schedule = None
                else:
                    account_schedule = scheduled_at

                task = MessageTask(
                    account_id=account.id,
                    text=task_text or " ",
                    content_item_id=task_content_item.id if task_content_item else None,
                    total_groups=len(groups),
                    status="scheduled" if account_schedule else "queued",
                    schedule_time=account_schedule,
                    repeat_rule=repeat_rule,
                    source_mode=task_source_mode,
                    forward_source_ref=task_forward_source_ref if task_source_mode == "forward_last" else None,
                    batch_size=batch_size,
                )
                db.session.add(task)
                db.session.flush()
                db.session.add(CampaignTask(campaign_id=campaign.id, task_id=task.id))
                db.session.add_all([MessageTaskTarget(task_id=task.id, group_id=group.id) for group in groups])
                created_tasks.append(task)

            if validation_errors:
                db.session.rollback()
                flash("تعذر إنشاء الحملة: " + " | ".join(validation_errors[:6]), "danger")
            elif not created_tasks:
                db.session.rollback()
                flash("لا توجد مجموعات متزامنة في الحسابات المحددة. حدّث المجموعات أولاً.", "danger")
            else:
                if any(task.status == "queued" for task in created_tasks):
                    campaign.status = "queued"
                elif all(task.status == "scheduled" for task in created_tasks):
                    campaign.status = "scheduled"
                else:
                    campaign.status = "queued"
                campaign.scheduled_at = min((task.schedule_time for task in created_tasks if task.schedule_time), default=None)

                log_action("campaign.created", "campaign", campaign.id, details=f"tasks={len(created_tasks)}; strategy={content_strategy}")
                db.session.commit()
                app = current_app._get_current_object()
                immediate_tasks = [task for task in created_tasks if task.status == "queued"]
                scheduled_tasks = [task for task in created_tasks if task.status == "scheduled"]
                for task in immediate_tasks:
                    submit_job(app, execute_message_task, task.id)
                if scheduled_tasks and not immediate_tasks:
                    flash("تم إنشاء الحملة كحملة مجدولة بالكامل. شغّل scheduler_worker.py ليبدأ التنفيذ في مواعيده.", "info")
                elif scheduled_tasks and immediate_tasks:
                    flash(f"تم إنشاء حملة مختلطة: {len(immediate_tasks)} حساب يبدأ حالاً و {len(scheduled_tasks)} حساب/حسابات مجدولة.", "info")
                if skipped_accounts:
                    flash("تم تجاوز حسابات بلا مجموعات: " + "، ".join(skipped_accounts), "warning")
                flash(f"تم إنشاء الحملة على {len(created_tasks)} حساب/حسابات.", "success")
                return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))

    return render_template(
        "message_create.html",
        accounts=accounts,
        account_group_counts=account_group_counts,
        default_batch_size=get_int(current_user.id, "DEFAULT_BATCH_SIZE", current_app.config["DEFAULT_BATCH_SIZE"]),
        content_items=content_items,
        default_timezone="Asia/Aden",
        default_forward_source=get_setting(current_user.id, "FORWARD_SOURCE_REF", ""),
    )


def _get_owned_campaign(campaign_id):
    return MessageCampaign.query.filter_by(id=campaign_id, owner_id=current_user.id).first_or_404()


def _campaign_tasks(campaign):
    return [link.task for link in campaign.task_links]


def _refresh_campaign_status(campaign, tasks=None, commit=True):
    tasks = tasks if tasks is not None else _campaign_tasks(campaign)
    statuses = {task.status for task in tasks}
    if not tasks:
        campaign.status = "draft"
    elif any(status in statuses for status in {"running", "queued", "pause_requested", "cancel_requested"}):
        # Keep a visible manual state if the user has requested pause/cancel.
        if "pause_requested" in statuses:
            campaign.status = "pause_requested"
        elif "cancel_requested" in statuses:
            campaign.status = "cancel_requested"
        else:
            campaign.status = "running"
    elif "risk_hold" in statuses:
        campaign.status = "risk_hold"
    elif statuses and statuses <= {"paused", "paused_rate_limit"}:
        campaign.status = "paused"
    elif statuses and statuses <= {"cancelled"}:
        campaign.status = "cancelled"
    elif statuses and all(status == "completed" for status in statuses):
        campaign.status = "completed"
    elif statuses and all(status in {"completed", "stopped", "failed", "cancelled", "paused_rate_limit"} for status in statuses):
        campaign.status = "finished_with_issues"
    if commit:
        db.session.commit()
    return campaign.status


@bp.get("/campaign/<int:campaign_id>")
@login_required
def campaign_detail(campaign_id):
    campaign = _get_owned_campaign(campaign_id)
    tasks = _campaign_tasks(campaign)
    _refresh_campaign_status(campaign, tasks)
    return render_template("campaign_detail.html", campaign=campaign, tasks=tasks)


@bp.post("/campaign/<int:campaign_id>/pause")
@login_required
def campaign_pause(campaign_id):
    campaign = _get_owned_campaign(campaign_id)
    tasks = _campaign_tasks(campaign)
    changed = 0
    for task in tasks:
        if task.status in {"queued", "pending", "running", "paused_rate_limit"}:
            task.status = "pause_requested" if task.status == "running" else "paused"
            task.stop_reason = "تم طلب الإيقاف المؤقت من لوحة التحكم"
            changed += 1
    campaign.status = "pause_requested" if any(t.status == "pause_requested" for t in tasks) else "paused"
    log_action("campaign.pause", "campaign", campaign.id, details=f"tasks={changed}")
    db.session.commit()
    flash("تم طلب إيقاف الحملة مؤقتاً. أي مهمة تعمل الآن ستتوقف عند أول نقطة آمنة.", "warning")
    return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))


@bp.post("/campaign/<int:campaign_id>/resume")
@login_required
def campaign_resume(campaign_id):
    campaign = _get_owned_campaign(campaign_id)
    tasks = _campaign_tasks(campaign)
    app = current_app._get_current_object()
    resumed = 0
    for task in tasks:
        if task.status in {"paused", "paused_rate_limit", "pause_requested", "stopped", "risk_hold"}:
            task.status = "queued"
            task.stop_reason = None
            resumed += 1
    campaign.status = "queued"
    log_action("campaign.resume", "campaign", campaign.id, details=f"tasks={resumed}")
    db.session.commit()
    for task in tasks:
        if task.status == "queued":
            submit_job(app, execute_message_task, task.id)
    flash(f"تم استئناف {resumed} مهمة. سيكمل النظام من الأهداف غير المرسلة فقط.", "success")
    return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))


@bp.post("/campaign/<int:campaign_id>/cancel")
@login_required
def campaign_cancel(campaign_id):
    campaign = _get_owned_campaign(campaign_id)
    tasks = _campaign_tasks(campaign)
    changed = 0
    for task in tasks:
        if task.status not in {"completed", "cancelled"}:
            task.status = "cancel_requested" if task.status == "running" else "cancelled"
            task.stop_reason = "تم إلغاء الحملة من لوحة التحكم"
            changed += 1
    campaign.status = "cancel_requested" if any(t.status == "cancel_requested" for t in tasks) else "cancelled"
    log_action("campaign.cancel", "campaign", campaign.id, details=f"tasks={changed}")
    db.session.commit()
    flash("تم طلب إلغاء الحملة. لن يتم إرسال أهداف جديدة بعد نقطة التحقق التالية.", "danger")
    return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))


@bp.post("/campaign/<int:campaign_id>/edit-text")
@login_required
def campaign_edit_text(campaign_id):
    campaign = _get_owned_campaign(campaign_id)
    new_text = request.form.get("text", "").strip()
    if not new_text:
        flash("لا يمكن حفظ رسالة فارغة.", "danger")
        return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))

    tasks = _campaign_tasks(campaign)
    campaign.text = new_text
    # Make future sends use this campaign-specific text instead of the library item body.
    campaign.content_item_id = None
    updated = 0
    for task in tasks:
        if task.status not in {"completed", "cancelled"}:
            task.text = new_text
            task.content_item_id = None
            updated += 1
    log_action("campaign.edit_text", "campaign", campaign.id, details=f"tasks={updated}")
    db.session.commit()
    flash("تم تعديل رسالة الحملة. التعديل سيطبّق على الإرسالات القادمة فقط، ولن يغيّر الرسائل التي أُرسلت سابقاً.", "success")
    return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))


@bp.post("/campaign/<int:campaign_id>/risk-override")
@login_required
def campaign_risk_override(campaign_id):
    campaign = _get_owned_campaign(campaign_id)
    tasks = _campaign_tasks(campaign)
    app = current_app._get_current_object()
    resumed = 0
    for task in tasks:
        if task.status == "risk_hold":
            task.risk_override = True
            task.status = "queued"
            task.stop_reason = None
            resumed += 1
    campaign.status = "queued" if resumed else campaign.status
    log_action("campaign.risk_override", "campaign", campaign.id, details=f"tasks={resumed}")
    db.session.commit()
    for task in tasks:
        if task.status == "queued":
            submit_job(app, execute_message_task, task.id)
    flash("تم تفعيل الاستمرار رغم مستوى الخطر للحسابات المتوقفة. المسؤولية على مستخدم الحساب.", "warning")
    return redirect(url_for("messages.campaign_detail", campaign_id=campaign.id))


@bp.get("/<int:task_id>")
@login_required
def detail(task_id):
    task = MessageTask.query.join(TelegramAccount).filter(
        MessageTask.id == task_id,
        TelegramAccount.owner_id == current_user.id,
    ).first_or_404()
    logs = MessageLog.query.filter_by(task_id=task.id).order_by(MessageLog.id.desc()).all()
    return render_template("message_detail.html", task=task, logs=logs)
