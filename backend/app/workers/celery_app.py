import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['GLOG_minloglevel'] = '2'
import logging
import warnings
logging.getLogger("tensorflow").setLevel(logging.ERROR)
logging.getLogger("absl").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", category=DeprecationWarning)
from celery import Celery
from loguru import logger
from app.config import settings

os.makedirs(os.path.dirname(settings.CELERY_LOG_PATH), exist_ok=True)
logger.add(
    settings.CELERY_LOG_PATH,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} - {message}",
    level="INFO",
    rotation="10 MB",
    retention="7 days",
    enqueue=True,
)

# ------------------------------------------------------------------
# Local Redis (Broker)
# ------------------------------------------------------------------
import os
BROKER_URL = os.environ.get("CELERY_BROKER_URL", settings.REDIS_URL)
REDIS_URL = settings.REDIS_URL

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[CeleryIntegration()],
        traces_sample_rate=1.0,
    )

celery_app = Celery(
    "bank_worker",
    broker=BROKER_URL,
    backend=REDIS_URL,
    include=[
        "app.workers.tasks.extraction",
        "app.workers.tasks.face_verification_tasks"
        # Note: process_sme_documents_async lives in app.workers.tasks.extraction
        # and is auto-registered via the @celery_app.task decorator in that module.
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # ---------------------------------------------------------------
    # Local Redis stability settings
    # ---------------------------------------------------------------
    broker_url=BROKER_URL,
    task_ignore_result=True,
    worker_send_task_events=False,
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
)

logger.info("[OnboardAI][CELERY] Booting Celery... Pre-warming ML models into RAM in global scope.")
try:
    # Import locally to avoid circular dependencies
    from app.services.face_verification.face_service import prewarm_deepface_model
    from app.services.face_verification.liveness_service import prewarm_landmarker
    prewarm_deepface_model()
    prewarm_landmarker()
    logger.info("[OnboardAI][CELERY] ML models successfully pre-warmed.")
except Exception as e:
    logger.warning(f"[OnboardAI][CELERY] Warning: Failed to pre-warm ML models: {e}")
