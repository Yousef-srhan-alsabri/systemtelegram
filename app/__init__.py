from flask import Flask, abort, request
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import db, login_manager, migrate
from flask_login import current_user
from .permissions import BLUEPRINT_PERMISSIONS, PERMISSION_SPECS, grouped_permission_specs


def create_app(config_object=Config):
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_object)

    if app.config.get("TRUST_PROXY_HEADERS", True):
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"



    STATUS_AR = {
        "pending_qr": "بانتظار رمز QR",
        "waiting_scan": "بانتظار المسح",
        "waiting_2fa": "بانتظار التحقق الثنائي",
        "active": "نشط",
        "qr_expired": "انتهت صلاحية QR",
        "unauthorized": "غير مصرح",
        "restricted": "مقيد",
        "disconnected": "غير متصل",
        "removed": "محذوف",
        "queued": "قيد الانتظار",
        "pending": "معلق",
        "running": "قيد التنفيذ",
        "completed": "مكتمل",
        "failed": "فشل",
        "stopped": "متوقف",
        "paused_rate_limit": "متوقف بسبب حد تيليجرام",
        "paused": "متوقف مؤقتاً",
        "pause_requested": "طلب إيقاف مؤقت",
        "cancelled": "ملغاة",
        "cancel_requested": "طلب إلغاء",
        "finished_with_issues": "اكتملت بملاحظات",
        "risk_hold": "متوقف لحماية الحساب",
        "sending": "جارٍ الإرسال",
        "sent": "تم الإرسال",
        "skipped": "تم التخطي",
        "valid_public": "صالح عام",
        "valid_invite": "رابط دعوة صالح",
        "already_member": "منضم مسبقاً",
        "join_request_pending": "طلب انضمام مرسل",
        "joined": "تم الانضمام",
        "expired": "منتهي",
        "invalid": "غير صالح",
        "unsupported": "غير مدعوم",
        "private_inaccessible": "خاص / غير متاح",
        "check_failed": "فشل الفحص",
        "rate_limited": "توقف بسبب حد تيليجرام",
        "discovered": "مكتشف",
        "checking": "قيد الفحص",
        "approved": "معتمد للتنفيذ",
        "active_link": "نشط",
        "inactive": "غير نشط",
        "global_plus_joined": "عام + حساباتي",
        "global_only": "عام فقط",
        "joined_only": "داخل حساباتي فقط",
        "telegram": "تيليجرام",
        "whatsapp": "واتساب",
        "other": "أخرى",
        "group": "جروب",
        "channel": "قناة",
        "bot": "بوت",
        "message": "رسالة",
        "text": "نص",
        "photo": "صورة",
        "document": "ملف",
        "sticker": "ملصق",
        "contact": "جهة اتصال",
        "ready": "جاهز",
        "draft": "مسودة",
        "archived": "مؤرشف",
        "selected": "المحددة فقط",
        "all_valid": "كل الروابط الجاهزة",
        "groups": "الجروبات فقط",
        "channels": "القنوات فقط",
        "invites": "روابط الدعوة فقط",
        "approval_required": "طلبات الموافقة فقط",
        "all_accounts": "كل الحسابات المحددة",
        "multi_account": "متعدد الحسابات",
        "flood_wait_sleeping": "ينتظر انتهاء حد تيليجرام",
        "draft": "مسودة",
        "publishing": "جارٍ النشر",
        "scheduled": "مجدول",
        "published": "منشور",
        "failed": "فشل",
        "research_opportunity": "فرصة بحثية",
        "opportunity_short": "فرصة مختصرة",
        "opportunity_index": "فهرس الفرص",
        "service": "خدمة",
        "educational": "تعليمي",
        "reminder": "تذكير",
        "welcome": "ترحيبي",
        "faq": "أسئلة شائعة",
        "custom": "مخصص",
        "content": "محتوى مباشر",
        "forward_last": "تحويل آخر رسالة",
    }


    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "telegram-dashboard", "version": "6.9-ux-campaign-planner-search-join"}

    @app.context_processor
    def inject_permissions():
        data = {
            "permission_specs": PERMISSION_SPECS,
            "permission_groups": grouped_permission_specs(),
            "active_campaign_controls": [],
            "active_join_controls": [],
            "active_publish_controls": [],
        }
        try:
            if current_user.is_authenticated:
                from .models import ChannelPost, JoinJob, MessageCampaign
                if current_user.has_permission("campaigns.manage"):
                    data["active_campaign_controls"] = MessageCampaign.query.filter(
                        MessageCampaign.owner_id == current_user.id,
                        MessageCampaign.status.in_(["queued", "running", "paused", "pause_requested", "paused_rate_limit", "scheduled", "risk_hold", "finished_with_issues"])
                    ).order_by(MessageCampaign.id.desc()).limit(3).all()
                data["active_join_controls"] = JoinJob.query.filter(
                    JoinJob.owner_id == current_user.id,
                    JoinJob.status.in_(["queued", "running", "paused", "stopped", "paused_rate_limit"])
                ).order_by(JoinJob.id.desc()).limit(4).all()
                data["active_publish_controls"] = ChannelPost.query.filter(
                    ChannelPost.owner_id == current_user.id,
                    ChannelPost.status.in_(["scheduled", "publishing"])
                ).order_by(ChannelPost.id.desc()).limit(3).all()
        except Exception:
            pass
        return data

    @app.before_request
    def enforce_active_user_and_permissions():
        if not current_user.is_authenticated:
            return None
        if not getattr(current_user, "is_active", True) and request.endpoint not in {"auth.logout"}:
            abort(403)
        required = BLUEPRINT_PERMISSIONS.get(request.blueprint)
        if required and not current_user.has_permission(required):
            abort(403)
        return None

    @app.template_filter("ar_status")
    def ar_status(value):
        if value is None:
            return "—"
        text = str(value)
        return STATUS_AR.get(text, text.replace("_", " "))

    from .models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from .blueprints.auth import bp as auth_bp
    from .blueprints.main import bp as main_bp
    from .blueprints.accounts import bp as accounts_bp
    from .blueprints.groups import bp as groups_bp
    from .blueprints.messages import bp as messages_bp
    from .blueprints.links import bp as links_bp
    from .blueprints.api import bp as api_bp
    from .blueprints.admin import bp as admin_bp
    from .blueprints.search import bp as search_bp
    from .blueprints.join_manager import bp as join_manager_bp
    from .blueprints.whatsapp import bp as whatsapp_bp
    from .blueprints.settings import bp as settings_bp
    from .blueprints.reports import bp as reports_bp
    from .blueprints.activity import bp as activity_bp
    from .blueprints.content import bp as content_bp
    from .blueprints.channel import bp as channel_bp
    from .blueprints.operations import bp as operations_bp

    for blueprint in (auth_bp, main_bp, accounts_bp, groups_bp, messages_bp, content_bp, channel_bp, operations_bp, links_bp, api_bp, admin_bp, search_bp, join_manager_bp, whatsapp_bp, settings_bp, reports_bp, activity_bp):
        app.register_blueprint(blueprint)

    with app.app_context():
        from .schema import ensure_legacy_columns
        db.create_all()
        ensure_legacy_columns()
        db.create_all()

        admin = User.query.filter_by(is_admin=True).first()
        if not admin:
            first_user = User.query.order_by(User.id.asc()).first()
            if first_user:
                first_user.is_admin = True
                first_user.role = "super_admin"
                first_user.is_active = True
                db.session.commit()
            else:
                admin = User(email=app.config["ADMIN_EMAIL"], is_admin=True, role="super_admin", is_active=True)
                admin.set_password(app.config["ADMIN_PASSWORD"])
                db.session.add(admin)
                db.session.commit()

    return app
