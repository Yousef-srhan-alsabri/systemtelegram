from datetime import datetime, timezone
import json

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False, index=True)
    role = db.Column(db.String(32), default="operator", nullable=False, index=True)
    permissions_json = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_failed_login_at = db.Column(db.DateTime(timezone=True), nullable=True)
    failed_login_count = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    @property
    def role_label(self):
        from app.permissions import ROLE_LABELS
        return ROLE_LABELS.get(self.role or "operator", self.role or "operator")

    def permissions_set(self):
        from app.permissions import ALL_PERMISSIONS, ROLE_DEFAULTS
        if self.is_admin or self.role == "super_admin":
            return set(ALL_PERMISSIONS)
        if self.permissions_json:
            try:
                data = json.loads(self.permissions_json)
                if isinstance(data, list):
                    return {str(x) for x in data}
            except Exception:
                pass
        return set(ROLE_DEFAULTS.get(self.role or "operator", []))

    def set_permissions(self, permissions):
        from app.permissions import ALL_PERMISSIONS
        allowed = set(ALL_PERMISSIONS)
        self.permissions_json = json.dumps(sorted({p for p in permissions if p in allowed}), ensure_ascii=False)

    def has_permission(self, permission: str) -> bool:
        if self.is_admin or self.role == "super_admin":
            return True
        if not self.is_active:
            return False
        return permission in self.permissions_set()

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class TelegramAccount(db.Model):
    __tablename__ = "telegram_accounts"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_user_id = db.Column(db.BigInteger, nullable=True)
    display_name = db.Column(db.String(255), nullable=True)
    phone_masked = db.Column(db.String(32), nullable=True)
    encrypted_session = db.Column(db.LargeBinary, nullable=True)
    encrypted_proxy = db.Column(db.LargeBinary, nullable=True)
    status = db.Column(db.String(32), default="pending_qr", nullable=False, index=True)
    status_reason = db.Column(db.Text, nullable=True)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class TelegramGroup(db.Model):
    __tablename__ = "telegram_groups"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    telegram_group_id = db.Column(db.BigInteger, nullable=False)
    title = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(255), nullable=True)
    can_publish = db.Column(db.Boolean, default=True, nullable=False)
    selected = db.Column(db.Boolean, default=True, nullable=False)
    last_synced_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    account = db.relationship("TelegramAccount", backref=db.backref("groups", cascade="all, delete-orphan"))
    __table_args__ = (db.UniqueConstraint("account_id", "telegram_group_id", name="uq_account_group"),)


