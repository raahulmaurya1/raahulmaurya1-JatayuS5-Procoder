"""
DEPRECATED: This file is NOT used in production.
The active database engine lives in app/db/base.py and reads
DATABASE_URL from environment variables via app/config.py.

DO NOT import or use this file. It is kept only to avoid breaking
any Alembic-era import references that may still reference it.
"""
raise ImportError(
    "app.db.session is DEPRECATED. Use `from app.db.base import AsyncSessionLocal` "
    "or `from app.storage.postgres import get_db` instead."
)
