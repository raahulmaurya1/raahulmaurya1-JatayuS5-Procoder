from pydantic_settings import BaseSettings
from pydantic import model_validator
from dotenv import load_dotenv
import os

# Intercept and force overwrite the Uvicorn environment cache
load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Compute a cross-platform base directory once at import time.
# __file__ == .../backend/app/config.py  →  dirname → .../backend/app
# join(.., "..") → .../backend
# ---------------------------------------------------------------------------
_base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Settings(BaseSettings):
    # ------------------------------------------------------------------
    # Database — Supabase PostgreSQL (async, used by SQLAlchemy/FastAPI)
    # Set DATABASE_URL in .env with your Supabase connection string.
    # Format: postgresql+asyncpg://postgres.[ref]:[pw]@[host]:6543/postgres
    # ------------------------------------------------------------------
    DATABASE_URL: str = ""

    # Sync URL used by Alembic migrations only (no +asyncpg driver prefix).
    # Format: postgresql://postgres.[ref]:[pw]@[host]:5432/postgres
    DATABASE_SYNC_URL: str = ""

    # ------------------------------------------------------------------
    # Upstash Redis (Cloud-hosted, TLS)
    # Set REDIS_URL in .env with your Upstash connection string.
    # Format: rediss://default:<password>@<endpoint>.upstash.io:6379/0
    # ------------------------------------------------------------------
    REDIS_URL: str = ""
    UPSTASH_REDIS_REST_URL: str = ""
    UPSTASH_REDIS_REST_TOKEN: str = ""

    # ------------------------------------------------------------------
    # Supabase Storage (S3-Compatible)
    # ------------------------------------------------------------------
    SUPABASE_STORAGE_ENDPOINT: str = ""
    SUPABASE_STORAGE_ACCESS_KEY: str = ""
    SUPABASE_STORAGE_SECRET_KEY: str = ""
    SUPABASE_STORAGE_REGION: str = "auto"
    SUPABASE_STORAGE_BUCKET_TEMP: str = "temp"
    SUPABASE_STORAGE_BUCKET_VERIFIED: str = "verified"

    GEMINI_API_KEY: str = ""
    TWO_FACTOR_API_KEY: str = ""
    OCR_SPACE_API_KEY: str = ""
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASS: str = ""

    # Risk Agent Log Paths — dynamically computed relative to project root.
    # Can be overridden by setting GUNICORN_LOG_PATH / CELERY_LOG_PATH in .env.
    GUNICORN_LOG_PATH: str = os.path.join(_base_dir, "logs", "gunicorn", "access.log")
    CELERY_LOG_PATH: str = os.path.join(_base_dir, "logs", "celery", "worker.log")

    # Face Verification & Liveness Settings
    FACE_MODEL_NAME: str = "OpenFace"
    FACE_SIMILARITY_THRESHOLD: float = 0.6
    BLINK_EAR_THRESHOLD: float = 0.2
    BLINK_CONSEC_FRAMES: int = 3
    MIN_BLINKS_FOR_LIVENESS: int = 1
    # Use /tmp/.deepface on AWS/Linux to avoid read-only root FS errors on ECS.
    DEEPFACE_HOME: str = os.environ.get(
        "DEEPFACE_HOME",
        os.path.expanduser("~/.deepface") if os.name == "nt" else "/tmp/.deepface",
    )

    SENTRY_DSN: str = ""

    # ------------------------------------------------------------------
    # CORS — Dynamic origins from environment (comma-separated string).
    # Example: CORS_ORIGINS=https://my-app.amazonaws.com,https://my-domain.com
    # ------------------------------------------------------------------
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse the comma-separated CORS_ORIGINS string into a clean list."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _validate_critical_settings(self) -> "Settings":
        """
        Fail fast at startup if critical environment variables are missing.
        This prevents silent mid-request crashes on AWS ECS/EC2.
        """
        missing: list[str] = []
        if not self.DATABASE_URL:
            missing.append("DATABASE_URL")
        if not self.REDIS_URL:
            missing.append("REDIS_URL")
        if not self.GEMINI_API_KEY:
            missing.append("GEMINI_API_KEY")
        if missing:
            raise ValueError(
                f"[OnboardAI] STARTUP FAILED — missing required environment variables: "
                f"{', '.join(missing)}. "
                f"Set these in your .env file or AWS ECS Task Definition environment."
            )
        return self

    model_config = {
        "env_file": ".env",
        "extra": "ignore"
    }

settings = Settings()