class MessageCampaign(db.Model):
    __tablename__ = "message_campaigns"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    text = db.Column(db.Text, nullable=False)
    content_item_id = db.Column(db.Integer, db.ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True, index=True)
    target_mode = db.Column(db.String(24), default="all_groups", nullable=False)
    batch_size = db.Column(db.Integer, default=30, nullable=False)
    status = db.Column(db.String(32), default="queued", nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    send_window_start = db.Column(db.String(5), nullable=True)
    send_window_end = db.Column(db.String(5), nullable=True)
    repeat_rule = db.Column(db.String(16), default="none", nullable=False)
    source_mode = db.Column(db.String(32), default="content", nullable=False, index=True)
    forward_source_ref = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class MessageTask(db.Model):
    __tablename__ = "message_tasks"
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    text = db.Column(db.Text, nullable=False)
    content_item_id = db.Column(db.Integer, db.ForeignKey("content_items.id", ondelete="SET NULL"), nullable=True, index=True)
    status = db.Column(db.String(32), default="pending", nullable=False, index=True)
    schedule_time = db.Column(db.DateTime(timezone=True), nullable=True)
    repeat_rule = db.Column(db.String(16), default="none", nullable=False)
    source_mode = db.Column(db.String(32), default="content", nullable=False, index=True)
    forward_source_ref = db.Column(db.String(500), nullable=True)
    risk_override = db.Column(db.Boolean, default=False, nullable=False)
    risk_score = db.Column(db.Integer, default=0, nullable=False)
    last_risk_reason = db.Column(db.Text, nullable=True)
    batch_size = db.Column(db.Integer, default=30, nullable=False)
    total_groups = db.Column(db.Integer, default=0, nullable=False)
    sent_count = db.Column(db.Integer, default=0, nullable=False)
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    skipped_count = db.Column(db.Integer, default=0, nullable=False)
    stop_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    account = db.relationship("TelegramAccount")
    content_item = db.relationship("ContentItem")


class CampaignTask(db.Model):
    __tablename__ = "campaign_tasks"
    id = db.Column(db.Integer, primary_key=True)
    campaign_id = db.Column(db.Integer, db.ForeignKey("message_campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    task_id = db.Column(db.Integer, db.ForeignKey("message_tasks.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    campaign = db.relationship("MessageCampaign", backref=db.backref("task_links", cascade="all, delete-orphan"))
    task = db.relationship("MessageTask")


class MessageTaskTarget(db.Model):
    __tablename__ = "message_task_targets"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("message_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("telegram_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(24), default="pending", nullable=False)
    task = db.relationship("MessageTask", backref=db.backref("targets", cascade="all, delete-orphan"))
    group = db.relationship("TelegramGroup")
    __table_args__ = (db.UniqueConstraint("task_id", "group_id", name="uq_task_target"),)


class MessageLog(db.Model):
    __tablename__ = "message_logs"
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.Integer, db.ForeignKey("message_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("telegram_groups.id", ondelete="SET NULL"), nullable=True)
    status = db.Column(db.String(24), nullable=False)
    telegram_message_id = db.Column(db.BigInteger, nullable=True)
    error_code = db.Column(db.String(100), nullable=True)
    error_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    group = db.relationship("TelegramGroup")


class ManagedLink(db.Model):
    __tablename__ = "managed_links"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), nullable=False, unique=True)
    link_type = db.Column(db.String(16), nullable=False, index=True)
    status = db.Column(db.String(24), default="unchecked", nullable=False, index=True)
    status_reason = db.Column(db.String(255), nullable=True)
    checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

class SearchJob(db.Model):
    __tablename__ = "search_jobs"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    query_text = db.Column(db.String(500), nullable=False)
    saudi_only = db.Column(db.Boolean, default=True, nullable=False)
    search_scope = db.Column(db.String(32), default="global_plus_joined", nullable=False, index=True)
    include_public_messages = db.Column(db.Boolean, default=True, nullable=False)
    exclude_system_sources = db.Column(db.Boolean, default=True, nullable=False)
    expanded_queries_json = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), default="queued", nullable=False, index=True)
    max_results = db.Column(db.Integer, default=250, nullable=False)
    groups_count = db.Column(db.Integer, default=0, nullable=False)
    channels_count = db.Column(db.Integer, default=0, nullable=False)
    messages_count = db.Column(db.Integer, default=0, nullable=False)
    bots_count = db.Column(db.Integer, default=0, nullable=False)
    links_count = db.Column(db.Integer, default=0, nullable=False)
    error_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class SearchJobAccount(db.Model):
    __tablename__ = "search_job_accounts"
    id = db.Column(db.Integer, primary_key=True)
    search_job_id = db.Column(db.Integer, db.ForeignKey("search_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    __table_args__ = (db.UniqueConstraint("search_job_id", "account_id", name="uq_search_job_account"),)


class SearchEntity(db.Model):
    __tablename__ = "search_entities"
    id = db.Column(db.Integer, primary_key=True)
    search_job_id = db.Column(db.Integer, db.ForeignKey("search_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type = db.Column(db.String(24), nullable=False, index=True)  # group/channel/bot
    telegram_id = db.Column(db.BigInteger, nullable=True)
    title = db.Column(db.String(500), nullable=False)
    username = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    is_member = db.Column(db.Boolean, default=False, nullable=False)
    is_public = db.Column(db.Boolean, default=False, nullable=False)
    similarity_score = db.Column(db.Integer, default=0, nullable=False)
    saudi_score = db.Column(db.Integer, default=0, nullable=False)
    matched_text = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("search_job_id", "entity_type", "telegram_id", name="uq_search_entity"),)


class SearchMessage(db.Model):
    __tablename__ = "search_messages"
    id = db.Column(db.Integer, primary_key=True)
    search_job_id = db.Column(db.Integer, db.ForeignKey("search_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    peer_id = db.Column(db.BigInteger, nullable=True)
    peer_type = db.Column(db.String(24), nullable=True)
    peer_title = db.Column(db.String(500), nullable=True)
    peer_username = db.Column(db.String(255), nullable=True)
    message_id = db.Column(db.BigInteger, nullable=False)
    message_text = db.Column(db.Text, nullable=True)
    message_date = db.Column(db.DateTime(timezone=True), nullable=True)
    message_url = db.Column(db.Text, nullable=True)
    is_accessible = db.Column(db.Boolean, default=True, nullable=False)
    saudi_score = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("search_job_id", "peer_id", "message_id", name="uq_search_message"),)


class DiscoveredLink(db.Model):
    __tablename__ = "discovered_links"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    search_job_id = db.Column(db.Integer, db.ForeignKey("search_jobs.id", ondelete="CASCADE"), nullable=True, index=True)
    search_message_id = db.Column(db.Integer, db.ForeignKey("search_messages.id", ondelete="SET NULL"), nullable=True, index=True)
    url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), nullable=False, index=True)
    link_type = db.Column(db.String(24), nullable=False, index=True)
    status = db.Column(db.String(24), default="unchecked", nullable=False, index=True)
    source_title = db.Column(db.String(500), nullable=True)
    source_message_url = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("owner_id", "search_job_id", "url_hash", name="uq_discovered_job_link"),)


class WhatsAppScanJob(db.Model):
    __tablename__ = "whatsapp_scan_jobs"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(32), default="queued", nullable=False, index=True)
    scope = db.Column(db.String(32), default="groups_channels", nullable=False, index=True)
    start_date = db.Column(db.DateTime(timezone=True), nullable=True)
    export_mode = db.Column(db.String(24), default="pdf", nullable=False)
    export_channel_ref = db.Column(db.String(500), nullable=True)
    export_account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="SET NULL"), nullable=True)
    messages_scanned = db.Column(db.Integer, default=0, nullable=False)
    chats_scanned = db.Column(db.Integer, default=0, nullable=False)
    links_found = db.Column(db.Integer, default=0, nullable=False)
    unique_links = db.Column(db.Integer, default=0, nullable=False)
    pdf_path = db.Column(db.Text, nullable=True)
    error_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class WhatsAppLink(db.Model):
    __tablename__ = "whatsapp_links"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    scan_job_id = db.Column(db.Integer, db.ForeignKey("whatsapp_scan_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), nullable=False, index=True)
    source_title = db.Column(db.String(500), nullable=True)
    source_username = db.Column(db.String(255), nullable=True)
    source_type = db.Column(db.String(24), nullable=True)
    source_message_id = db.Column(db.BigInteger, nullable=True)
    source_message_url = db.Column(db.Text, nullable=True)
    message_date = db.Column(db.DateTime(timezone=True), nullable=True)
    discovered_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("scan_job_id", "url_hash", name="uq_whatsapp_scan_link"),)


class ExportSetting(db.Model):
    __tablename__ = "export_settings"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="SET NULL"), nullable=True)
    channel_ref = db.Column(db.String(500), nullable=True)
    channel_id = db.Column(db.BigInteger, nullable=True)
    channel_username = db.Column(db.String(255), nullable=True)
    channel_title = db.Column(db.String(500), nullable=True)
    is_active = db.Column(db.Boolean, default=False, nullable=False)
    validated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class JoinSource(db.Model):
    __tablename__ = "join_sources"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_channel_ref = db.Column(db.String(500), nullable=False)
    source_channel_id = db.Column(db.BigInteger, nullable=True)
    source_channel_title = db.Column(db.String(500), nullable=True)
    last_scanned_message_id = db.Column(db.BigInteger, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class JoinScanJob(db.Model):
    __tablename__ = "join_scan_jobs"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id = db.Column(db.Integer, db.ForeignKey("join_sources.id", ondelete="CASCADE"), nullable=True, index=True)
    status = db.Column(db.String(32), default="queued", nullable=False, index=True)
    messages_scanned = db.Column(db.Integer, default=0, nullable=False)
    links_found = db.Column(db.Integer, default=0, nullable=False)
    unique_links = db.Column(db.Integer, default=0, nullable=False)
    error_text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class DiscoveredJoinLink(db.Model):
    __tablename__ = "discovered_join_links"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    scan_job_id = db.Column(db.Integer, db.ForeignKey("join_scan_jobs.id", ondelete="CASCADE"), nullable=True, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_message_id = db.Column(db.BigInteger, nullable=True)
    source_message_url = db.Column(db.Text, nullable=True)
    url = db.Column(db.Text, nullable=False)
    url_hash = db.Column(db.String(64), nullable=False, index=True)
    invite_hash = db.Column(db.String(255), nullable=True)
    username = db.Column(db.String(255), nullable=True)
    entity_type = db.Column(db.String(24), nullable=True)
    entity_title = db.Column(db.String(500), nullable=True)
    entity_id = db.Column(db.BigInteger, nullable=True)
    status = db.Column(db.String(32), default="discovered", nullable=False, index=True)
    requires_approval = db.Column(db.Boolean, default=False, nullable=False)
    is_already_member = db.Column(db.Boolean, default=False, nullable=False)
    error_text = db.Column(db.Text, nullable=True)
    discovered_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    __table_args__ = (db.UniqueConstraint("owner_id", "account_id", "url_hash", name="uq_join_link_owner_account"),)


class JoinJob(db.Model):
    __tablename__ = "join_jobs"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(32), default="queued", nullable=False, index=True)
    selection_mode = db.Column(db.String(32), default="selected", nullable=False)
    auto_continue = db.Column(db.Boolean, default=False, nullable=False)
    batch_pause_seconds = db.Column(db.Integer, default=300, nullable=False)
    max_batches = db.Column(db.Integer, default=1, nullable=False)
    batch_index = db.Column(db.Integer, default=1, nullable=False)
    parent_job_id = db.Column(db.Integer, db.ForeignKey("join_jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    total_links = db.Column(db.Integer, default=0, nullable=False)
    joined_count = db.Column(db.Integer, default=0, nullable=False)
    request_pending_count = db.Column(db.Integer, default=0, nullable=False)
    already_member_count = db.Column(db.Integer, default=0, nullable=False)
    failed_count = db.Column(db.Integer, default=0, nullable=False)
    stopped_reason = db.Column(db.Text, nullable=True)
    rate_limited_until = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    auto_resume = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)


class JoinJobItem(db.Model):
    __tablename__ = "join_job_items"
    id = db.Column(db.Integer, primary_key=True)
    join_job_id = db.Column(db.Integer, db.ForeignKey("join_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    discovered_link_id = db.Column(db.Integer, db.ForeignKey("discovered_join_links.id", ondelete="CASCADE"), nullable=False, index=True)
    status = db.Column(db.String(32), default="approved", nullable=False, index=True)
    error_code = db.Column(db.String(100), nullable=True)
    error_text = db.Column(db.Text, nullable=True)
    next_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    attempted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    __table_args__ = (db.UniqueConstraint("join_job_id", "discovered_link_id", name="uq_join_job_item"),)



class AppSetting(db.Model):
    __tablename__ = "app_settings"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    key = db.Column(db.String(120), nullable=False)
    value = db.Column(db.Text, nullable=True)
    value_type = db.Column(db.String(24), default="string", nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("owner_id", "key", name="uq_app_setting_owner_key"),)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(120), nullable=False, index=True)
    entity_type = db.Column(db.String(120), nullable=True, index=True)
    entity_id = db.Column(db.String(120), nullable=True, index=True)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)


class SavedChannel(db.Model):
    __tablename__ = "saved_channels"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    purpose = db.Column(db.String(32), nullable=False, index=True)  # export/source
    account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="SET NULL"), nullable=True)
    channel_ref = db.Column(db.String(500), nullable=False)
    channel_title = db.Column(db.String(500), nullable=True)
    is_default = db.Column(db.Boolean, default=True, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("owner_id", "purpose", "channel_ref", name="uq_saved_channel_owner_purpose_ref"),)

