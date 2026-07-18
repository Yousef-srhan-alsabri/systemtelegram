from __future__ import annotations

import re
from string import Template

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.background import submit_job
from app.extensions import db
from app.models import ChannelPost, ChannelPostLink, ChannelPostTemplate, ChannelSettings, TelegramAccount
from app.services.audit import log_action
from worker_tasks import publish_channel_post, update_channel_index_post, send_channel_post_test

bp = Blueprint("channel", __name__, url_prefix="/channel")

POST_TYPES = {
    "research_opportunity": "فرصة بحثية",
    "opportunity_short": "فرصة مختصرة",
    "opportunity_index": "فهرس الفرص",
    "service": "خدمة",
    "educational": "تعليمي",
    "reminder": "تذكير",
    "welcome": "ترحيبي / مثبت",
    "faq": "أسئلة شائعة",
    "custom": "مخصص",
}

DEFAULT_TEMPLATES = [
    ("فرصة بحثية تفصيلية", "research_opportunity", """🔬 <b>فرصة بحثية متاحة</b>\n\n<b>التخصص:</b> ${specialty}\n<b>عدد المقاعد:</b> ${total_seats}\n<b>المقاعد المتاحة:</b> ${available_seats}\n<b>نوع المشاركة:</b> ${participation_type}\n<b>الفئة المستهدفة:</b> ${audience}\n<b>مدة العمل:</b> ${duration}\n\n📌 <b>التفاصيل:</b>\n${details}\n\nللتقديم:\n${registration_url}\n\nللاستفسار:\n${contact_url}"""),
    ("فرصة بحثية مختصرة", "opportunity_short", """✅ <b>فرصة بحثية جديدة متاحة</b>\n\n<b>التخصص:</b> ${specialty}\n<b>المقاعد المتاحة:</b> ${available_seats}\n<b>الفئة المستهدفة:</b> ${audience}\n\nللتفاصيل والتقديم:\n${details_url}"""),
    ("فهرس الفرص البحثية", "opportunity_index", """📌 <b>الفرص البحثية المتاحة حالياً</b>\n\n${index_items}\n\nيتم تحديث هذا المنشور دورياً.\n\nللتسجيل العام:\n${registration_url}"""),
    ("خدمة دعم بحثي", "service", """🧩 <b>خدمة دعم بحثي</b>\n\nنساعدك في:\n- تحسين فكرة البحث\n- تجهيز المخطوطة\n- اختيار المجلة المناسبة\n- تجهيز ملفات التقديم\n- الرد على المحكمين\n\nللتواصل:\n${contact_url}"""),
    ("معلومة بحثية سريعة", "educational", """💡 <b>${title}</b>\n\n${details}\n\n<b>الخلاصة:</b>\n${summary}"""),
    ("تذكير بالمقاعد", "reminder", """⏳ <b>تذكير مهم</b>\n\nما زالت هناك مقاعد متاحة في:\n<b>${specialty}</b>\n\n<b>المقاعد المتبقية:</b> ${available_seats}\n<b>آخر موعد:</b> ${deadline}\n\nللتسجيل:\n${registration_url}"""),
    ("منشور ترحيبي مثبت", "welcome", """مرحباً بك في القناة 👋\n\nهنا تجد:\n- فرص بحثية طبية\n- محتوى تعليمي للباحثين\n- خدمات دعم النشر العلمي\n- تحديثات المقاعد المتاحة\n\nابدأ من هنا:\n${index_url}\n\nللتواصل:\n${contact_url}"""),
    ("أسئلة شائعة", "faq", """❓ <b>أسئلة شائعة</b>\n\n<b>كيف أشارك في فرصة بحثية؟</b>\nمن خلال رابط التسجيل أو التواصل المرفق في كل منشور.\n\n<b>هل المقاعد محدودة؟</b>\nنعم، يتم تحديث المقاعد حسب توفر كل فرصة.\n\n<b>أين أجد الفرص الحالية؟</b>\n${index_url}"""),
]


def _settings() -> ChannelSettings:
    settings = ChannelSettings.query.filter_by(owner_id=current_user.id).first()
    if not settings:
        settings = ChannelSettings(owner_id=current_user.id)
        db.session.add(settings)
        db.session.commit()
    return settings


def _ensure_templates() -> None:
    existing = {(t.template_type, t.name) for t in ChannelPostTemplate.query.filter_by(owner_id=current_user.id).all()}
    changed = False
    for name, template_type, body in DEFAULT_TEMPLATES:
        if (template_type, name) not in existing:
            db.session.add(ChannelPostTemplate(owner_id=current_user.id, name=name, template_type=template_type, body_html=body, is_system=True))
            changed = True
    if changed:
        db.session.commit()


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _base_context(settings: ChannelSettings) -> dict[str, str]:
    return {
        "website_url": settings.website_url or "",
        "registration_url": settings.registration_url or "",
        "contact_url": settings.contact_url or settings.telegram_contact_url or settings.whatsapp_url or "",
        "whatsapp_url": settings.whatsapp_url or "",
        "telegram_contact_url": settings.telegram_contact_url or "",
        "index_url": _index_url(settings) or "سيتم تحديث رابط الفهرس بعد النشر",
    }


