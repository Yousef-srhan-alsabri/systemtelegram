import asyncio
import base64
import io
import os
import random
import re
import json

import qrcode
from datetime import datetime, timezone, timedelta

from flask import current_app
from sqlalchemy.dialects.postgresql import insert as pg_insert
from telethon import functions, types
from telethon.errors import (
    ChatWriteForbiddenError,
    FloodWaitError,
    PeerFloodError,
    SessionPasswordNeededError,
    UserBannedInChannelError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession

from app.extensions import db
from app.background import get_account_lock, set_qr_state
from app.models import ContentItem, MessageLog, MessageTask, TelegramAccount, TelegramGroup, ChannelPost, ChannelSettings, utcnow
from app.services.crypto import CryptoService
from app.services.telegram import build_client
from app.services.settings import get_bool, get_int


def _crypto():
    return CryptoService(current_app.config["SESSION_ENCRYPTION_KEYS"])


def _content_file_path(content):
    if content and content.media and content.media.file_path:
        return content.media.file_path
    return None


def _normalize_contact_phone(phone):
    """Keep Telegram contact phone numbers in an international-friendly format."""
    return (phone or "").strip().replace(" ", "")


async def send_real_contact(client, entity, *, phone_number, first_name, last_name="", vcard=""):
    """Send a real Telegram contact card, not a plain text fallback.

    This uses Telegram raw API InputMediaContact so the recipient sees the same
    contact-card style that appears when sending a contact from the Telegram app.
    """
    phone_number = _normalize_contact_phone(phone_number)
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()

    if not phone_number or not first_name:
        raise ValueError("بيانات جهة الاتصال غير مكتملة")

    peer = await client.get_input_entity(entity)

    return await client(
        functions.messages.SendMediaRequest(
            peer=peer,
            media=types.InputMediaContact(
                phone_number=phone_number,
                first_name=first_name,
                last_name=last_name,
                vcard=vcard or "",
            ),
            message="",
            random_id=random.randrange(-(2 ** 63), 2 ** 63),
        )
    )




def _extract_sent_message_id(result):
    """Return a Telegram message id from normal Message or raw Updates results."""
    direct_id = getattr(result, "id", None)
    if direct_id is not None:
        return direct_id

    for update in getattr(result, "updates", []) or []:
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if message_id is not None:
            return message_id

    return None

async def send_campaign_content(client, entity, content, fallback_text=""):
    """Send one saved campaign content item, falling back to plain/html text.

    Supported types: text, photo, document, sticker, contact.
    Contacts are sent as real Telegram contact cards through InputMediaContact.
    """
    if not content:
        return await client.send_message(entity, fallback_text or " ", link_preview=False)

    body = content.body_html or fallback_text or content.body_plain or ""
    ctype = content.content_type or "text"
    file_path = _content_file_path(content)

    if ctype == "text":
        return await client.send_message(
            entity,
            body or " ",
            parse_mode="html",
            link_preview=bool(content.link_preview),
        )

    if ctype == "photo" and file_path:
        return await client.send_file(
            entity,
            file_path,
            caption=body or "",
            parse_mode="html",
        )

    if ctype == "document" and file_path:
        return await client.send_file(
            entity,
            file_path,
            caption=body or "",
            parse_mode="html",
            force_document=True,
        )

    if ctype == "sticker" and file_path:
        return await client.send_file(entity, file_path)

    if ctype == "contact":
        return await send_real_contact(
            client,
            entity,
            phone_number=content.contact_phone,
            first_name=content.contact_first_name,
            last_name=content.contact_last_name or "",
            vcard="",
        )

    return await client.send_message(entity, body or fallback_text or " ", parse_mode="html", link_preview=False)


async def forward_latest_source_message(client, entity, source_ref):
    """Forward the latest message from a saved user/chat/channel source to the target."""
    source_ref = (source_ref or "").strip()
    if not source_ref:
        raise ValueError("مصدر التحويل غير محدد")
    source_entity = await client.get_entity(source_ref)
    latest = None
    async for message in client.iter_messages(source_entity, limit=1):
        latest = message
        break
    if latest is None:
        raise ValueError("لا توجد رسالة أخيرة قابلة للتحويل في المصدر")
    return await client.forward_messages(entity, latest)


def _task_risk_should_hold(task, owner_id):
    if getattr(task, "risk_override", False):
        return False, None
    sent_limit = get_int(owner_id, "CAMPAIGN_RISK_SENT_LIMIT", current_app.config.get("CAMPAIGN_RISK_SENT_LIMIT", 120))
    failure_limit = get_int(owner_id, "CAMPAIGN_RISK_FAILURE_LIMIT", current_app.config.get("CAMPAIGN_RISK_FAILURE_LIMIT", 8))
    if int(task.sent_count or 0) >= sent_limit:
        return True, f"وصل الحساب إلى حد خطر النشر اليومي/المهمة: {sent_limit} رسالة"
    if int(task.failed_count or 0) >= failure_limit:
        return True, f"وصل الحساب إلى حد فشل مرتفع: {failure_limit} أخطاء"
    return False, None


def send_content_test(content_id, account_id, channel_ref):
    return asyncio.run(_send_content_test(content_id, account_id, channel_ref))


async def _send_content_test(content_id, account_id, channel_ref):
    account = db.session.get(TelegramAccount, account_id)
    content = db.session.get(ContentItem, content_id)
    if not account or not content or account.status != "active":
        return {"status": "missing_or_inactive"}
    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    client = build_client(current_app.config["TELEGRAM_API_ID"], current_app.config["TELEGRAM_API_HASH"], session, proxy)
    try:
        await client.connect()
        entity = await client.get_entity(channel_ref)
        msg = await send_campaign_content(client, entity, content)
        return {"status": "sent", "message_id": getattr(msg, "id", None)}
    finally:
        await client.disconnect()


def qr_login_task(account_id, token):
    return asyncio.run(_qr_login(account_id, token))


async def _qr_login(account_id, token):
    set_qr_state(token, status="preparing", account_id=account_id)

    account = db.session.get(TelegramAccount, account_id)
    if not account:
        set_qr_state(token, status="error", error="الحساب غير موجود")
        return

    client = build_client(current_app.config["TELEGRAM_API_ID"], current_app.config["TELEGRAM_API_HASH"])
    try:
        await client.connect()
        qr = await client.qr_login()
        qr_image = qrcode.make(qr.url)
        buffer = io.BytesIO()
        qr_image.save(buffer, format="PNG")
        qr_data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        set_qr_state(token, status="waiting_scan", qr_url=qr.url, qr_image=qr_data_url)
        try:
            await asyncio.wait_for(qr.wait(), timeout=current_app.config["QR_LOGIN_TIMEOUT_SECONDS"])
        except SessionPasswordNeededError:
            account.status = "waiting_2fa"
            account.status_reason = "الحساب يتطلب كلمة مرور التحقق بخطوتين"
            db.session.commit()
            set_qr_state(token, status="waiting_2fa", error=account.status_reason)
            return
        except asyncio.TimeoutError:
            account.status = "qr_expired"
            account.status_reason = "انتهت صلاحية رمز QR"
            db.session.commit()
            set_qr_state(token, status="expired")
            return

        me = await client.get_me()
        session_string = StringSession.save(client.session)
        account.encrypted_session = _crypto().encrypt(session_string)
        account.telegram_user_id = me.id
        account.display_name = " ".join(x for x in [getattr(me, "first_name", None), getattr(me, "last_name", None)] if x) or getattr(me, "username", None)
        account.phone_masked = ("***" + me.phone[-4:]) if getattr(me, "phone", None) else None
        account.status = "active"
        account.status_reason = None
        account.last_seen_at = utcnow()
        db.session.commit()
        set_qr_state(token, status="authorized", account_id=account.id)
    except Exception as exc:
        account.status = "disconnected"
        account.status_reason = type(exc).__name__
        db.session.commit()
        set_qr_state(token, status="error", error=type(exc).__name__)
    finally:
        await client.disconnect()


def sync_groups_task(account_id):
    return asyncio.run(_sync_groups(account_id))


async def _sync_groups(account_id):
    account = db.session.get(TelegramAccount, account_id)
    if not account or not account.encrypted_session:
        return {"status": "ignored"}
    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    client = build_client(current_app.config["TELEGRAM_API_ID"], current_app.config["TELEGRAM_API_HASH"], session, proxy)
    count = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            account.status = "unauthorized"
            db.session.commit()
            return {"status": "unauthorized"}
        dialogs = await client.get_dialogs(limit=None)
        for dialog in dialogs:
            if not dialog.is_group:
                continue
            group = TelegramGroup.query.filter_by(account_id=account.id, telegram_group_id=int(dialog.id)).first()
            if not group:
                group = TelegramGroup(account_id=account.id, telegram_group_id=int(dialog.id), title=dialog.name or "مجموعة بلا اسم", can_publish=True, selected=True)
                db.session.add(group)
            else:
                group.title = dialog.name or group.title
            group.username = getattr(dialog.entity, "username", None)
            group.can_publish = True
            group.selected = True
            group.last_synced_at = utcnow()
            count += 1
        account.last_seen_at = utcnow()
        db.session.commit()
        return {"status": "ok", "count": count}
    finally:
        await client.disconnect()


def execute_message_task(task_id):
    task = db.session.get(MessageTask, task_id)
    if not task:
        return {"status": "missing"}
    lock = get_account_lock(task.account_id)
    if not lock.acquire(blocking=False):
        task.status = "queued"
        task.stop_reason = "هناك مهمة أخرى تعمل على الحساب"
        db.session.commit()
        return {"status": "queued"}
    try:
        return asyncio.run(_execute(task_id))
    finally:
        lock.release()


async def _execute(task_id):
    task = db.session.get(MessageTask, task_id)
    account = db.session.get(TelegramAccount, task.account_id)
    if not account or account.status != "active" or not account.encrypted_session:
        task.status = "stopped"
        task.stop_reason = "الحساب غير نشط"
        db.session.commit()
        return {"status": "stopped"}

    targets = [target for target in task.targets if target.group.can_publish]
    max_targets = get_int(account.owner_id, "MAX_TARGETS_PER_ACCOUNT_TASK", current_app.config["MAX_TARGETS_PER_ACCOUNT_TASK"])
    if len(targets) > max_targets:
        targets = targets[:max_targets]
        task.total_groups = len(targets)
        db.session.commit()

    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    client = build_client(
        current_app.config["TELEGRAM_API_ID"],
        current_app.config["TELEGRAM_API_HASH"],
        session,
        proxy,
    )
    content = db.session.get(ContentItem, task.content_item_id) if getattr(task, "content_item_id", None) else None
    # Do not start if the dashboard paused or cancelled the task before the worker got it.
    if task.status in {"paused", "pause_requested"}:
        task.status = "paused"
        task.stop_reason = task.stop_reason or "تم إيقاف المهمة مؤقتاً قبل بدء التنفيذ"
        db.session.commit()
        return {"status": "paused"}
    if task.status in {"cancelled", "cancel_requested"}:
        task.status = "cancelled"
        task.stop_reason = task.stop_reason or "تم إلغاء المهمة قبل بدء التنفيذ"
        db.session.commit()
        return {"status": "cancelled"}

    task.status = "running"
    task.stop_reason = None
    db.session.commit()

    try:
        await client.connect()
        batch_size = max(1, int(task.batch_size or get_int(account.owner_id, "DEFAULT_BATCH_SIZE", current_app.config["DEFAULT_BATCH_SIZE"])))
        batches = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
        processed = 0

        for batch in batches:
            for target in batch:
                # Refresh task state before each Telegram send so Pause/Cancel from the UI
                # takes effect at the first safe point: after the current send, before the next one.
                db.session.refresh(task)
                if task.status in {"pause_requested", "paused"}:
                    task.status = "paused"
                    task.stop_reason = "تم إيقاف الحملة مؤقتاً"
                    db.session.commit()
                    return {"status": "paused"}
                if task.status in {"cancel_requested", "cancelled"}:
                    task.status = "cancelled"
                    task.stop_reason = "تم إلغاء الحملة"
                    db.session.commit()
                    return {"status": "cancelled"}

                if target.status == "sent":
                    processed += 1
                    continue

                target.status = "sending"
                db.session.commit()

                try:
                    should_hold, risk_reason = _task_risk_should_hold(task, account.owner_id)
                    if should_hold:
                        task.status = "risk_hold"
                        task.last_risk_reason = risk_reason
                        task.stop_reason = risk_reason
                        db.session.commit()
                        return {"status": "risk_hold", "reason": risk_reason}

                    if getattr(task, "source_mode", "content") == "forward_last":
                        message = await forward_latest_source_message(
                            client,
                            target.group.telegram_group_id,
                            task.forward_source_ref,
                        )
                    else:
                        message = await send_campaign_content(
                            client,
                            target.group.telegram_group_id,
                            content,
                            fallback_text=task.text,
                        )
                except FloodWaitError as exc:
                    task.status = "paused_rate_limit"
                    task.stop_reason = f"Telegram طلب الانتظار {exc.seconds} ثانية"
                    db.session.commit()
                    return {"status": task.status, "wait_seconds": exc.seconds}
                except (PeerFloodError, UserBannedInChannelError, UserDeactivatedError) as exc:
                    account.status = "restricted"
                    account.status_reason = type(exc).__name__
                    task.status = "stopped"
                    task.stop_reason = type(exc).__name__
                    db.session.add(MessageLog(
                        task_id=task.id,
                        group_id=target.group_id,
                        status="failed",
                        error_code=type(exc).__name__,
                        error_text=str(exc)[:1000],
                    ))
                    db.session.commit()
                    return {"status": "stopped"}
                except ChatWriteForbiddenError as exc:
                    target.status = "skipped"
                    task.skipped_count += 1
                    db.session.add(MessageLog(
                        task_id=task.id,
                        group_id=target.group_id,
                        status="skipped",
                        error_code=type(exc).__name__,
                        error_text=str(exc)[:1000],
                    ))
                    db.session.commit()
                except Exception as exc:
                    target.status = "failed"
                    task.failed_count += 1
                    db.session.add(MessageLog(
                        task_id=task.id,
                        group_id=target.group_id,
                        status="failed",
                        error_code=type(exc).__name__,
                        error_text=str(exc)[:1000],
                    ))
                    db.session.commit()
                else:
                    target.status = "sent"
                    task.sent_count += 1
                    db.session.add(MessageLog(
                        task_id=task.id,
                        group_id=target.group_id,
                        status="sent",
                        telegram_message_id=_extract_sent_message_id(message),
                    ))
                    db.session.commit()

                processed += 1
                if processed < len(targets):
                    delay = random.uniform(
                        get_int(account.owner_id, "MESSAGE_DELAY_MIN_SECONDS", current_app.config["MESSAGE_DELAY_MIN_SECONDS"]),
                        get_int(account.owner_id, "MESSAGE_DELAY_MAX_SECONDS", current_app.config["MESSAGE_DELAY_MAX_SECONDS"]),
                    )
                    await asyncio.sleep(delay)

        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        return {"status": "completed"}
    finally:
        await client.disconnect()

# ---------------------------------------------------------------------------
# Search Explorer, export, and bounded Join Manager
# ---------------------------------------------------------------------------
import hashlib
from sqlalchemy.exc import IntegrityError
from telethon import functions
from telethon import functions, types
from telethon.errors import (
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    InviteRequestSentError,
    UserAlreadyParticipantError,
)

from app.models import (
    DiscoveredJoinLink,
    DiscoveredLink,
    ExportSetting,
    JoinJob,
    JoinJobItem,
    JoinScanJob,
    JoinSource,
    SearchEntity,
    SearchJob,
    SearchJobAccount,
    SearchMessage,
    WhatsAppLink,
    WhatsAppScanJob,
)
from app.services.discovery import (
    entity_kind,
    entity_title,
    parse_telegram_link,
    public_message_url,
    saudi_score,
    similarity_score,
    telegram_links_from_text,
)
from app.services.links import extract_links




def _extract_message_links(message):
    """Extract links from normal text, text-url entities, URL entities, and inline buttons.

    Telegram channels often hide links behind clickable text or inline keyboard buttons.
    Earlier builds only inspected message.message, so Join Manager could scan messages
    successfully but discover zero links.
    """
    text = getattr(message, "message", None) or ""
    candidates = [text]

    # Hidden links inside message entities, including MessageEntityTextUrl.
    for entity in getattr(message, "entities", None) or []:
        direct = getattr(entity, "url", None)
        if direct:
            candidates.append(direct)
            continue
        offset = getattr(entity, "offset", None)
        length = getattr(entity, "length", None)
        if offset is not None and length:
            # Telegram offsets use UTF-16 code units.  Normal Python slicing
            # misses hidden URLs after Arabic text or emoji unless converted.
            encoded = text.encode("utf-16-le")
            candidates.append(encoded[offset * 2:(offset + length) * 2].decode("utf-16-le", "ignore"))

    # Links inside inline keyboard buttons.
    reply_markup = getattr(message, "reply_markup", None)
    if reply_markup:
        for row in getattr(reply_markup, "rows", []) or []:
            for button in getattr(row, "buttons", []) or []:
                url = getattr(button, "url", None)
                if url:
                    candidates.append(url)

    merged = "\n".join(candidates)
    return extract_links(merged)


def _prepare_join_link_for_execution(row):
    """Classify a Telegram URL locally without consuming Telegram API quota.

    Source channels often contain hundreds of links in one post.  Checking every
    one via the API triggers FloodWait and incorrectly made valid links look bad.
    The worker still performs the authoritative check just before joining.
    """
    target = parse_telegram_link(row.url)
    row.error_text = None
    row.checked_at = utcnow()
    if target.kind == "invite":
        row.invite_hash = target.value
        row.status = "valid_invite"
    elif target.kind == "username":
        row.username = target.value
        row.status = "valid_public"
    else:
        row.status = "unsupported"

def _account_client(account):
    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    return build_client(
        current_app.config["TELEGRAM_API_ID"],
        current_app.config["TELEGRAM_API_HASH"],
        session,
        proxy,
    )


def _safe_commit():
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False



def _username_from_ref(ref):
    value = (ref or "").strip().rstrip("/")
    match = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)$", value, re.I)
    if match:
        return match.group(1).lower()
    if value.startswith("@"):
        return value[1:].lower()
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
        return value.lower()
    return None