class ContentMedia(db.Model):
    __tablename__ = "content_media"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    file_path = db.Column(db.Text, nullable=False)
    original_filename = db.Column(db.String(500), nullable=False)
    mime_type = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, default=0, nullable=False)
    media_type = db.Column(db.String(32), default="document", nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class ContentItem(db.Model):
    __tablename__ = "content_items"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(32), default="text", nullable=False, index=True)  # text/photo/document/sticker/contact
    body_html = db.Column(db.Text, nullable=True)
    body_plain = db.Column(db.Text, nullable=True)
    media_id = db.Column(db.Integer, db.ForeignKey("content_media.id", ondelete="SET NULL"), nullable=True)
    contact_first_name = db.Column(db.String(255), nullable=True)
    contact_last_name = db.Column(db.String(255), nullable=True)
    contact_phone = db.Column(db.String(64), nullable=True)
    link_preview = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.String(24), default="ready", nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    media = db.relationship("ContentMedia")



class ChannelSettings(db.Model):
    __tablename__ = "channel_settings"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    channel_ref = db.Column(db.String(500), nullable=True)
    channel_title = db.Column(db.String(500), nullable=True)
    publisher_account_id = db.Column(db.Integer, db.ForeignKey("telegram_accounts.id", ondelete="SET NULL"), nullable=True)
    website_url = db.Column(db.String(500), nullable=True)
    registration_url = db.Column(db.String(500), nullable=True)
    contact_url = db.Column(db.String(500), nullable=True)
    whatsapp_url = db.Column(db.String(500), nullable=True)
    telegram_contact_url = db.Column(db.String(500), nullable=True)
    default_style = db.Column(db.String(64), default="research_professional", nullable=False)
    index_post_id = db.Column(db.Integer, db.ForeignKey("channel_posts.id", ondelete="SET NULL"), nullable=True)
    pinned_post_id = db.Column(db.Integer, db.ForeignKey("channel_posts.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    publisher_account = db.relationship("TelegramAccount", foreign_keys=[publisher_account_id])


class ChannelPostTemplate(db.Model):
    __tablename__ = "channel_post_templates"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    template_type = db.Column(db.String(64), nullable=False, index=True)
    body_html = db.Column(db.Text, nullable=False)
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("owner_id", "template_type", "name", name="uq_channel_template_owner_type_name"),)


class ChannelPost(db.Model):
    __tablename__ = "channel_posts"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("channel_post_templates.id", ondelete="SET NULL"), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    post_type = db.Column(db.String(64), default="custom", nullable=False, index=True)
    body_html = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(32), default="draft", nullable=False, index=True)
    scheduled_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    auto_pin = db.Column(db.Boolean, default=False, nullable=False)
    pinned_at = db.Column(db.DateTime(timezone=True), nullable=True)
    telegram_message_id = db.Column(db.BigInteger, nullable=True, index=True)
    telegram_post_url = db.Column(db.String(500), nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    template = db.relationship("ChannelPostTemplate")


class ChannelPostLink(db.Model):
    __tablename__ = "channel_post_links"
    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    source_post_id = db.Column(db.Integer, db.ForeignKey("channel_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    target_post_id = db.Column(db.Integer, db.ForeignKey("channel_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    label = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    source_post = db.relationship("ChannelPost", foreign_keys=[source_post_id], backref=db.backref("outgoing_links", cascade="all, delete-orphan"))
    target_post = db.relationship("ChannelPost", foreign_keys=[target_post_id])
    __table_args__ = (db.UniqueConstraint("source_post_id", "target_post_id", name="uq_channel_post_link_pair"),)
