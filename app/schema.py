from sqlalchemy import inspect, text

from .extensions import db


def _dialect() -> str:
    return db.engine.dialect.name


def _q(identifier: str) -> str:
    """Quote SQL identifiers safely for SQLite/PostgreSQL legacy ALTERs."""
    return '"' + identifier.replace('"', '""') + '"'


def _bool_default(value: bool) -> str:
    if _dialect() == "postgresql":
        return "TRUE" if value else "FALSE"
    return "1" if value else "0"


def _dt_type() -> str:
    return "TIMESTAMP WITH TIME ZONE" if _dialect() == "postgresql" else "DATETIME"


def _add_column(table: str, name: str, definition: str, columns: set[str]):
    if name not in columns:
        db.session.execute(text(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(name)} {definition}"))
        db.session.commit()
        columns.add(name)


def ensure_legacy_columns():
    """Add missing columns for users upgrading old SQLite builds or Railway/PostgreSQL deployments.

    New Railway/PostgreSQL databases are normally created by db.create_all(); these
    ALTER statements mainly protect old installations and avoid PostgreSQL syntax
    issues caused by unquoted reserved identifiers such as the user table.
    """
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    if "user" in tables:
        columns = {column["name"] for column in inspector.get_columns("user")}
        _add_column("user", "is_admin", f"BOOLEAN NOT NULL DEFAULT {_bool_default(False)}", columns)
        _add_column("user", "role", "VARCHAR(32) NOT NULL DEFAULT 'operator'", columns)
        _add_column("user", "permissions_json", "TEXT", columns)
        _add_column("user", "is_active", f"BOOLEAN NOT NULL DEFAULT {_bool_default(True)}", columns)
        _add_column("user", "last_login_at", _dt_type(), columns)
        _add_column("user", "last_failed_login_at", _dt_type(), columns)
        _add_column("user", "failed_login_count", "INTEGER NOT NULL DEFAULT 0", columns)
        _add_column("user", "locked_until", _dt_type(), columns)
        db.session.execute(text(f"UPDATE {_q('user')} SET {_q('role')}='super_admin' WHERE {_q('is_admin')}={_bool_default(True)} AND ({_q('role')} IS NULL OR {_q('role')}='operator')"))
        db.session.commit()

    if "message_campaigns" in tables:
        columns = {column["name"] for column in inspector.get_columns("message_campaigns")}
        _add_column("message_campaigns", "content_item_id", "INTEGER", columns)
        _add_column("message_campaigns", "scheduled_at", _dt_type(), columns)
        _add_column("message_campaigns", "send_window_start", "VARCHAR(5)", columns)
        _add_column("message_campaigns", "send_window_end", "VARCHAR(5)", columns)
        _add_column("message_campaigns", "repeat_rule", "VARCHAR(16) NOT NULL DEFAULT 'none'", columns)
        _add_column("message_campaigns", "source_mode", "VARCHAR(32) NOT NULL DEFAULT 'content'", columns)
        _add_column("message_campaigns", "forward_source_ref", "VARCHAR(500)", columns)

    if "message_tasks" in tables:
        columns = {column["name"] for column in inspector.get_columns("message_tasks")}
        _add_column("message_tasks", "content_item_id", "INTEGER", columns)
        _add_column("message_tasks", "batch_size", "INTEGER NOT NULL DEFAULT 30", columns)
        _add_column("message_tasks", "source_mode", "VARCHAR(32) NOT NULL DEFAULT 'content'", columns)
        _add_column("message_tasks", "forward_source_ref", "VARCHAR(500)", columns)
        _add_column("message_tasks", "risk_override", f"BOOLEAN NOT NULL DEFAULT {_bool_default(False)}", columns)
        _add_column("message_tasks", "risk_score", "INTEGER NOT NULL DEFAULT 0", columns)
        _add_column("message_tasks", "last_risk_reason", "TEXT", columns)

    if "search_jobs" in tables:
        columns = {column["name"] for column in inspector.get_columns("search_jobs")}
        _add_column("search_jobs", "search_scope", "VARCHAR(32) NOT NULL DEFAULT 'global_plus_joined'", columns)
        _add_column("search_jobs", "include_public_messages", f"BOOLEAN NOT NULL DEFAULT {_bool_default(True)}", columns)
        _add_column("search_jobs", "exclude_system_sources", f"BOOLEAN NOT NULL DEFAULT {_bool_default(True)}", columns)
        _add_column("search_jobs", "expanded_queries_json", "TEXT", columns)

    if "join_jobs" in tables:
        columns = {column["name"] for column in inspector.get_columns("join_jobs")}
        additions = {
            "selection_mode": "VARCHAR(32) NOT NULL DEFAULT 'selected'",
            "auto_continue": f"BOOLEAN NOT NULL DEFAULT {_bool_default(False)}",
            "batch_pause_seconds": "INTEGER NOT NULL DEFAULT 300",
            "max_batches": "INTEGER NOT NULL DEFAULT 1",
            "batch_index": "INTEGER NOT NULL DEFAULT 1",
            "parent_job_id": "INTEGER",
            "rate_limited_until": _dt_type(),
            "auto_resume": f"BOOLEAN NOT NULL DEFAULT {_bool_default(True)}",
        }
        for name, definition in additions.items():
            _add_column("join_jobs", name, definition, columns)

    if "join_job_items" in tables:
        columns = {column["name"] for column in inspector.get_columns("join_job_items")}
        _add_column("join_job_items", "next_attempt_at", _dt_type(), columns)

    if "channel_posts" in tables:
        columns = {column["name"] for column in inspector.get_columns("channel_posts")}
        _add_column("channel_posts", "scheduled_at", _dt_type(), columns)
        _add_column("channel_posts", "auto_pin", f"BOOLEAN NOT NULL DEFAULT {_bool_default(False)}", columns)
        _add_column("channel_posts", "pinned_at", _dt_type(), columns)