def _system_source_identity_sets(owner_id):
    usernames = set()
    ids = set()
    setting = ExportSetting.query.filter_by(owner_id=owner_id).first()
    if setting:
        username = _username_from_ref(setting.channel_ref) or (setting.channel_username or "").lower()
        if username:
            usernames.add(username)
        if setting.channel_id:
            ids.add(abs(int(setting.channel_id)))
    for source in JoinSource.query.filter_by(owner_id=owner_id).all():
        username = _username_from_ref(source.source_channel_ref)
        if username:
            usernames.add(username)
        if source.source_channel_id:
            ids.add(abs(int(source.source_channel_id)))
    return usernames, ids


def _is_system_source(username=None, entity_id=None, usernames=None, ids=None):
    usernames = usernames or set()
    ids = ids or set()
    if username and username.lower() in usernames:
        return True
    if entity_id:
        try:
            return abs(int(entity_id)) in ids
        except Exception:
            return False
    return False


def _expanded_search_terms(query):
    base = (query or "").strip()
    terms = [base] if base else []
    lower = base.lower()
    extras = []
    if any(token in lower for token in ["بحث", "ابحاث", "أبحاث", "research", "medical", "طب"]):
        extras.extend(["بحث علمي", "أبحاث طبية", "نشر علمي", "medical research", "clinical research", "research group", "publication research"])
    words = [w for w in re.split(r"\s+", base) if len(w) > 2]
    if len(words) >= 2:
        extras.extend([" ".join(words[:2]), " ".join(reversed(words[:2]))])
    seen = set()
    result = []
    for term in terms + extras:
        key = term.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(term.strip())
    return result[:8]

