from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from rapidfuzz import fuzz

from app.services.links import extract_links

ARABIC_DIACRITICS = re.compile(r"[\u0617-\u061A\u064B-\u0652\u0670\u06D6-\u06ED]")
TATWEEL = "\u0640"
TELEGRAM_HOSTS = {"t.me", "telegram.me", "www.telegram.me"}

SAUDI_TERMS = {
    "السعودية": 30, "المملكة": 15, "الرياض": 20, "جدة": 20, "مكة": 18,
    "المدينة": 12, "الدمام": 18, "الخبر": 15, "القصيم": 15, "أبها": 15,
    "تبوك": 15, "جامعة الملك سعود": 35, "ksu": 35, "king saud university": 35,
    "جامعة الملك عبدالعزيز": 30, "kau": 25, "وزارة التعليم": 25,
    "هيئة التخصصات": 20, "قياس": 15, "جدارات": 15, "+966": 25,
    "00966": 25, ".edu.sa": 25, ".gov.sa": 25, ".com.sa": 15,
    "شعب": 8, "تحضيري": 8, "مكافأة": 8, "قبول": 5, "تسجيل": 5,
}

ALIASES = {
    "جامعة الملك سعود": ["جامعة الملك سعود", "الملك سعود", "ksu", "king saud university"],
    "جامعة الملك عبدالعزيز": ["جامعة الملك عبدالعزيز", "الملك عبدالعزيز", "kau", "king abdulaziz university"],
}


def normalize_text(value: str | None) -> str:
    text = (value or "").strip().lower().replace(TATWEEL, "")
    text = ARABIC_DIACRITICS.sub("", text)
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي")):
        text = text.replace(source, target)
    return " ".join(text.split())


def expand_query(query: str) -> list[str]:
    normalized = normalize_text(query)
    values = [query, normalized]
    for key, aliases in ALIASES.items():
        if normalize_text(key) in normalized or normalized in normalize_text(key):
            values.extend(aliases)
    # preserve order, remove blanks and duplicates
    result = []
    seen = set()
    for value in values:
        item = normalize_text(value)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def similarity_score(query: str, *fields: str | None) -> int:
    choices = expand_query(query)
    candidates = [normalize_text(field) for field in fields if field]
    if not candidates:
        return 0
    best = 0
    for choice in choices:
        for candidate in candidates:
            best = max(best, int(fuzz.WRatio(choice, candidate)))
            if choice and choice in candidate:
                best = max(best, 92)
    return min(best, 100)


def saudi_score(*fields: str | None) -> int:
    text = normalize_text(" ".join(field or "" for field in fields))
    raw_text = " ".join(field or "" for field in fields).lower()
    score = 0
    for term, weight in SAUDI_TERMS.items():
        needle = normalize_text(term)
        if needle in text or term.lower() in raw_text:
            score += weight
    if re.search(r"(?:\+966|00966|\b05\d{8}\b)", raw_text):
        score += 25
    return min(score, 100)


def entity_kind(entity) -> str | None:
    if getattr(entity, "bot", False):
        return "bot"
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False) or entity.__class__.__name__.lower().endswith("chat"):
        return "group"
    return None


def entity_title(entity) -> str:
    return (
        getattr(entity, "title", None)
        or " ".join(x for x in [getattr(entity, "first_name", None), getattr(entity, "last_name", None)] if x)
        or getattr(entity, "username", None)
        or "بدون اسم"
    )


def public_message_url(username: str | None, message_id: int | None) -> str | None:
    if username and message_id:
        return f"https://t.me/{username}/{message_id}"
    return None


@dataclass(frozen=True)
class TelegramLinkTarget:
    kind: str  # username / invite / unsupported
    value: str | None


def parse_telegram_link(url: str) -> TelegramLinkTarget:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in TELEGRAM_HOSTS:
        return TelegramLinkTarget("unsupported", None)
    # Telegram also emits tg://join?invite=... from mobile share sheets.
    if parsed.scheme.lower() == "tg" and parsed.netloc.lower() == "join":
        from urllib.parse import parse_qs
        invite = parse_qs(parsed.query).get("invite", [None])[0]
        return TelegramLinkTarget("invite", invite) if invite else TelegramLinkTarget("unsupported", None)
    path = parsed.path.strip("/")
    if not path:
        return TelegramLinkTarget("unsupported", None)
    if path.startswith("+") and len(path) > 1:
        return TelegramLinkTarget("invite", path[1:])
    if path.lower().startswith("joinchat/"):
        return TelegramLinkTarget("invite", path.split("/", 1)[1])
    first = path.split("/", 1)[0]
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", first):
        return TelegramLinkTarget("username", first)
    return TelegramLinkTarget("unsupported", None)


def telegram_links_from_text(text: str) -> list:
    return [item for item in extract_links(text) if item.link_type == "telegram"]


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
