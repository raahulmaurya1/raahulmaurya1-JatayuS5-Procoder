import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['GLOG_minloglevel'] = '2'
import logging
import warnings
logging.getLogger("tensorflow").setLevel(logging.ERROR)
logging.getLogger("absl").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=DeprecationWarning)
import sys
from loguru import logger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.middleware.prefix_validation import PrefixValidationMiddleware
from app.api.auth_routes import router as auth_router
from app.api.onboarding_routes import router as onboarding_router
from app.api.review_routes import router as review_router
from app.api.ops_routes import router as ops_router
from app.api.decision_routes import router as decision_router
from app.api.face_routes import router as face_router
from app.api.risk_review_routes import router as risk_review_router
from app.api.health_routes import router as health_router
from app.api.bank_branch_routes import router as bank_branch_router
from app.api.admin_routes import router as admin_router
from fastapi.staticfiles import StaticFiles

# Configure loguru logger
logger.remove()
# Ensure GUNICORN_LOG_PATH directory exists to avoid telemetry failures
from app.config import settings
import os
os.makedirs(os.path.dirname(settings.GUNICORN_LOG_PATH), exist_ok=True)

logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO",
    enqueue=True,
    colorize=True,
)
logger.add(
    settings.GUNICORN_LOG_PATH,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
    level="INFO",
    rotation="10 MB",
    retention="7 days",
    enqueue=True,
)

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from app.config import settings

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
    )

from app.rate_limiter import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Warm up DB pool and intent cache table BEFORE the first request."""
    from loguru import logger as _log

    # ── 1. Warm the asyncpg connection pool + ensure risk_evaluations table ──
    try:
        from app.db.vector_store import get_pool
        await get_pool()
        _log.info("[STARTUP] ✓ asyncpg pool + risk_evaluations table ready")
    except Exception as exc:
        _log.warning(f"[STARTUP] asyncpg pool warm-up failed (non-fatal): {exc}")

    # ── 2. Ensure the intent_cache pgvector table exists ─────────────────────
    try:
        from app.agents.intent_cache_service import _ensure_table
        await _ensure_table()
        _log.info("[STARTUP] ✓ intent_cache table ready")
    except Exception as exc:
        _log.warning(f"[STARTUP] intent_cache table warm-up failed (non-fatal): {exc}")

    yield  # Application runs here

app = FastAPI(title="Bank Onboarding System API", openapi_version="3.0.2", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.get("/health")
async def root_health():
    """Root-level liveness probe for DigitalOcean App Platform."""
    return {"status": "ok"}

app.add_middleware(PrefixValidationMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")
app.include_router(onboarding_router, prefix="/api")
app.include_router(review_router, prefix="/api")
app.include_router(ops_router, prefix="/api")
app.include_router(decision_router, prefix="/api")
app.include_router(face_router, prefix="/api/v1/face", tags=["Face Verification"])
app.include_router(risk_review_router, prefix="/api", tags=["Risk Review"])
app.include_router(health_router, prefix="/api")
app.include_router(bank_branch_router, prefix="/api", tags=["Bank Branches"])
app.include_router(admin_router, prefix="/api", tags=["Admin"])
# ── Admin static panel ──────────────────────────────────────────────────────
# Served at: http://localhost:8000/admin/bank_branches.html
import os as _os
_static_dir = _os.path.join(_os.path.dirname(__file__), "static", "admin")
_os.makedirs(_static_dir, exist_ok=True)
app.mount("/admin", StaticFiles(directory=_static_dir, html=True), name="admin")