def execute_search_job(job_id):
    return asyncio.run(_execute_search_job(job_id))


async def _execute_search_job(job_id):
    job = db.session.get(SearchJob, job_id)
    if not job:
        return {"status": "missing"}

    job.status = "running"
    job.started_at = utcnow()
    job.error_text = None
    db.session.commit()

    account_ids = [row.account_id for row in SearchJobAccount.query.filter_by(search_job_id=job.id).all()]
    accounts = TelegramAccount.query.filter(TelegramAccount.id.in_(account_ids), TelegramAccount.status == "active").all()
    if not accounts:
        job.status = "failed"
        job.error_text = "لا توجد حسابات نشطة للبحث"
        job.completed_at = utcnow()
        db.session.commit()
        return {"status": "failed"}

    max_per_account = max(10, min(job.max_results, get_int(job.owner_id, "SEARCH_MAX_RESULTS", current_app.config["SEARCH_MAX_RESULTS"])))
    public_limit = max(10, min(max_per_account, get_int(job.owner_id, "SEARCH_GLOBAL_LIMIT", current_app.config.get("SEARCH_GLOBAL_LIMIT", 100))))
    threshold = get_int(job.owner_id, "SEARCH_SAUDI_THRESHOLD", current_app.config["SEARCH_SAUDI_THRESHOLD"])
    scope = getattr(job, "search_scope", None) or "global_plus_joined"
    include_joined = scope in {"joined_only", "global_plus_joined"}
    include_global = scope in {"global_only", "global_plus_joined"}
    include_public_messages = bool(getattr(job, "include_public_messages", True)) and include_global
    exclude_system_sources = bool(getattr(job, "exclude_system_sources", True))
    base_system_usernames, base_system_ids = _system_source_identity_sets(job.owner_id) if exclude_system_sources else (set(), set())
    query_terms = _expanded_search_terms(job.query_text) if getattr(job, "expanded_queries_json", None) else [job.query_text]
    try:
        job.expanded_queries_json = json.dumps(query_terms, ensure_ascii=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
    errors = []

    for account in accounts:
        client = _account_client(account)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                errors.append(f"الحساب {account.id}: غير مصرح")
                continue

            system_usernames = set(base_system_usernames)
            system_ids = set(base_system_ids)
            if exclude_system_sources:
                await _resolve_system_sources_with_client(client, job.owner_id, system_usernames, system_ids)

            dialogs = []
            member_ids = set()
            # A global search does not need the account's dialog list.  Loading it
            # made "global only" appear to search local chats first (and delayed
            # the actual Telegram-wide request on large accounts).
            if include_joined:
                try:
                    dialog_limit = max(50, min(
                        get_int(job.owner_id, "SEARCH_JOINED_DIALOG_LIMIT", current_app.config.get("SEARCH_JOINED_DIALOG_LIMIT", 500)),
                        2000,
                    ))
                    dialogs = await client.get_dialogs(limit=dialog_limit)
                    member_ids = {int(dialog.id) for dialog in dialogs}
                except Exception as exc:
                    errors.append(f"قراءة محادثات الحساب {account.id}: {type(exc).__name__}")

            # 1) Joined/local entities and messages. This is the reliable membership-aware search.
            if include_joined:
                per_dialog_limit = max(5, min(50, max_per_account // 4 or 10))
                for dialog in dialogs:
                    kind = entity_kind(dialog.entity)
                    if kind not in {"group", "channel"}:
                        continue
                    title = dialog.name or entity_title(dialog.entity)
                    username = getattr(dialog.entity, "username", None)
                    if _is_system_source(username=username, entity_id=getattr(dialog.entity, "id", None), usernames=system_usernames, ids=system_ids):
                        continue
                    sim = similarity_score(job.query_text, title, username)
                    score = saudi_score(title, username)
                    # Saudi score is a ranking signal, not an exclusion rule.
                    # The old condition made normal global queries return zero.
                    matches_title = sim >= 42
                    if matches_title:
                        _upsert_search_entity(job, dialog.entity, kind, title, username, True, sim, score)

                    # Per-chat history search is expensive.  Limit it to likely
                    # matching chats; global message search below still catches
                    # content-only matches without making the UI appear stuck.
                    if not matches_title:
                        continue
                    # Search inside likely matching joined chats too; this covers
                    # messages that global search may not return.
                    for term in query_terms:
                        try:
                            async for message in client.iter_messages(dialog.entity, search=term, limit=per_dialog_limit):
                                await _record_search_message(job, account, message, title_hint=title, username_hint=username, peer_kind_hint=kind, threshold=threshold, system_usernames=system_usernames, system_ids=system_ids)
                        except FloodWaitError as exc:
                            errors.append(f"الحساب {account.id}: FloodWait {exc.seconds}s أثناء البحث المحلي")
                            break
                        except Exception:
                            # Some dialogs may be inaccessible for history search; skip without stopping the entire job.
                            continue

            # 2) Telegram global peer search. Returns public groups/channels/bots, including non-members when Telegram returns them.
            if include_global:
                try:
                    for term in query_terms:
                        result = await client(functions.contacts.SearchRequest(q=term, limit=min(max_per_account, 100)))
                        for entity in list(getattr(result, "chats", [])) + list(getattr(result, "users", [])):
                            kind = entity_kind(entity)
                            if kind not in {"group", "channel", "bot"}:
                                continue
                            title = entity_title(entity)
                            username = getattr(entity, "username", None)
                            entity_id = int(getattr(entity, "id", 0) or 0)
                            if _is_system_source(username=username, entity_id=entity_id, usernames=system_usernames, ids=system_ids):
                                continue
                            sim = max(similarity_score(term, title, username), similarity_score(job.query_text, title, username))
                            score = saudi_score(title, username)
                            is_member = entity_id in member_ids or any(abs(x) == entity_id for x in member_ids)
                            _upsert_search_entity(job, entity, kind, title, username, is_member, sim, score)
                except Exception as exc:
                    errors.append(f"بحث الكيانات العامة في الحساب {account.id}: {type(exc).__name__}")

            # 3) Global message search. Telegram decides the accessible/public scope.
            if include_public_messages:
                try:
                    for term in query_terms:
                        async for message in client.iter_messages(None, search=term, limit=public_limit):
                            await _record_search_message(job, account, message, threshold=threshold, system_usernames=system_usernames, system_ids=system_ids)
                except FloodWaitError as exc:
                    errors.append(f"الحساب {account.id}: FloodWait {exc.seconds}s أثناء البحث العام")
                except Exception as exc:
                    errors.append(f"بحث الرسائل العامة في الحساب {account.id}: {type(exc).__name__}")

        except Exception as exc:
            errors.append(f"الحساب {account.id}: {type(exc).__name__}")
        finally:
            await client.disconnect()

    job.groups_count = SearchEntity.query.filter_by(search_job_id=job.id, entity_type="group").count()
    job.channels_count = SearchEntity.query.filter_by(search_job_id=job.id, entity_type="channel").count()
    job.bots_count = SearchEntity.query.filter_by(search_job_id=job.id, entity_type="bot").count()
    job.messages_count = SearchMessage.query.filter_by(search_job_id=job.id).count()
    job.links_count = DiscoveredLink.query.filter_by(search_job_id=job.id).count()
    job.status = "completed" if (job.groups_count + job.channels_count + job.messages_count + job.bots_count) else "completed_empty"
    job.error_text = " | ".join(errors)[:3000] if errors else None
    job.completed_at = utcnow()
    db.session.commit()
    return {"status": job.status}


async def _record_search_message(job, account, message, title_hint=None, username_hint=None, peer_kind_hint=None, threshold=25, system_usernames=None, system_ids=None):
    if not getattr(message, "id", None):
        return None
    text = getattr(message, "message", None) or ""
    try:
        chat = await message.get_chat()
    except Exception:
        chat = None
    title = title_hint or (entity_title(chat) if chat else "مصدر غير معروف")
    username = username_hint or (getattr(chat, "username", None) if chat else None)
    kind = peer_kind_hint or (entity_kind(chat) if chat else "message")
    if _is_system_source(username=username, entity_id=getattr(chat, "id", None) if chat else getattr(message, "chat_id", None), usernames=system_usernames, ids=system_ids):
        return None
    score = saudi_score(title, username, text)
    peer_id = int(getattr(message, "chat_id", 0) or 0)
    row = SearchMessage.query.filter_by(
        search_job_id=job.id, peer_id=peer_id, message_id=int(message.id)
    ).first()
    if not row:
        row = SearchMessage(
            search_job_id=job.id,
            account_id=account.id,
            peer_id=peer_id,
            peer_type=kind,
            peer_title=title,
            peer_username=username,
            message_id=int(message.id),
            message_text=text,
            message_date=getattr(message, "date", None),
            message_url=public_message_url(username, int(message.id)),
            is_accessible=True,
            saudi_score=score,
        )
        db.session.add(row)
        db.session.flush()
        for link in _extract_message_links(message):
            existing = DiscoveredLink.query.filter_by(
                owner_id=job.owner_id, search_job_id=job.id, url_hash=link.url_hash
            ).first()
            if not existing:
                db.session.add(DiscoveredLink(
                    owner_id=job.owner_id,
                    search_job_id=job.id,
                    search_message_id=row.id,
                    url=link.url,
                    url_hash=link.url_hash,
                    link_type=link.link_type,
                    source_title=title,
                    source_message_url=row.message_url,
                ))
        db.session.commit()
    return row


def _upsert_search_entity(job, entity, kind, title, username, is_member, sim, score):
    entity_id = int(getattr(entity, "id", 0) or 0)
    if not entity_id:
        return
    row = SearchEntity.query.filter_by(search_job_id=job.id, entity_type=kind, telegram_id=entity_id).first()
    if not row:
        row = SearchEntity(
            search_job_id=job.id,
            entity_type=kind,
            telegram_id=entity_id,
            title=title,
            username=username,
            is_member=bool(is_member),
            is_public=bool(username),
            similarity_score=sim,
            saudi_score=score,
            matched_text=job.query_text,
        )
        db.session.add(row)
    else:
        row.similarity_score = max(row.similarity_score, sim)
        row.saudi_score = max(row.saudi_score, score)
        row.is_member = row.is_member or bool(is_member)
    db.session.commit()


def validate_export_setting(setting_id):
    return asyncio.run(_validate_export_setting(setting_id))


async def _validate_export_setting(setting_id):
    setting = db.session.get(ExportSetting, setting_id)
    if not setting:
        return {"status": "missing"}
    account = db.session.get(TelegramAccount, setting.account_id)
    if not account or account.status != "active":
        setting.is_active = False
        db.session.commit()
        return {"status": "invalid_account"}
    client = _account_client(account)
    try:
        await client.connect()
        entity = await client.get_entity(setting.channel_ref)
        setting.channel_id = int(getattr(entity, "id", 0) or 0)
        setting.channel_username = getattr(entity, "username", None)
        setting.channel_title = entity_title(entity)
        setting.is_active = True
        setting.validated_at = utcnow()
        db.session.commit()
        return {"status": "active"}
    except Exception as exc:
        setting.is_active = False
        setting.channel_title = type(exc).__name__
        db.session.commit()
        return {"status": "failed", "error": type(exc).__name__}
    finally:
        await client.disconnect()


def export_search_job(job_id, export_type="all"):
    return asyncio.run(_export_search_job(job_id, export_type))


async def _export_search_job(job_id, export_type):
    job = db.session.get(SearchJob, job_id)
    if not job:
        return {"status": "missing"}
    setting = ExportSetting.query.filter_by(owner_id=job.owner_id, is_active=True).first()
    if not setting:
        return {"status": "no_setting"}
    account = db.session.get(TelegramAccount, setting.account_id)
    if not account or account.status != "active":
        return {"status": "invalid_account"}

    lines = [f"نتائج البحث: {job.query_text}", ""]
    if export_type in {"all", "groups", "channels", "bots"}:
        wanted = {"groups": "group", "channels": "channel", "bots": "bot"}.get(export_type)
        query = SearchEntity.query.filter_by(search_job_id=job.id)
        if wanted:
            query = query.filter_by(entity_type=wanted)
        for row in query.order_by(SearchEntity.similarity_score.desc()).limit(300):
            url = f"https://t.me/{row.username}" if row.username else ""
            lines.append(f"• {row.title}\n{url}".strip())
    if export_type in {"all", "messages"}:
        for row in SearchMessage.query.filter_by(search_job_id=job.id).order_by(SearchMessage.id.asc()).limit(300):
            if row.message_url:
                lines.append(f"• {row.peer_title or 'رسالة'}\n{row.message_url}")
    if export_type in {"all", "links", "telegram", "whatsapp"}:
        query = DiscoveredLink.query.filter_by(search_job_id=job.id)
        if export_type in {"telegram", "whatsapp"}:
            query = query.filter_by(link_type=export_type)
        for row in query.order_by(DiscoveredLink.id.asc()).limit(500):
            lines.append(row.url)

    # Telegram message limit: send safe chunks.
    chunks, current = [], ""
    for line in lines:
        addition = line + "\n\n"
        if len(current) + len(addition) > 3500:
            chunks.append(current.strip())
            current = addition
        else:
            current += addition
    if current.strip():
        chunks.append(current.strip())

    client = _account_client(account)
    try:
        await client.connect()
        entity = await client.get_entity(setting.channel_ref)
        for chunk in chunks:
            await client.send_message(entity, chunk, link_preview=False)
            await asyncio.sleep(1)
        return {"status": "completed", "chunks": len(chunks)}
    finally:
        await client.disconnect()


def _pdf_escape(value):
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _is_whatsapp_group_link(link):
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(link.url)
        host = (parsed.hostname or "").lower()
        # A valid invite has an opaque token.  Checking it avoids exporting generic
        # WhatsApp pages that happen to contain the domain name.
        token = parsed.path.strip("/").split("/", 1)[0]
        return host in {"chat.whatsapp.com", "www.chat.whatsapp.com"} and bool(re.fullmatch(r"[A-Za-z0-9_-]{10,64}", token))
    except Exception:
        return False


def _write_simple_links_pdf(path, title, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pages = []
    current = [title, ""]
    for line in lines:
        if len(current) >= 46:
            pages.append(current)
            current = []
        current.append(line)
    if current:
        pages.append(current)

    objects = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    for index, page_lines in enumerate(pages):
        page_id = 3 + index * 2
        content_id = page_id + 1
        stream_lines = ["BT", "/F1 10 Tf", "50 790 Td", "14 TL"]
        for line_index, line in enumerate(page_lines):
            text = _pdf_escape(line[:115])
            if line_index == 0:
                stream_lines.append(f"({text}) Tj")
            else:
                stream_lines.append(f"T* ({text}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> /Contents {content_id} 0 R >>")
        objects.append(f"<< /Length {len(stream.encode('latin-1', 'ignore'))} >>\nstream\n{stream}\nendstream")

    body = "%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(body.encode("latin-1")))
        body += f"{number} 0 obj\n{obj}\nendobj\n"
    xref = len(body.encode("latin-1"))
    body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        body += f"{offset:010d} 00000 n \n"
    body += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    with open(path, "wb") as handle:
        handle.write(body.encode("latin-1", "ignore"))


async def _add_export_ref_to_system_sets(client, channel_ref, usernames, ids):
    username = _username_from_ref(channel_ref)
    if username:
        usernames.add(username)
    if not channel_ref:
        return
    try:
        entity = await client.get_entity(channel_ref)
        entity_username = getattr(entity, "username", None)
        if entity_username:
            usernames.add(entity_username.lower())
        entity_id = getattr(entity, "id", None)
        if entity_id:
            ids.add(abs(int(entity_id)))
    except Exception:
        pass


async def _resolve_system_sources_with_client(client, owner_id, usernames, ids):
    refs = []
    setting = ExportSetting.query.filter_by(owner_id=owner_id).first()
    if setting and setting.channel_ref:
        refs.append(setting.channel_ref)
    refs.extend(source.source_channel_ref for source in JoinSource.query.filter_by(owner_id=owner_id).all() if source.source_channel_ref)
    for ref in refs:
        await _add_export_ref_to_system_sets(client, ref, usernames, ids)


def execute_whatsapp_scan_job(job_id, account_ids):
    return asyncio.run(_execute_whatsapp_scan_job(job_id, account_ids))


async def _execute_whatsapp_scan_job(job_id, account_ids):
    job = db.session.get(WhatsAppScanJob, job_id)
    if not job:
        return {"status": "missing"}

    job.status = "running"
    job.started_at = utcnow()
    job.error_text = None
    db.session.commit()

    accounts = TelegramAccount.query.filter(
        TelegramAccount.id.in_(list(account_ids or [])),
        TelegramAccount.owner_id == job.owner_id,
        TelegramAccount.status == "active",
    ).order_by(TelegramAccount.id.asc()).all()
    if not accounts:
        job.status = "failed"
        job.error_text = "no active accounts"
        job.completed_at = utcnow()
        db.session.commit()
        return {"status": "failed"}

    errors = []
    limit = max(50, min(get_int(job.owner_id, "WHATSAPP_SCAN_MESSAGE_LIMIT", current_app.config.get("WHATSAPP_SCAN_MESSAGE_LIMIT", 2000)), 10000))
    allowed_kinds = {"group", "channel"} if job.scope == "groups_channels" else {job.scope[:-1] if job.scope.endswith("s") else job.scope}

    for account in accounts:
        client = _account_client(account)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                errors.append(f"account {account.id}: unauthorized")
                continue

            system_usernames, system_ids = _system_source_identity_sets(job.owner_id)
            await _resolve_system_sources_with_client(client, job.owner_id, system_usernames, system_ids)
            await _add_export_ref_to_system_sets(client, job.export_channel_ref, system_usernames, system_ids)

            dialogs = await client.get_dialogs(limit=None)
            for dialog in dialogs:
                kind = entity_kind(dialog.entity)
                if kind not in allowed_kinds:
                    continue
                username = getattr(dialog.entity, "username", None)
                entity_id = getattr(dialog.entity, "id", None)
                if _is_system_source(username=username, entity_id=entity_id, usernames=system_usernames, ids=system_ids):
                    continue

                job.chats_scanned += 1
                title = dialog.name or entity_title(dialog.entity)
                try:
                    async for message in client.iter_messages(dialog.entity, limit=limit):
                        message_date = getattr(message, "date", None)
                        if job.start_date and message_date and message_date < job.start_date:
                            break
                        job.messages_scanned += 1
                        links = [item for item in _extract_message_links(message) if item.link_type == "whatsapp" and _is_whatsapp_group_link(item)]
                        if not links:
                            continue
                        job.links_found += len(links)
                        source_url = public_message_url(username, int(message.id))
                        for link in links:
                            existing = WhatsAppLink.query.filter_by(scan_job_id=job.id, url_hash=link.url_hash).first()
                            if existing:
                                continue
                            db.session.add(WhatsAppLink(
                                owner_id=job.owner_id,
                                scan_job_id=job.id,
                                account_id=account.id,
                                url=link.url,
                                url_hash=link.url_hash,
                                source_title=title,
                                source_username=username,
                                source_type=kind,
                                source_message_id=int(message.id),
                                source_message_url=source_url,
                                message_date=message_date,
                            ))
                            job.unique_links += 1
                        db.session.commit()
                except FloodWaitError as exc:
                    errors.append(f"account {account.id}: FloodWait {exc.seconds}s")
                    break
                except Exception as exc:
                    errors.append(f"chat {title}: {type(exc).__name__}")
                    continue
                db.session.commit()
        except Exception as exc:
            errors.append(f"account {account.id}: {type(exc).__name__}")
        finally:
            await client.disconnect()

    links = WhatsAppLink.query.filter_by(scan_job_id=job.id).order_by(WhatsAppLink.id.asc()).all()
    export_lines = []
    for index, row in enumerate(links, start=1):
        source = row.source_title or "unknown source"
        when = row.message_date.strftime("%Y-%m-%d %H:%M") if row.message_date else ""
        export_lines.append(f"{index}. {row.url}")
        export_lines.append(f"   source: {source} {when}".strip())
        if row.source_message_url:
            export_lines.append(f"   message: {row.source_message_url}")
        export_lines.append("")

    if job.export_mode in {"pdf", "both"}:
        filename = f"whatsapp-group-links-{job.id}.pdf"
        path = os.path.join(current_app.instance_path, "exports", filename)
        _write_simple_links_pdf(path, f"WhatsApp group links scan #{job.id}", export_lines or ["No WhatsApp group links found."])
        job.pdf_path = path
        db.session.commit()

    if job.export_mode in {"channel", "both"} and job.export_channel_ref and job.export_account_id:
        account = db.session.get(TelegramAccount, job.export_account_id)
        if account and account.status == "active":
            client = _account_client(account)
            try:
                await client.connect()
                entity = await client.get_entity(job.export_channel_ref)
                header = f"WhatsApp group links scan #{job.id}\nUnique group links: {len(links)}\n"
                chunks, current = [], header + "\n"
                for line in export_lines or ["No WhatsApp group links found."]:
                    addition = line + "\n"
                    if len(current) + len(addition) > 3500:
                        chunks.append(current.strip())
                        current = addition
                    else:
                        current += addition
                if current.strip():
                    chunks.append(current.strip())
                for chunk in chunks:
                    await client.send_message(entity, chunk, link_preview=False)
                    await asyncio.sleep(1)
            except Exception as exc:
                errors.append(f"export channel: {type(exc).__name__}")
            finally:
                await client.disconnect()

    job.status = "completed" if links else "completed_empty"
    job.error_text = " | ".join(errors)[:3000] if errors else None
    job.completed_at = utcnow()
    db.session.commit()
    return {"status": job.status, "links": len(links)}


def scan_join_source(scan_job_id):
    return asyncio.run(_scan_join_source(scan_job_id))


async def _scan_join_source(scan_job_id):
    scan = db.session.get(JoinScanJob, scan_job_id)
    if not scan:
        return {"status": "missing"}
    source = db.session.get(JoinSource, scan.source_id)
    account = db.session.get(TelegramAccount, scan.account_id)
    if not source or not account or account.status != "active":
        scan.status = "failed"
        scan.error_text = "المصدر أو الحساب غير صالح"
        db.session.commit()
        return {"status": "failed"}

    scan.status = "running"
    scan.started_at = utcnow()
    db.session.commit()
    client = _account_client(account)
    max_message_id = source.last_scanned_message_id or 0
    found = 0
    unique = 0
    try:
        await client.connect()
        entity = await client.get_entity(source.source_channel_ref)
        source.source_channel_id = int(getattr(entity, "id", 0) or 0)
        source.source_channel_title = entity_title(entity)
        limit = get_int(scan.owner_id, "JOIN_SCAN_MESSAGE_LIMIT", current_app.config["JOIN_SCAN_MESSAGE_LIMIT"])
        async for message in client.iter_messages(entity, limit=limit, min_id=source.last_scanned_message_id or 0, reverse=True):
            scan.messages_scanned += 1
            max_message_id = max(max_message_id, int(message.id))
            text = getattr(message, "message", None) or ""
            links = [item for item in _extract_message_links(message) if item.link_type == "telegram"]
            found += len(links)
            source_url = public_message_url(getattr(entity, "username", None), int(message.id))
            for link in links:
                row = DiscoveredJoinLink.query.filter_by(
                    owner_id=scan.owner_id, account_id=account.id, url_hash=link.url_hash
                ).first()
                if not row:
                    row = DiscoveredJoinLink(
                        owner_id=scan.owner_id,
                        scan_job_id=scan.id,
                        account_id=account.id,
                        source_message_id=int(message.id),
                        source_message_url=source_url,
                        url=link.url,
                        url_hash=link.url_hash,
                        status="discovered",
                    )
                    db.session.add(row)
                    db.session.flush()
                    unique += 1
                # Do not call Telegram once per discovered URL here.  This local
                # preparation also repairs old check_failed rows during a rescan.
                _prepare_join_link_for_execution(row)
                db.session.commit()
        source.last_scanned_message_id = max_message_id
        scan.links_found = found
        scan.unique_links = unique
        scan.status = "completed"
        scan.error_text = None
        scan.completed_at = utcnow()
        db.session.commit()
        return {"status": "completed", "unique": unique}
    except Exception as exc:
        scan.status = "failed"
        scan.error_text = f"{type(exc).__name__}: {str(exc)[:500]}"
        scan.completed_at = utcnow()
        db.session.commit()
        return {"status": "failed"}
    finally:
        await client.disconnect()


async def _inspect_join_link(client, row):
    target = parse_telegram_link(row.url)
    row.checked_at = utcnow()
    if target.kind == "unsupported":
        row.status = "unsupported"
        return
    try:
        if target.kind == "invite":
            row.invite_hash = target.value
            result = await client(functions.messages.CheckChatInviteRequest(hash=target.value))
            chat = getattr(result, "chat", None)
            row.entity_title = getattr(chat, "title", None) or getattr(result, "title", None) or "دعوة تيليجرام"
            row.entity_id = int(getattr(chat, "id", 0) or 0) or None
            row.entity_type = "channel" if getattr(chat, "broadcast", False) else "group"
            if result.__class__.__name__ == "ChatInviteAlready":
                row.status = "already_member"
                row.is_already_member = True
            else:
                row.requires_approval = bool(getattr(result, "request_needed", False))
                row.status = "valid_invite"
        else:
            row.username = target.value
            entity = await client.get_entity(target.value)
            row.entity_title = entity_title(entity)
            row.entity_id = int(getattr(entity, "id", 0) or 0) or None
            row.entity_type = entity_kind(entity)
            if row.entity_type not in {"group", "channel"}:
                row.status = "unsupported"
            else:
                row.status = "valid_public"
    except UserAlreadyParticipantError:
        row.status = "already_member"
        row.is_already_member = True
    except InviteHashExpiredError:
        row.status = "expired"
    except InviteHashInvalidError:
        row.status = "invalid"
    except ChannelPrivateError:
        row.status = "private_inaccessible"
    except FloodWaitError:
        # This is a temporary Telegram throttle, never a bad link.  The caller
        # pauses and retries it without exposing a false "check failed" result.
        row.status = "discovered"
        row.error_text = None
        raise
    except Exception as exc:
        row.status = "check_failed"
        row.error_text = type(exc).__name__



JOINABLE_POOL_STATUSES = ["valid_public", "valid_invite", "already_member", "joined", "join_request_pending"]


def _dedupe_rows_by_hash(rows):
    seen = set()
    result = []
    for row in rows:
        if row.url_hash in seen:
            continue
        seen.add(row.url_hash)
        result.append(row)
    return result


def _query_join_links_for_mode(owner_id, account_id, mode):
    """Return account-specific joinable links for compatibility paths."""
    query = DiscoveredJoinLink.query.filter(
        DiscoveredJoinLink.owner_id == owner_id,
        DiscoveredJoinLink.account_id == account_id,
        DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES),
    )

    if mode == "groups":
        query = query.filter(DiscoveredJoinLink.entity_type == "group")
    elif mode == "channels":
        query = query.filter(DiscoveredJoinLink.entity_type == "channel")
    elif mode == "invites":
        query = query.filter(DiscoveredJoinLink.invite_hash.isnot(None))
    elif mode == "approval_required":
        query = query.filter(DiscoveredJoinLink.requires_approval.is_(True))
    elif mode in {"all_valid", "selected"}:
        pass
    else:
        query = query.filter(DiscoveredJoinLink.id == -1)

    return query


def _global_join_pool_for_mode(owner_id, mode, exclude_hashes=None, limit=10):
    """Return a deduplicated global pool for the next batch.

    Auto-continuation must work even when links were discovered by one account but
    are being executed by several accounts. The worker clones the next URL batch
    into each target account before executing it.
    """
    exclude_hashes = exclude_hashes or set()
    query = DiscoveredJoinLink.query.filter(
        DiscoveredJoinLink.owner_id == owner_id,
        DiscoveredJoinLink.status.in_(JOINABLE_POOL_STATUSES),
    )
    if mode == "groups":
        query = query.filter(DiscoveredJoinLink.entity_type == "group")
    elif mode == "channels":
        query = query.filter(DiscoveredJoinLink.entity_type == "channel")
    elif mode == "invites":
        query = query.filter(DiscoveredJoinLink.invite_hash.isnot(None))
    elif mode == "approval_required":
        query = query.filter(DiscoveredJoinLink.requires_approval.is_(True))
    elif mode in {"all_valid", "selected"}:
        pass
    else:
        return []

    rows = query.order_by(DiscoveredJoinLink.id.desc()).limit(max(50, limit * 10)).all()
    rows = [row for row in _dedupe_rows_by_hash(rows) if row.url_hash not in exclude_hashes]
    return rows[:limit]


def _ensure_worker_link_for_account(owner_id, account_id, source):
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
        status="discovered",
        requires_approval=source.requires_approval,
        is_already_member=False,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _used_join_hashes_for_account(owner_id, account_id):
    used_ids = [
        value for (value,) in db.session.query(JoinJobItem.discovered_link_id)
        .join(JoinJob, JoinJobItem.join_job_id == JoinJob.id)
        .filter(JoinJob.owner_id == owner_id, JoinJob.account_id == account_id)
        .all()
    ]
    if not used_ids:
        return set()
    return {
        value for (value,) in db.session.query(DiscoveredJoinLink.url_hash)
        .filter(DiscoveredJoinLink.id.in_(used_ids))
        .all()
    }


def _create_next_join_batch(previous_job):
    """Create the next JoinJob batch for the same owner/account/mode, if enabled."""
    if not previous_job.auto_continue:
        return None
    if previous_job.batch_index >= max(1, previous_job.max_batches):
        return None

    max_items = max(1, get_int(previous_job.owner_id, "JOIN_MAX_ITEMS_PER_JOB", current_app.config["JOIN_MAX_ITEMS_PER_JOB"]))
    mode = previous_job.selection_mode or "all_valid"
    used_hashes = _used_join_hashes_for_account(previous_job.owner_id, previous_job.account_id)
    source_rows = _global_join_pool_for_mode(previous_job.owner_id, mode, exclude_hashes=used_hashes, limit=max_items)
    links = [_ensure_worker_link_for_account(previous_job.owner_id, previous_job.account_id, source) for source in source_rows]

    if not links:
        return None

    next_job = JoinJob(
        owner_id=previous_job.owner_id,
        account_id=previous_job.account_id,
        total_links=len(links),
        selection_mode=mode,
        auto_continue=previous_job.auto_continue,
        batch_pause_seconds=previous_job.batch_pause_seconds,
        max_batches=previous_job.max_batches,
        batch_index=previous_job.batch_index + 1,
        parent_job_id=previous_job.parent_job_id or previous_job.id,
        status="queued",
    )
    db.session.add(next_job)
    db.session.flush()
    db.session.add_all([
        JoinJobItem(join_job_id=next_job.id, discovered_link_id=row.id)
        for row in links
    ])
    db.session.commit()
    return next_job

def execute_join_job(join_job_id):
    job = db.session.get(JoinJob, join_job_id)
    if not job:
        return {"status": "missing"}
    lock = get_account_lock(job.account_id)
    if not lock.acquire(blocking=False):
        job.status = "queued"
        job.stopped_reason = "الحساب مستخدم في مهمة أخرى"
        db.session.commit()
        return {"status": "queued"}
    try:
        return asyncio.run(_execute_join_job(join_job_id))
    finally:
        lock.release()


async def _execute_join_job(join_job_id):
    job = db.session.get(JoinJob, join_job_id)
    account = db.session.get(TelegramAccount, job.account_id)
    if not account or account.status != "active":
        job.status = "stopped"
        job.stopped_reason = "الحساب غير نشط"
        db.session.commit()
        return {"status": "stopped"}

    items = JoinJobItem.query.filter_by(join_job_id=job.id).order_by(JoinJobItem.id.asc()).all()
    job.status = "running"
    job.started_at = utcnow()
    db.session.commit()

    client = _account_client(account)
    try:
        await client.connect()
        resume_after_floodwait = get_bool(
            job.owner_id,
            "JOIN_RESUME_AFTER_FLOODWAIT",
            current_app.config.get("JOIN_RESUME_AFTER_FLOODWAIT", True),
        )
        max_floodwait_sleep = get_int(
            job.owner_id,
            "JOIN_MAX_FLOODWAIT_SLEEP_SECONDS",
            current_app.config.get("JOIN_MAX_FLOODWAIT_SLEEP_SECONDS", 3600),
        )

        for index, item in enumerate(items):
            db.session.refresh(job)
            if job.status in {"cancelled", "cancel_requested"}:
                return {"status": job.status}
            if job.status in {"paused", "stopped"}:
                return {"status": job.status}
            row = db.session.get(DiscoveredJoinLink, item.discovered_link_id)
            if not row:
                continue

            while True:
                item.attempted_at = utcnow()
                try:
                    # Re-check account-specific state before joining. This is critical
                    # for multi-account execution: one account may already be a member
                    # while another account still needs to join or send a request.
                    if row.status in {"discovered", "check_failed", "valid_public", "valid_invite", "joined", "join_request_pending"}:
                        await _inspect_join_link(client, row)
                        db.session.commit()

                    if row.status == "already_member":
                        item.status = "already_member"
                        row.is_already_member = True
                        job.already_member_count += 1
                    elif row.status == "join_request_pending":
                        item.status = "join_request_pending"
                        job.request_pending_count += 1
                    elif row.invite_hash:
                        await client(functions.messages.ImportChatInviteRequest(hash=row.invite_hash))
                        item.status = "joined"
                        row.status = "joined"
                        job.joined_count += 1
                    elif row.username:
                        entity = await client.get_entity(row.username)
                        await client(functions.channels.JoinChannelRequest(channel=entity))
                        item.status = "joined"
                        row.status = "joined"
                        job.joined_count += 1
                    else:
                        raise ValueError("الرابط غير قابل للانضمام")

                    item.completed_at = utcnow()
                    db.session.commit()
                    break

                except InviteRequestSentError:
                    item.status = "join_request_pending"
                    row.status = "join_request_pending"
                    row.requires_approval = True
                    job.request_pending_count += 1
                    item.completed_at = utcnow()
                    db.session.commit()
                    break
                except UserAlreadyParticipantError:
                    item.status = "already_member"
                    row.status = "already_member"
                    row.is_already_member = True
                    job.already_member_count += 1
                    item.completed_at = utcnow()
                    db.session.commit()
                    break
                except FloodWaitError as exc:
                    item.status = "rate_limited"
                    item.error_code = type(exc).__name__
                    item.error_text = f"انتظار {exc.seconds} ثانية"
                    item.next_attempt_at = utcnow() + timedelta(seconds=max(1, int(exc.seconds)))
                    job.status = "paused_rate_limit"
                    job.stopped_reason = item.error_text
                    db.session.commit()

                    if resume_after_floodwait and int(exc.seconds) <= max_floodwait_sleep:
                        wait_until = utcnow() + timedelta(seconds=max(1, int(exc.seconds)))
                        job.rate_limited_until = wait_until
                        db.session.commit()
                        monitor_step = max(10, get_int(job.owner_id, "JOIN_DYNAMIC_MONITOR_SECONDS", current_app.config.get("JOIN_DYNAMIC_MONITOR_SECONDS", 60)))
                        remaining = max(1, int(exc.seconds))
                        while remaining > 0:
                            await asyncio.sleep(min(monitor_step, remaining))
                            remaining -= monitor_step
                            db.session.refresh(job)
                            if job.status in {"cancelled", "cancel_requested", "stopped"}:
                                return {"status": job.status}
                        job.status = "running"
                        job.rate_limited_until = None
                        job.stopped_reason = None
                        item.status = "approved"
                        item.next_attempt_at = None
                        item.error_text = None
                        item.error_code = None
                        db.session.commit()
                        continue

                    return {"status": job.status, "wait_seconds": exc.seconds}
                except Exception as exc:
                    item.status = "failed"
                    item.error_code = type(exc).__name__
                    item.error_text = str(exc)[:1000]
                    row.status = "failed"
                    row.error_text = type(exc).__name__
                    job.failed_count += 1
                    item.completed_at = utcnow()
                    db.session.commit()
                    break

            if index < len(items) - 1:
                # Preserve Telegram-safe pacing, while tolerating inverted values
                # saved in Settings instead of crashing a whole job.
                low = max(0, get_int(job.owner_id, "JOIN_DELAY_MIN_SECONDS", current_app.config["JOIN_DELAY_MIN_SECONDS"]))
                high = max(low, get_int(job.owner_id, "JOIN_DELAY_MAX_SECONDS", current_app.config["JOIN_DELAY_MAX_SECONDS"]))
                await asyncio.sleep(random.uniform(low, high))

        job.status = "completed"
        job.completed_at = utcnow()
        db.session.commit()

        next_job = _create_next_join_batch(job)
        if next_job:
            pause_seconds = max(0, int(job.batch_pause_seconds or 0))
            if pause_seconds:
                await asyncio.sleep(pause_seconds)
            return await _execute_join_job(next_job.id)

        return {"status": "completed"}
    finally:
        await client.disconnect()


def inspect_join_links(account_id, link_ids):
    return asyncio.run(_inspect_join_links(account_id, link_ids))


async def _inspect_join_links(account_id, link_ids):
    account = db.session.get(TelegramAccount, account_id)
    if not account or account.status != "active":
        return {"status": "invalid_account"}
    rows = DiscoveredJoinLink.query.filter(
        DiscoveredJoinLink.account_id == account.id,
        DiscoveredJoinLink.id.in_(list(link_ids)),
    ).all()
    client = _account_client(account)
    try:
        await client.connect()
        for index, row in enumerate(rows):
            while True:
                try:
                    await _inspect_join_link(client, row)
                    db.session.commit()
                    break
                except FloodWaitError as exc:
                    row.status = "discovered"
                    row.error_text = f"Telegram rate limit: retrying in {exc.seconds}s"
                    db.session.commit()
                    await asyncio.sleep(max(1, int(exc.seconds)))
            if index < len(rows) - 1:
                low = max(0.0, float(get_int(scan.owner_id, "JOIN_INSPECT_DELAY_MIN_SECONDS", int(current_app.config.get("JOIN_INSPECT_DELAY_MIN_SECONDS", 2)))))
                high = max(low, float(get_int(scan.owner_id, "JOIN_INSPECT_DELAY_MAX_SECONDS", int(current_app.config.get("JOIN_INSPECT_DELAY_MAX_SECONDS", 4)))))
                await asyncio.sleep(random.uniform(low, high))
        return {"status": "completed", "count": len(rows)}
    finally:
        await client.disconnect()



def _channel_username_from_ref(channel_ref):
    import re
    value = (channel_ref or "").strip().rstrip("/")
    match = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)$", value)
    if match:
        return match.group(1)
    if value.startswith("@"):
        return value[1:]
    if re.fullmatch(r"[A-Za-z0-9_]{5,}", value):
        return value
    return None


def _channel_post_url(channel_ref, message_id):
    username = _channel_username_from_ref(channel_ref)
    if username and message_id:
        return f"https://t.me/{username}/{message_id}"
    return None


def publish_channel_post(post_id, account_id, channel_ref):
    return asyncio.run(_publish_channel_post(post_id, account_id, channel_ref))


async def _publish_channel_post(post_id, account_id, channel_ref):
    post = db.session.get(ChannelPost, post_id)
    account = db.session.get(TelegramAccount, account_id)
    if not post or not account or account.status != "active":
        return {"status": "missing_or_inactive"}

    post.status = "publishing"
    post.last_error = None
    db.session.commit()

    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    client = build_client(current_app.config["TELEGRAM_API_ID"], current_app.config["TELEGRAM_API_HASH"], session, proxy)
    try:
        await client.connect()
        entity = await client.get_entity(channel_ref)
        result = await client.send_message(entity, post.body_html or " ", parse_mode="html", link_preview=True)
        message_id = _extract_sent_message_id(result)
        post.telegram_message_id = message_id
        post.telegram_post_url = _channel_post_url(channel_ref, message_id)
        if getattr(post, "auto_pin", False) and message_id:
            try:
                await client.pin_message(entity, message_id, notify=False)
                post.pinned_at = utcnow()
            except Exception as pin_exc:
                post.last_error = f"تم النشر لكن فشل التثبيت: {pin_exc}"
        post.status = "published"
        post.published_at = utcnow()
        post.last_error = None
        db.session.commit()
        return {"status": "published", "message_id": message_id, "url": post.telegram_post_url}
    except Exception as exc:
        post.status = "failed"
        post.last_error = str(exc)
        db.session.commit()
        return {"status": "failed", "error": str(exc)}
    finally:
        await client.disconnect()


def send_channel_post_test(post_id, account_id, channel_ref):
    return asyncio.run(_send_channel_post_test(post_id, account_id, channel_ref))


async def _send_channel_post_test(post_id, account_id, channel_ref):
    post = db.session.get(ChannelPost, post_id)
    account = db.session.get(TelegramAccount, account_id)
    if not post or not account or account.status != "active":
        return {"status": "missing_or_inactive"}
    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    client = build_client(current_app.config["TELEGRAM_API_ID"], current_app.config["TELEGRAM_API_HASH"], session, proxy)
    try:
        await client.connect()
        entity = await client.get_entity(channel_ref)
        result = await client.send_message(entity, post.body_html or " ", parse_mode="html", link_preview=True)
        return {"status": "sent", "message_id": _extract_sent_message_id(result)}
    finally:
        await client.disconnect()


def _build_channel_index_body(owner_id, registration_url=""):
    posts = (
        ChannelPost.query
        .filter(ChannelPost.owner_id == owner_id, ChannelPost.status == "published", ChannelPost.telegram_post_url.isnot(None), ChannelPost.post_type.in_(["research_opportunity", "opportunity_short", "service", "educational", "reminder", "faq", "welcome", "custom"]))
        .order_by(ChannelPost.post_type.asc(), ChannelPost.published_at.desc())
        .all()
    )
    if not posts:
        items = "لا توجد منشورات منشورة بعد."
    else:
        groups = {}
        labels = {
            "research_opportunity": "🔬 الفرص البحثية",
            "opportunity_short": "✅ فرص مختصرة",
            "service": "🧩 الخدمات",
            "educational": "💡 منشورات تعليمية",
            "reminder": "⏳ تذكيرات",
            "faq": "❓ أسئلة شائعة",
            "welcome": "👋 منشورات تعريفية",
            "custom": "📌 منشورات أخرى",
        }
        for post in posts:
            groups.setdefault(post.post_type or "custom", []).append(post)
        blocks = []
        for post_type, post_list in groups.items():
            lines = [f"<b>{labels.get(post_type, post_type)}</b>"]
            for post in post_list[:25]:
                url = post.telegram_post_url
                title = (post.title or "منشور").replace("<", "").replace(">", "")
                lines.append(f"- <a href=\"{url}\">{title}</a>")
            blocks.append("\n".join(lines))
        items = "\n\n".join(blocks)
    tail = f"\n\nللتسجيل العام:\n{registration_url}" if registration_url else ""
    return f"📌 <b>فهرس القناة</b>\n\n{items}{tail}\n\nآخر تحديث: اليوم"


def update_channel_index_post(settings_id):
    return asyncio.run(_update_channel_index_post(settings_id))


async def _update_channel_index_post(settings_id):
    settings = db.session.get(ChannelSettings, settings_id)
    if not settings or not settings.index_post_id:
        return {"status": "missing_settings_or_index"}
    index_post = db.session.get(ChannelPost, settings.index_post_id)
    account = db.session.get(TelegramAccount, settings.publisher_account_id) if settings.publisher_account_id else None
    if not index_post or not account or account.status != "active" or not settings.channel_ref:
        return {"status": "missing_or_inactive"}

    body = _build_channel_index_body(settings.owner_id, settings.registration_url or "")
    index_post.body_html = body
    index_post.last_error = None
    db.session.commit()

    session = _crypto().decrypt(account.encrypted_session)
    proxy = _crypto().decrypt(account.encrypted_proxy) if account.encrypted_proxy else None
    client = build_client(current_app.config["TELEGRAM_API_ID"], current_app.config["TELEGRAM_API_HASH"], session, proxy)
    try:
        await client.connect()
        entity = await client.get_entity(settings.channel_ref)
        if index_post.telegram_message_id:
            await client.edit_message(entity, index_post.telegram_message_id, body, parse_mode="html", link_preview=True)
        else:
            result = await client.send_message(entity, body, parse_mode="html", link_preview=True)
            index_post.telegram_message_id = _extract_sent_message_id(result)
            index_post.telegram_post_url = _channel_post_url(settings.channel_ref, index_post.telegram_message_id)
        index_post.status = "published"
        index_post.published_at = index_post.published_at or utcnow()
        index_post.last_error = None
        db.session.commit()
        return {"status": "updated", "message_id": index_post.telegram_message_id, "url": index_post.telegram_post_url}
    except Exception as exc:
        index_post.last_error = str(exc)
        db.session.commit()
        return {"status": "failed", "error": str(exc)}
    finally:
        await client.disconnect()
