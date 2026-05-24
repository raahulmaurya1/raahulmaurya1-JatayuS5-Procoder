from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    # Required for Supabase: disables asyncpg prepared statement caching.
    # Without this, PgBouncer (transaction/statement pooling mode) raises:
    #   DuplicatePreparedStatementError: prepared statement "..." already exists
    # Safe to keep even on direct connections — minor perf trade-off, zero risk.
    connect_args={"statement_cache_size": 0},
    # Verify connections before use — prevents stale connection errors
    # caused by Supabase's idle connection timeouts.
    pool_pre_ping=True,
    # ---------------------------------------------------------------
    # Connection pool tuning for Supabase free tier.
    # Supabase transaction pooler typically allows 20-50 active connections.
    # pool_size=5 keeps a small resident pool; max_overflow=10 allows
    # bursts up to 15 total before requests queue.
    # ---------------------------------------------------------------
    pool_size=5,
    max_overflow=10,
)
AsyncSessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)

Base = declarative_base()

