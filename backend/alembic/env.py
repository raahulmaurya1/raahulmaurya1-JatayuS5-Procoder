from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from app.db.base import Base
# Import all models so they are registered with Base.metadata before Alembic tries to compare them
from app.db.models.user import UserInitial
from app.db.models.document import UserDocument
from app.db.models.session import OnboardingSession
from app.db.models.agent import AgentContext

target_metadata = Base.metadata

# ------------------------------------------------------------------
# Supabase Migration: Read database URL from environment variables.
# DATABASE_SYNC_URL must be set in .env as a synchronous psycopg2 URL
# (no +asyncpg prefix). Alembic requires a synchronous driver.
#
# [OLD] Previously the URL came from alembic.ini:
#   sqlalchemy.url = postgresql+asyncpg://postgres:abc123@127.0.0.1:5433/bank_db
# ------------------------------------------------------------------
from dotenv import load_dotenv
load_dotenv(override=True)

def _get_sync_url() -> str:
    """
    Return the synchronous PostgreSQL URL for Alembic.

    Priority:
      1. DATABASE_SYNC_URL env var  (Supabase direct connection, port 5432)
      2. DATABASE_URL env var with +asyncpg stripped  (fallback)
      3. sqlalchemy.url from alembic.ini  (legacy fallback)
    """
    # Supabase recommended: use the direct / transaction-pooler sync URL
    sync_url = os.environ.get("DATABASE_SYNC_URL", "")
    if sync_url:
        return sync_url

    # Fallback: strip asyncpg driver from the async URL
    async_url = os.environ.get("DATABASE_URL", "")
    if async_url:
        return async_url.replace("+asyncpg", "")

    # Last resort: read from alembic.ini (old Docker value)
    return config.get_main_option("sqlalchemy.url", "").replace("+asyncpg", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = _get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


import asyncio
from sqlalchemy.engine import Connection, create_engine

def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Uses a synchronous engine connecting to Supabase via psycopg2.
    SSL is required by Supabase — connect_args enforces it.
    """
    sync_url = _get_sync_url()

    # Supabase requires SSL; connect_args is ignored for local Docker URLs.
    connect_args = {}
    if "supabase.com" in sync_url:
        connect_args["sslmode"] = "require"

    connectable = create_engine(
        sync_url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

