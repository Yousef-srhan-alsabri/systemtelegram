import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=True)


def _normalize_database_url(raw: str) -> str:
    """Return a SQLAlchemy URL that works on Railway/PostgreSQL and locally.

    Railway commonly exposes DATABASE_URL as postgresql://... . This project
    uses psycopg v3, so SQLAlchemy must be directed to the psycopg driver.
    SQLite remains supported for local development only.
    """
    url = (raw or "").strip() or "sqlite:///app.db"
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///app.db"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 300}
    SESSION_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
    REMEMBER_COOKIE_SECURE = os.getenv("REMEMBER_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}

    TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
    TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
    SESSION_ENCRYPTION_KEYS = [
        key.strip()
        for key in os.getenv("SESSION_ENCRYPTION_KEYS", "").split(",")
        if key.strip()
    ]

    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com").strip().lower()
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "ChangeMe123!")
    MAX_TELEGRAM_ACCOUNTS = int(os.getenv("MAX_TELEGRAM_ACCOUNTS", "50"))
    MESSAGE_DELAY_MIN_SECONDS = int(os.getenv("MESSAGE_DELAY_MIN_SECONDS", "20"))
    MESSAGE_DELAY_MAX_SECONDS = int(os.getenv("MESSAGE_DELAY_MAX_SECONDS", "30"))
    CAMPAIGN_RISK_SENT_LIMIT = int(os.getenv("CAMPAIGN_RISK_SENT_LIMIT", "120"))
    CAMPAIGN_RISK_FAILURE_LIMIT = int(os.getenv("CAMPAIGN_RISK_FAILURE_LIMIT", "8"))
    DEFAULT_BATCH_SIZE = int(os.getenv("DEFAULT_BATCH_SIZE", "30"))
    MAX_TARGETS_PER_ACCOUNT_TASK = int(os.getenv("MAX_TARGETS_PER_ACCOUNT_TASK", "500"))
    QR_LOGIN_TIMEOUT_SECONDS = int(os.getenv("QR_LOGIN_TIMEOUT_SECONDS", "300"))

    SEARCH_DEFAULT_MAX_RESULTS = int(os.getenv("SEARCH_DEFAULT_MAX_RESULTS", "250"))
    SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "1000"))
    SEARCH_SAUDI_THRESHOLD = int(os.getenv("SEARCH_SAUDI_THRESHOLD", "25"))
    SEARCH_SCOPE_DEFAULT = os.getenv("SEARCH_SCOPE_DEFAULT", "global_plus_joined").strip()
    SEARCH_INCLUDE_PUBLIC_MESSAGES = os.getenv("SEARCH_INCLUDE_PUBLIC_MESSAGES", "true").lower() in {"1", "true", "yes", "on"}
    SEARCH_GLOBAL_LIMIT = int(os.getenv("SEARCH_GLOBAL_LIMIT", "100"))
    JOIN_MAX_ITEMS_PER_JOB = int(os.getenv("JOIN_MAX_ITEMS_PER_JOB", "10"))
    JOIN_DELAY_MIN_SECONDS = int(os.getenv("JOIN_DELAY_MIN_SECONDS", "15"))
    JOIN_DELAY_MAX_SECONDS = int(os.getenv("JOIN_DELAY_MAX_SECONDS", "30"))
    JOIN_SCAN_MESSAGE_LIMIT = int(os.getenv("JOIN_SCAN_MESSAGE_LIMIT", "1000"))
    JOIN_CONTINUE_BATCHES = os.getenv("JOIN_CONTINUE_BATCHES", "false").lower() in {"1", "true", "yes", "on"}
    JOIN_BATCH_PAUSE_SECONDS = int(os.getenv("JOIN_BATCH_PAUSE_SECONDS", "300"))
    JOIN_MAX_BATCHES_PER_RUN = int(os.getenv("JOIN_MAX_BATCHES_PER_RUN", "5"))
    JOIN_RESUME_AFTER_FLOODWAIT = os.getenv("JOIN_RESUME_AFTER_FLOODWAIT", "true").lower() in {"1", "true", "yes", "on"}
    JOIN_MAX_FLOODWAIT_SLEEP_SECONDS = int(os.getenv("JOIN_MAX_FLOODWAIT_SLEEP_SECONDS", "3600"))
    JOIN_DYNAMIC_MONITOR_SECONDS = int(os.getenv("JOIN_DYNAMIC_MONITOR_SECONDS", "60"))
