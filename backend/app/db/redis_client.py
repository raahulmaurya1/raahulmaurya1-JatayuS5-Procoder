import ssl
import redis
import json
from app.config import settings

# ------------------------------------------------------------------
# Synchronous Redis client for Celery workers (extraction caching).
# Connects via URL to support Upstash TLS (rediss://) connections.
#
# NOTE: redis-py does not correctly parse `ssl_cert_reqs` from the
# URL query string — it passes the raw string "none" which crashes.
# We strip it from the URL and pass the native ssl constant instead.
# ------------------------------------------------------------------
_redis_url = settings.REDIS_URL.split("?")[0]  # Strip query params

kwargs = {"decode_responses": True}
if _redis_url.startswith("rediss://"):
    kwargs["ssl_cert_reqs"] = ssl.CERT_NONE

redis_client = redis.Redis.from_url(_redis_url, **kwargs)

def save_temp_extraction(session_id: str, data: dict, expire_seconds: int = 3600):
    """Saves the unverified extraction struct and file MinIO paths for 1 hour."""
    redis_client.setex(f"extractor:{session_id}", expire_seconds, json.dumps(data))

def get_temp_extraction(session_id: str) -> dict:
    """Retrieves the pending extraction data waiting for human approval."""
    data = redis_client.get(f"extractor:{session_id}")
    if data:
        return json.loads(data)
    return None

def clear_temp_extraction(session_id: str):
    """Purges the cached extraction metadata post-confirmation."""
    redis_client.delete(f"extractor:{session_id}")
