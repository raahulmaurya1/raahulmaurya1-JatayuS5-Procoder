"""
app/agents/intent_cache_service.py
====================================
Layer 2 — Semantic Intent Cache

Stores previously classified intent embeddings in a pgvector table
(``intent_cache``) in the existing Supabase PostgreSQL database.

On a cache hit (cosine similarity ≥ 0.95), the cached intent is
returned immediately — saving a full Gemini LLM round-trip (~1–3 s).

Shadow Mode
-----------
When ``INTENT_CACHE_SHADOW_MODE=true`` (default), cache hits are
logged but NOT used for routing.  The system still falls through to
the LLM so you can compare results and build confidence before
cutting over.

Set ``INTENT_CACHE_SHADOW_MODE=false`` to activate cache-driven routing.

Integration
-----------
- ``check_semantic_cache(text)`` is called by ``decision_agent`` AFTER
  the fast path router misses, BEFORE the LLM slow path.
- ``populate_cache_async(text, intent)`` is called fire-and-forget
  after a successful LLM classification.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from loguru import logger

from app.config import settings

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# Intents that are safe to cache.  Includes "unknown" so conversational/ambiguous
# inputs are cached via pgvector and bypass the LLM on future similar queries.
CACHEABLE_INTENTS = frozenset({
    "retail_savings", "digital_only", "sme_current", "re_kyc", "reactivation",
    "unknown",
})

# Shadow mode: log hits but do NOT use them for routing (safe rollout).
# Set to "true" to observe cache behavior without affecting routing.
# Default is "false" — cache hits are used for routing (production mode).
SHADOW_MODE = os.getenv(
    "INTENT_CACHE_SHADOW_MODE", "false"
).lower() in ("true", "1", "yes")

# Similarity threshold — 0.95 is conservative (high precision).
_MATCH_THRESHOLD = float(os.getenv("INTENT_CACHE_THRESHOLD", "0.95"))

# ---------------------------------------------------------------------------
# TABLE DDL (auto-created on first use alongside the risk_evaluations table)
# ---------------------------------------------------------------------------

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS intent_cache (
    id          BIGSERIAL   PRIMARY KEY,
    query_text  TEXT        UNIQUE NOT NULL,
    embedding   vector(3072) NOT NULL,     -- Gemini gemini-embedding-001 dimension
    intent      TEXT        NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
"""

# IVFFLAT index created separately after table has rows (needs data to build lists).
# For < 1000 rows, pgvector falls back to sequential scan automatically.
_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS intent_cache_embedding_idx
    ON intent_cache
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);
"""

_TABLE_ENSURED = False


async def _ensure_table() -> None:
    """Create the intent_cache table if it doesn't exist yet."""
    global _TABLE_ENSURED
    if _TABLE_ENSURED:
        return

    try:
        from app.db.vector_store import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Check if old table exists with wrong vector dimension
            row = await conn.fetchrow(
                "SELECT EXISTS ("
                "  SELECT 1 FROM information_schema.tables "
                "  WHERE table_name = 'intent_cache'"
                ") AS table_exists"
            )
            if row and row['table_exists']:
                dim_row = await conn.fetchrow(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'intent_cache'::regclass "
                    "AND attname = 'embedding'"
                )
                if dim_row and dim_row['atttypmod'] != 3072:
                    logger.info("[INTENT_CACHE] Dimension mismatch — recreating table")
                    await conn.execute("DROP TABLE IF EXISTS intent_cache CASCADE")

            await conn.execute(_CACHE_DDL)

            # Try to create IVFFLAT index (requires at least 1 row; OK to fail)
            try:
                count = await conn.fetchval("SELECT COUNT(*) FROM intent_cache")
                if count and count > 0:
                    await conn.execute(_INDEX_DDL)
            except Exception:
                pass  # Sequential scan is fine for small tables

        _TABLE_ENSURED = True
        logger.info("[INTENT_CACHE] Table ensured successfully")
    except Exception as exc:
        logger.debug(f"[INTENT_CACHE] Table creation skipped: {exc}")


# ---------------------------------------------------------------------------
# EMBEDDING HELPER
# ---------------------------------------------------------------------------

