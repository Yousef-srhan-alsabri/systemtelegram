import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

URL_PATTERN = re.compile(r'(?i)\b((?:https?://|www\.|t\.me/|telegram\.me/|chat\.whatsapp\.com/|wa\.me/)[^\s<>"\'\]\[{}()]+)')
MENTION_PATTERN = re.compile(r'(?<![A-Za-z0-9_])@([A-Za-z0-9_]{5,32})')
TRAILING = ".,;:!?،؛؟)]}>"


@dataclass(frozen=True)
class LinkItem:
    url: str
    url_hash: str
    link_type: str


def normalize_url(raw):
    value = raw.strip().rstrip(TRAILING)
    if not re.match(r"(?i)^https?://", value):
        value = "https://" + value
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise ValueError("invalid url")
    netloc = host
    if parsed.port and not ((parsed.scheme == "http" and parsed.port == 80) or (parsed.scheme == "https" and parsed.port == 443)):
        netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/") or "/", parsed.query, ""))


def classify(url):
    host = (urlsplit(url).hostname or "").lower()
    if host in {"t.me", "telegram.me", "www.telegram.me"}:
        return "telegram"
    if host in {"chat.whatsapp.com", "wa.me", "www.wa.me"}:
        return "whatsapp"
    return "other"


def extract_links(text):
    unique = {}
    for match in URL_PATTERN.finditer(text or ""):
        try:
            url = normalize_url(match.group(1))
        except ValueError:
            continue
        digest = hashlib.sha256(url.encode()).hexdigest()
        unique[digest] = LinkItem(url, digest, classify(url))
    for match in MENTION_PATTERN.finditer(text or ""):
        try:
            url = normalize_url("https://t.me/" + match.group(1))
        except ValueError:
            continue
        digest = hashlib.sha256(url.encode()).hexdigest()
        unique.setdefault(digest, LinkItem(url, digest, "telegram"))
    return list(unique.values())
