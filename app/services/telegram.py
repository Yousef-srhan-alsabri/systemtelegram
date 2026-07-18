from urllib.parse import urlparse

import socks
from telethon import TelegramClient
from telethon.sessions import StringSession


def parse_proxy(proxy_url):
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}
    if parsed.scheme not in types or not parsed.hostname or not parsed.port:
        raise ValueError("صيغة البروكسي غير صحيحة")
    return (types[parsed.scheme], parsed.hostname, parsed.port, True, parsed.username, parsed.password)


def build_client(api_id, api_hash, session_string="", proxy_url=None):
    return TelegramClient(
        StringSession(session_string), api_id, api_hash,
        proxy=parse_proxy(proxy_url), request_retries=3,
        connection_retries=3, retry_delay=2,
        auto_reconnect=True, flood_sleep_threshold=0,
        receive_updates=True,
    )
