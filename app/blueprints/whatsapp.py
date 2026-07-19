from datetime import datetime, timezone

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.background import submit_job
from app.extensions import db
from app.models import TelegramAccount, WhatsAppLink, WhatsAppScanJob
from app.services.audit import log_action
from worker_tasks import execute_whatsapp_scan_job

bp = Blueprint("whatsapp", __name__, url_prefix="/whatsapp")


def _parse_start_date(value):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


@bp.route("", methods=["GET", "POST"])
@login_required
def index():
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id).all()
    jobs = WhatsAppScanJob.query.filter_by(owner_id=current_user.id).order_by(WhatsAppScanJob.id.desc()).limit(20).all()
    links = WhatsAppLink.query.filter_by(owner_id=current_user.id).order_by(WhatsAppLink.id.desc()).limit(300).all()

    if request.method == "POST":
        account_ids = [int(v) for v in request.form.getlist("account_ids") if str(v).isdigit()]
        selected_accounts = [a for a in accounts if a.id in account_ids]
        scope = request.form.get("scope", "groups_channels")
        if scope not in {"groups", "channels", "groups_channels"}:
            scope = "groups_channels"
        export_mode = request.form.get("export_mode", "pdf")
        if export_mode not in {"pdf", "channel", "both"}:
            export_mode = "pdf"
        export_channel_ref = request.form.get("export_channel_ref", "").strip()
        export_account_id = request.form.get("export_account_id", type=int)
        start_date = _parse_start_date(request.form.get("start_date"))

        if not selected_accounts:
            flash("اختر حساباً نشطاً واحداً على الأقل.", "danger")
        elif export_mode in {"channel", "both"} and not export_channel_ref:
            flash("أدخل رابط قناة التصدير عند اختيار التصدير إلى قناة.", "danger")
        elif export_mode in {"channel", "both"} and export_account_id not in {a.id for a in accounts}:
            flash("اختر حساباً ناشراً صالحاً لقناة التصدير.", "danger")
        else:
            job = WhatsAppScanJob(
                owner_id=current_user.id,
                scope=scope,
                start_date=start_date,
                export_mode=export_mode,
                export_channel_ref=export_channel_ref or None,
                export_account_id=export_account_id if export_mode in {"channel", "both"} else None,
            )
            db.session.add(job)
            db.session.flush()
            log_action("whatsapp_scan.created", "whatsapp_scan_job", job.id, details=f"accounts={len(selected_accounts)}; scope={scope}")
            db.session.commit()
            submit_job(current_app._get_current_object(), execute_whatsapp_scan_job, job.id, [a.id for a in selected_accounts])
            flash("بدأ استخراج روابط واتساب. ستظهر النتائج هنا بعد اكتمال المهمة.", "info")
            return redirect(url_for("whatsapp.index"))

    return render_template("whatsapp/index.html", accounts=accounts, jobs=jobs, links=links)


@bp.get("/jobs/<int:job_id>/pdf")
@login_required
def download_pdf(job_id):
    job = WhatsAppScanJob.query.filter_by(id=job_id, owner_id=current_user.id).first_or_404()
    if not job.pdf_path:
        flash("ملف PDF غير جاهز لهذه المهمة.", "warning")
        return redirect(url_for("whatsapp.index"))
    return send_file(job.pdf_path, as_attachment=True, download_name=f"whatsapp-links-{job.id}.pdf")
