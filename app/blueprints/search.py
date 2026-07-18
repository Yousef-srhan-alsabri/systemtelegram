import json

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.background import submit_job
from app.extensions import db
from app.models import (
    DiscoveredLink,
    ExportSetting,
    SearchEntity,
    SearchJob,
    SearchJobAccount,
    SearchMessage,
    TelegramAccount,
)
from worker_tasks import execute_search_job, export_search_job, validate_export_setting
from app.services.settings import get_int
from app.services.audit import log_action

bp = Blueprint("search", __name__, url_prefix="/search")


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id).all()
    setting = ExportSetting.query.filter_by(owner_id=current_user.id).first()
    recent = SearchJob.query.filter_by(owner_id=current_user.id).order_by(SearchJob.id.desc()).limit(10).all()
    if request.method == "POST":
        query = request.form.get("query", "").strip()
        selected_ids = {int(v) for v in request.form.getlist("account_ids") if v.isdigit()}
        selected = [a for a in accounts if a.id in selected_ids]
        try:
            max_results = int(request.form.get("max_results", get_int(current_user.id, "SEARCH_DEFAULT_MAX_RESULTS", current_app.config["SEARCH_DEFAULT_MAX_RESULTS"])))
        except ValueError:
            max_results = get_int(current_user.id, "SEARCH_DEFAULT_MAX_RESULTS", current_app.config["SEARCH_DEFAULT_MAX_RESULTS"])
        max_results = max(10, min(max_results, get_int(current_user.id, "SEARCH_MAX_RESULTS", current_app.config["SEARCH_MAX_RESULTS"])))
        saudi_only = request.form.get("saudi_only") == "1"
        allowed_scopes = {"joined_only", "global_plus_joined", "global_only"}
        search_scope = request.form.get("search_scope", current_app.config.get("SEARCH_SCOPE_DEFAULT", "global_plus_joined")).strip()
        if search_scope not in allowed_scopes:
            search_scope = "global_plus_joined"
        include_public_messages = request.form.get("include_public_messages") == "1"
        exclude_system_sources = request.form.get("exclude_system_sources", "1") == "1"
        search_expansion = request.form.get("search_expansion", "1") == "1"
        if len(query) < 2:
            flash("اكتب عبارة بحث لا تقل عن حرفين", "danger")
        elif not selected:
            flash("اختر حساباً واحداً على الأقل", "danger")
        else:
            job = SearchJob(
                owner_id=current_user.id,
                query_text=query,
                saudi_only=saudi_only,
                max_results=max_results,
                search_scope=search_scope,
                include_public_messages=include_public_messages,
                exclude_system_sources=exclude_system_sources,
                expanded_queries_json=json.dumps([query], ensure_ascii=False) if search_expansion else None,
            )
            db.session.add(job)
            db.session.flush()
            db.session.add_all([SearchJobAccount(search_job_id=job.id, account_id=a.id) for a in selected])
            log_action("search.created", "search_job", job.id, details=query)
            db.session.commit()
            submit_job(current_app._get_current_object(), execute_search_job, job.id)
            flash("بدأ البحث. ستتحدث النتائج تلقائياً عند اكتماله.", "info")
            return redirect(url_for("search.results", job_id=job.id))
    return render_template("search/index.html", accounts=accounts, recent=recent, setting=setting,
                           default_max=get_int(current_user.id, "SEARCH_DEFAULT_MAX_RESULTS", current_app.config["SEARCH_DEFAULT_MAX_RESULTS"]),
                           default_scope=current_app.config.get("SEARCH_SCOPE_DEFAULT", "global_plus_joined"),
                           include_public_default=current_app.config.get("SEARCH_INCLUDE_PUBLIC_MESSAGES", True),
                           exclude_system_default=True)


@bp.get("/<int:job_id>")
@login_required
def results(job_id):
    job = SearchJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    groups = SearchEntity.query.filter_by(search_job_id=job.id, entity_type="group").order_by(SearchEntity.similarity_score.desc(), SearchEntity.saudi_score.desc()).all()
    channels = SearchEntity.query.filter_by(search_job_id=job.id, entity_type="channel").order_by(SearchEntity.similarity_score.desc(), SearchEntity.saudi_score.desc()).all()
    bots = SearchEntity.query.filter_by(search_job_id=job.id, entity_type="bot").order_by(SearchEntity.similarity_score.desc()).all()
    messages = SearchMessage.query.filter_by(search_job_id=job.id).order_by(SearchMessage.message_date.desc()).all()
    links = DiscoveredLink.query.filter_by(search_job_id=job.id).order_by(DiscoveredLink.link_type, DiscoveredLink.id).all()
    setting = ExportSetting.query.filter_by(owner_id=current_user.id).first()
    job_account_ids = [row.account_id for row in SearchJobAccount.query.filter_by(search_job_id=job.id).all()]
    job_accounts = TelegramAccount.query.filter(TelegramAccount.id.in_(job_account_ids)).all() if job_account_ids else []
    return render_template("search/results.html", job=job, groups=groups, channels=channels, bots=bots,
                           messages=messages, links=links, setting=setting, job_accounts=job_accounts)


@bp.post("/settings/export")
@login_required
def save_export_setting():
    account_id = request.form.get("account_id", type=int)
    channel_ref = request.form.get("channel_ref", "").strip()
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id, status="active").first()
    if not account or not channel_ref:
        flash("اختر حساباً نشطاً وأدخل رابط أو username القناة", "danger")
        return redirect(request.referrer or url_for("search.index"))
    setting = ExportSetting.query.filter_by(owner_id=current_user.id).first()
    if not setting:
        setting = ExportSetting(owner_id=current_user.id)
        db.session.add(setting)
    setting.account_id = account.id
    setting.channel_ref = channel_ref
    setting.is_active = False
    log_action("export_setting.saved", "export_setting", setting.id)
    db.session.commit()
    submit_job(current_app._get_current_object(), validate_export_setting, setting.id)
    flash("تم حفظ قناة التصدير وبدأ اختبار الوصول إليها.", "info")
    return redirect(request.referrer or url_for("search.index"))


@bp.post("/<int:job_id>/export")
@login_required
def export(job_id):
    job = SearchJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    setting = ExportSetting.query.filter_by(owner_id=current_user.id, is_active=True).first()
    if not setting:
        flash("احفظ قناة تصدير صالحة أولاً", "danger")
    else:
        export_type = request.form.get("export_type", "all")
        log_action("search.export_started", "search_job", job.id, details=export_type)
        db.session.commit()
        submit_job(current_app._get_current_object(), export_search_job, job.id, export_type)
        flash("بدأ تصدير النتائج إلى القناة المحفوظة", "success")
    return redirect(url_for("search.results", job_id=job.id))