def _render_template_body(body: str, values: dict[str, str]) -> str:
    safe_values = {k: _clean(v) for k, v in values.items()}
    return Template(body).safe_substitute(safe_values)


def _index_url(settings: ChannelSettings) -> str | None:
    if not settings.index_post_id:
        return None
    post = db.session.get(ChannelPost, settings.index_post_id)
    return post.telegram_post_url if post else None


def _public_channel_username(channel_ref: str | None) -> str | None:
    value = (channel_ref or "").strip()
    if not value:
        return None
    value = value.rstrip("/")
    m = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)$", value)
    if m:
        return m.group(1)
    if value.startswith("@"):
        return value[1:]
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
        return value
    return None


@bp.get("/")
@login_required
def dashboard():
    _ensure_templates()
    settings = _settings()
    posts = ChannelPost.query.filter_by(owner_id=current_user.id).order_by(ChannelPost.id.desc()).limit(30).all()
    templates = ChannelPostTemplate.query.filter_by(owner_id=current_user.id).order_by(ChannelPostTemplate.template_type.asc(), ChannelPostTemplate.id.asc()).all()
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id.asc()).all()
    return render_template("channel/dashboard.html", settings=settings, posts=posts, templates=templates, accounts=accounts, post_types=POST_TYPES)


@bp.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    settings = _settings()
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id.asc()).all()
    if request.method == "POST":
        settings.channel_ref = _clean(request.form.get("channel_ref")) or None
        settings.channel_title = _clean(request.form.get("channel_title")) or None
        settings.publisher_account_id = request.form.get("publisher_account_id", type=int) or None
        settings.website_url = _clean(request.form.get("website_url")) or None
        settings.registration_url = _clean(request.form.get("registration_url")) or None
        settings.contact_url = _clean(request.form.get("contact_url")) or None
        settings.whatsapp_url = _clean(request.form.get("whatsapp_url")) or None
        settings.telegram_contact_url = _clean(request.form.get("telegram_contact_url")) or None
        settings.default_style = _clean(request.form.get("default_style")) or "research_professional"
        db.session.commit()
        log_action("channel.settings.saved", "channel_settings", settings.id, details=settings.channel_ref or "")
        flash("تم حفظ إعدادات القناة", "success")
        return redirect(url_for("channel.dashboard"))
    return render_template("channel/settings.html", settings=settings, accounts=accounts)


@bp.route("/posts/new", methods=["GET", "POST"])
@login_required
def create_post():
    _ensure_templates()
    settings = _settings()
    templates = ChannelPostTemplate.query.filter_by(owner_id=current_user.id).order_by(ChannelPostTemplate.template_type.asc(), ChannelPostTemplate.id.asc()).all()
    if request.method == "POST":
        title = _clean(request.form.get("title"))
        post_type = _clean(request.form.get("post_type")) or "custom"
        template_id = request.form.get("template_id", type=int)
        manual_body = _clean(request.form.get("body_html"))
        template = ChannelPostTemplate.query.filter_by(id=template_id, owner_id=current_user.id).first() if template_id else None
        values = _base_context(settings)
        for key in ["specialty", "total_seats", "available_seats", "participation_type", "audience", "duration", "details", "details_url", "summary", "deadline"]:
            values[key] = _clean(request.form.get(key))
        values["title"] = title
        if template and not manual_body:
            body_html = _render_template_body(template.body_html, values)
            post_type = template.template_type
        else:
            body_html = manual_body
        if not title or not body_html:
            flash("العنوان ونص المنشور مطلوبان", "danger")
            return redirect(request.referrer or url_for("channel.create_post"))
        post = ChannelPost(owner_id=current_user.id, template_id=template.id if template else None, title=title, post_type=post_type, body_html=body_html, status="draft")
        db.session.add(post)
        db.session.commit()
        log_action("channel.post.created", "channel_post", post.id, details=post_type)
        flash("تم إنشاء مسودة المنشور", "success")
        return redirect(url_for("channel.post_detail", post_id=post.id))
    return render_template("channel/post_form.html", post=None, templates=templates, settings=settings, post_types=POST_TYPES)


