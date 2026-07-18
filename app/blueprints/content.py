import os
import re
import uuid
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.background import submit_job
from app.extensions import db
from app.models import ContentItem, ContentMedia, SavedChannel, TelegramAccount
from app.services.audit import log_action
from worker_tasks import send_content_test

bp = Blueprint("content", __name__, url_prefix="/content")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "mp4", "pdf", "doc", "docx", "xlsx", "zip", "webm", "tgs"}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
STICKER_EXTENSIONS = {"webp", "tgs", "webm"}


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


def _upload_dir() -> Path:
    path = Path(current_app.instance_path) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_upload(file_storage, owner_id: int, requested_type: str) -> ContentMedia | None:
    if not file_storage or not file_storage.filename:
        return None
    original = file_storage.filename
    safe = secure_filename(original) or "upload"
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError("نوع الملف غير مسموح")
    filename = f"{uuid.uuid4().hex}_{safe}"
    disk_path = _upload_dir() / filename
    file_storage.save(disk_path)
    size = disk_path.stat().st_size
    media_type = requested_type if requested_type in {"photo", "sticker"} else "document"
    if requested_type == "photo" and ext not in IMAGE_EXTENSIONS:
        media_type = "document"
    if requested_type == "sticker" and ext not in STICKER_EXTENSIONS:
        media_type = "document"
    media = ContentMedia(
        owner_id=owner_id,
        file_path=str(disk_path),
        original_filename=original,
        mime_type=file_storage.mimetype,
        file_size=size,
        media_type=media_type,
    )
    db.session.add(media)
    db.session.flush()
    return media


@bp.get("/")
@login_required
def index():
    items = ContentItem.query.filter_by(owner_id=current_user.id).order_by(ContentItem.id.desc()).all()
    return render_template("content/index.html", items=items)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    if request.method == "POST":
        return _save_item()
    return render_template("content/form.html", item=None)


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@login_required
def edit(item_id):
    item = ContentItem.query.filter_by(id=item_id, owner_id=current_user.id).first_or_404()
    if request.method == "POST":
        return _save_item(item)
    return render_template("content/form.html", item=item)


def _save_item(item=None):
    title = request.form.get("title", "").strip()
    content_type = request.form.get("content_type", "text").strip() or "text"
    body_html = request.form.get("body_html", "").strip()
    contact_first_name = request.form.get("contact_first_name", "").strip()
    contact_last_name = request.form.get("contact_last_name", "").strip()
    contact_phone = request.form.get("contact_phone", "").strip()
    link_preview = bool(request.form.get("link_preview"))

    if content_type not in {"text", "photo", "document", "sticker", "contact"}:
        flash("نوع المحتوى غير مدعوم", "danger")
        return redirect(request.referrer or url_for("content.index"))
    if not title:
        flash("عنوان المحتوى مطلوب", "danger")
        return redirect(request.referrer or url_for("content.index"))
    if content_type == "text" and not body_html:
        flash("النص مطلوب لهذا النوع", "danger")
        return redirect(request.referrer or url_for("content.index"))
    if content_type == "contact" and (not contact_first_name or not contact_phone):
        flash("اسم جهة الاتصال ورقم الهاتف مطلوبان", "danger")
        return redirect(request.referrer or url_for("content.index"))

    try:
        media = _save_upload(request.files.get("media_file"), current_user.id, content_type)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(request.referrer or url_for("content.index"))

    if content_type in {"photo", "document", "sticker"} and not media and item is None:
        flash("اختر ملفاً لهذا النوع من المحتوى", "danger")
        return redirect(request.referrer or url_for("content.index"))

    if item is None:
        item = ContentItem(owner_id=current_user.id)
        db.session.add(item)

    item.title = title
    item.content_type = content_type
    item.body_html = body_html
    item.body_plain = _strip_html(body_html)
    item.contact_first_name = contact_first_name or None
    item.contact_last_name = contact_last_name or None
    item.contact_phone = contact_phone or None
    item.link_preview = link_preview
    item.status = request.form.get("status", "ready") or "ready"
    if media:
        item.media_id = media.id
    db.session.commit()
    log_action("content.saved", "content", item.id, details=content_type)
    flash("تم حفظ المحتوى", "success")
    return redirect(url_for("content.preview", item_id=item.id))


@bp.get("/<int:item_id>/preview")
@login_required
def preview(item_id):
    item = ContentItem.query.filter_by(id=item_id, owner_id=current_user.id).first_or_404()
    export_channels = SavedChannel.query.filter_by(owner_id=current_user.id, purpose="export", is_active=True).order_by(SavedChannel.id.desc()).all()
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id.asc()).all()
    return render_template("content/preview.html", item=item, export_channels=export_channels, accounts=accounts)


@bp.post("/<int:item_id>/test-send")
@login_required
def test_send(item_id):
    item = ContentItem.query.filter_by(id=item_id, owner_id=current_user.id).first_or_404()
    account_id = request.form.get("account_id", type=int)
    channel_ref = request.form.get("channel_ref", "").strip()
    if not account_id or not channel_ref:
        flash("اختر الحساب وقناة الاختبار", "danger")
        return redirect(url_for("content.preview", item_id=item.id))
    account = TelegramAccount.query.filter_by(id=account_id, owner_id=current_user.id, status="active").first()
    if not account:
        flash("الحساب غير نشط أو غير موجود", "danger")
        return redirect(url_for("content.preview", item_id=item.id))
    app = current_app._get_current_object()
    submit_job(app, send_content_test, item.id, account.id, channel_ref)
    log_action("content.test_send", "content", item.id, details=channel_ref)
    flash("تم إرسال اختبار المحتوى في الخلفية. راقب نافذة التشغيل إذا ظهر خطأ.", "info")
    return redirect(url_for("content.preview", item_id=item.id))


@bp.post("/<int:item_id>/delete")
@login_required
def delete(item_id):
    item = ContentItem.query.filter_by(id=item_id, owner_id=current_user.id).first_or_404()
    item.status = "archived"
    db.session.commit()
    flash("تم أرشفة المحتوى", "success")
    return redirect(url_for("content.index"))