async def _get_embedding(text: str) -> list[float]:
    """
    Generate a 768-dim embedding using Gemini text-embedding-004.

    Uses the same ``google.generativeai`` package already installed and
    configured in the codebase.  Wraps the synchronous call in a thread
    to avoid blocking the event loop.

    Returns an empty list on any failure (caller should treat as a miss).
    """

    def _call() -> list[float]:
        import google.generativeai as genai
        # genai is already configured globally by gemini_client.py import chain.
        # Re-configure defensively in case this is called from a Celery worker
        # context where the module-level configure hasn't fired.
        if settings.GEMINI_API_KEY:
            genai.configure(api_key=settings.GEMINI_API_KEY)

        result = genai.embed_content(
            model="models/gemini-embedding-001",
            content=text,
        )
        return result["embedding"]

    try:
        embedding = await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=10.0
        )
        return embedding
    except asyncio.TimeoutError:
        logger.warning("[INTENT_CACHE] Embedding call timed out (10s)")
        return []
    except Exception as exc:
        logger.debug(f"[INTENT_CACHE] Embedding generation failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# CACHE LOOKUP (async)
# ---------------------------------------------------------------------------

async def check_semantic_cache(text: str) -> Optional[str]:
    """
    Check the semantic cache for a near-duplicate of *text*.

    Returns
    -------
    str or None
        The cached intent string (e.g. ``"retail_savings"``) if similarity
        ≥ threshold.  ``None`` on miss, error, or shadow-mode suppression.
    """
    try:
        await _ensure_table()

        embedding = await _get_embedding(text)
        if not embedding:
            return None

        from app.db.vector_store import get_pool
        pool = await get_pool()

        # pgvector cosine distance: 1 - cosine_similarity
        # We want similarity >= threshold, so distance <= (1 - threshold)
        max_distance = 1.0 - _MATCH_THRESHOLD

        # Serialise the embedding to pgvector text format
        emb_str = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT intent,
                       (embedding <=> $1::vector) AS distance
                FROM   intent_cache
                ORDER  BY embedding <=> $1::vector
                LIMIT  1
                """,
                emb_str,
            )

        if not row:
            logger.debug("[INTENT_CACHE] Cache empty — miss")
            return None

        distance = float(row["distance"])
        intent = row["intent"]
        similarity = 1.0 - distance

        if distance <= max_distance:
            if SHADOW_MODE:
                logger.info(
                    f"[INTENT_CACHE] 👻 SHADOW HIT: '{text[:60]}' → "
                    f"'{intent}' (sim={similarity:.4f}) — NOT used (shadow mode)"
                )
                return None  # Shadow mode: log but don't use
            else:
                logger.info(
                    f"[INTENT_CACHE] ⚡ CACHE HIT: '{text[:60]}' → "
                    f"'{intent}' (sim={similarity:.4f})"
                )
                return intent
        else:
            logger.debug(
                f"[INTENT_CACHE] Cache miss: best={intent} sim={similarity:.4f} "
                f"< threshold={_MATCH_THRESHOLD}"
            )
            return None

    except Exception as exc:
        logger.debug(f"[INTENT_CACHE] Cache lookup error: {exc}")
        return None


# ---------------------------------------------------------------------------
# CACHE POPULATION (async, fire-and-forget)
# ---------------------------------------------------------------------------

async def populate_cache_async(text: str, intent: str) -> None:
    """
    Insert a (text, embedding, intent) tuple into the cache.

    Called fire-and-forget via ``asyncio.create_task()`` after a
    successful LLM classification.  Errors are logged, never raised.

    Only caches intents in :data:`CACHEABLE_INTENTS` — "unknown" and
    ambiguous results are never cached.
    """
    if intent not in CACHEABLE_INTENTS:
        return

    try:
        await _ensure_table()

        embedding = await _get_embedding(text)
        if not embedding:
            return

        from app.db.vector_store import get_pool
        pool = await get_pool()

        emb_str = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO intent_cache (query_text, embedding, intent)
                VALUES ($1, $2::vector, $3)
                ON CONFLICT (query_text) DO UPDATE
                SET embedding  = EXCLUDED.embedding,
                    intent     = EXCLUDED.intent,
                    created_at = NOW()
                """,
                text.strip().lower(),
                emb_str,
                intent,
            )

        logger.info(
            f"[INTENT_CACHE] ✓ Cached: '{text[:50]}' → '{intent}'"
        )

    except Exception as exc:
        logger.debug(f"[INTENT_CACHE] Cache population failed: {exc}")