@bp.route("/posts/<int:post_id>", methods=["GET", "POST"])
@login_required
def post_detail(post_id):
    post = ChannelPost.query.filter_by(id=post_id, owner_id=current_user.id).first_or_404()
    settings = _settings()
    accounts = TelegramAccount.query.filter_by(owner_id=current_user.id, status="active").order_by(TelegramAccount.id.asc()).all()
    if request.method == "POST":
        post.title = _clean(request.form.get("title")) or post.title
        post.post_type = _clean(request.form.get("post_type")) or post.post_type
        post.body_html = _clean(request.form.get("body_html")) or post.body_html
        db.session.commit()
        flash("تم حفظ تعديل المنشور", "success")
        return redirect(url_for("channel.post_detail", post_id=post.id))
    linked = ChannelPostLink.query.filter_by(source_post_id=post.id).order_by(ChannelPostLink.sort_order.asc(), ChannelPostLink.id.asc()).all()
    published_posts = ChannelPost.query.filter(ChannelPost.owner_id == current_user.id, ChannelPost.id != post.id, ChannelPost.status == "published").order_by(ChannelPost.published_at.desc()).all()
    return render_template("channel/post_detail.html", post=post, settings=settings, accounts=accounts, post_types=POST_TYPES, linked=linked, published_posts=published_posts)


@bp.post("/posts/<int:post_id>/publish")
@login_required
def publish(post_id):
    post = ChannelPost.query.filter_by(id=post_id, owner_id=current_user.id).first_or_404()
    settings = _settings()
    account_id = request.form.get("account_id", type=int) or settings.publisher_account_id
    channel_ref = _clean(request.form.get("channel_ref")) or settings.channel_ref
    if not account_id or not channel_ref:
        flash("حدد حساب النشر ورابط القناة أولاً", "danger")
        return redirect(url_for("channel.post_detail", post_id=post.id))
    app = current_app._get_current_object()
    submit_job(app, publish_channel_post, post.id, account_id, channel_ref)
    flash("تمت إضافة المنشور إلى قائمة النشر في الخلفية", "info")
    return redirect(url_for("channel.post_detail", post_id=post.id))


@bp.post("/posts/<int:post_id>/test")
@login_required
def test(post_id):
    post = ChannelPost.query.filter_by(id=post_id, owner_id=current_user.id).first_or_404()
    account_id = request.form.get("account_id", type=int)
    channel_ref = _clean(request.form.get("channel_ref"))
    if not account_id or not channel_ref:
        flash("حدد حساب وقناة الاختبار", "danger")
        return redirect(url_for("channel.post_detail", post_id=post.id))
    app = current_app._get_current_object()
    submit_job(app, send_channel_post_test, post.id, account_id, channel_ref)
    flash("تم إرسال التجربة في الخلفية", "info")
    return redirect(url_for("channel.post_detail", post_id=post.id))


@bp.post("/posts/<int:post_id>/set-index")
@login_required
def set_index(post_id):
    post = ChannelPost.query.filter_by(id=post_id, owner_id=current_user.id).first_or_404()
    settings = _settings()
    settings.index_post_id = post.id
    db.session.commit()
    flash("تم تحديد هذا المنشور كفهرس رئيسي", "success")
    return redirect(url_for("channel.post_detail", post_id=post.id))


@bp.post("/posts/<int:post_id>/links")
@login_required
def add_link(post_id):
    source = ChannelPost.query.filter_by(id=post_id, owner_id=current_user.id).first_or_404()
    target_id = request.form.get("target_post_id", type=int)
    target = ChannelPost.query.filter_by(id=target_id, owner_id=current_user.id, status="published").first()
    if not target:
        flash("اختر منشوراً منشوراً صالحاً", "danger")
        return redirect(url_for("channel.post_detail", post_id=source.id))
    exists = ChannelPostLink.query.filter_by(source_post_id=source.id, target_post_id=target.id).first()
    if not exists:
        db.session.add(ChannelPostLink(owner_id=current_user.id, source_post_id=source.id, target_post_id=target.id, label=_clean(request.form.get("label")) or target.title))
        db.session.commit()
    flash("تم ربط المنشور", "success")
    return redirect(url_for("channel.post_detail", post_id=source.id))


@bp.post("/links/<int:link_id>/delete")
@login_required
def delete_link(link_id):
    link = ChannelPostLink.query.filter_by(id=link_id, owner_id=current_user.id).first_or_404()
    source_id = link.source_post_id
    db.session.delete(link)
    db.session.commit()
    flash("تم حذف الرابط", "success")
    return redirect(url_for("channel.post_detail", post_id=source_id))


@bp.post("/index/update")
@login_required
def update_index():
    settings = _settings()
    if not settings.index_post_id:
        flash("حدد منشور الفهرس أولاً", "danger")
        return redirect(url_for("channel.dashboard"))
    if not settings.publisher_account_id or not settings.channel_ref:
        flash("أكمل إعدادات القناة وحساب النشر", "danger")
        return redirect(url_for("channel.settings"))
    app = current_app._get_current_object()
    submit_job(app, update_channel_index_post, settings.id)
    flash("تم تشغيل تحديث منشور الفهرس في الخلفية", "info")
    return redirect(url_for("channel.dashboard"))
