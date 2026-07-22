import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

# Keep this deliberately host-oriented.  Invite links are frequently pasted without
# a scheme, wrapped in punctuation, or attached to text inside a Telegram button.
URL_PATTERN = re.compile(
    r'(?i)(?<![\w@])((?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|chat\.whatsapp\.com|whatsapp\.com|wa\.me)(?:/[^\s<>"\'\]\[{}()،,;]*)?)'
)
TG_INVITE_PATTERN = re.compile(r"(?i)tg://join\?[^\s<>\"'\]\[{}()]+")
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
    if host in {"chat.whatsapp.com", "www.chat.whatsapp.com", "whatsapp.com", "www.whatsapp.com", "wa.me"}:
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
    # Telegram mobile clients commonly expose invitation links in this form.
    # Convert it to the canonical web invite URL so the join worker can use it.
    for match in TG_INVITE_PATTERN.finditer(text or ""):
        parsed = urlsplit(match.group(0).rstrip(TRAILING))
        from urllib.parse import parse_qs
        invite = parse_qs(parsed.query).get("invite", [""])[0]
        if not re.fullmatch(r"[A-Za-z0-9_-]{5,128}", invite):
            continue
        url = normalize_url("https://t.me/+" + invite)
        digest = hashlib.sha256(url.encode()).hexdigest()
        unique.setdefault(digest, LinkItem(url, digest, "telegram"))
    for match in MENTION_PATTERN.finditer(text or ""):
        try:
            url = normalize_url("https://t.me/" + match.group(1))
        except ValueError:
            continue
        digest = hashlib.sha256(url.encode()).hexdigest()
        unique.setdefault(digest, LinkItem(url, digest, "telegram"))
    return list(unique.values())
