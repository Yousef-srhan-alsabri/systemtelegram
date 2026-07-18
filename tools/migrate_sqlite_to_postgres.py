"""Optional one-time migration from local SQLite app.db to PostgreSQL.

Usage on your computer before/after Railway setup:

  set DATABASE_URL=postgresql://...        # Windows PowerShell: $env:DATABASE_URL="..."
  set SQLITE_PATH=instance/app.db          # PowerShell: $env:SQLITE_PATH="instance/app.db"
  python tools/migrate_sqlite_to_postgres.py --wipe

Notes:
- Keep SESSION_ENCRYPTION_KEYS identical to the old .env so encrypted Telegram sessions remain usable.
- Run this only on a trusted machine. Do not upload .env or app.db to public GitHub.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from sqlalchemy import MetaData, create_engine, select, text


def normalize_database_url(raw: str) -> str:
    url = (raw or "").strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default=os.getenv("SQLITE_PATH", "instance/app.db"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--wipe", action="store_true", help="Delete existing target rows before import")
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite).resolve()
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite file not found: {sqlite_path}")
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required")

    # Import app only after env is ready; create_app creates the PostgreSQL schema.
    os.environ["DATABASE_URL"] = args.database_url
    from app import create_app
    from app.extensions import db

    app = create_app()
    with app.app_context():
        target_engine = db.engine
        target_meta = db.metadata
        source_engine = create_engine(f"sqlite:///{sqlite_path}")
        source_meta = MetaData()
        source_meta.reflect(bind=source_engine)

        target_tables = {table.name: table for table in target_meta.sorted_tables}
        common_names = [table.name for table in target_meta.sorted_tables if table.name in source_meta.tables]

        with target_engine.begin() as conn:
            if args.wipe:
                for name in reversed(common_names):
                    conn.execute(target_tables[name].delete())

            with source_engine.connect() as src:
                for name in common_names:
                    source_table = source_meta.tables[name]
                    target_table = target_tables[name]
                    source_columns = set(source_table.columns.keys())
                    target_columns = set(target_table.columns.keys())
                    rows = []
                    for row in src.execute(select(source_table)).mappings():
                        rows.append({k: row[k] for k in source_columns & target_columns})
                    if not rows:
                        print(f"{name}: 0 rows")
                        continue
                    for i in range(0, len(rows), 500):
                        conn.execute(target_table.insert(), rows[i:i+500])
                    print(f"{name}: {len(rows)} rows")

            if target_engine.dialect.name == "postgresql":
                for name in common_names:
                    if "id" not in target_tables[name].columns:
                        continue
                    pg_table = f'"{name}"' if name == "user" else name
                    conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{pg_table}', 'id'), COALESCE((SELECT MAX(id) FROM \"{name}\"), 1), (SELECT MAX(id) IS NOT NULL FROM \"{name}\"))"))

    print("Migration completed.")


if __name__ == "__main__":
    main()
