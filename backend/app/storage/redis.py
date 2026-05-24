import ssl
import redis.asyncio as redis
from typing import AsyncGenerator
from app.config import settings

# Strip query params that redis-py cannot parse correctly (ssl_cert_reqs)
_redis_url = settings.REDIS_URL.split("?")[0]

# Create a global Redis connection pool
kwargs = {"decode_responses": True}
if _redis_url.startswith("rediss://"):
    kwargs["ssl_cert_reqs"] = ssl.CERT_NONE

redis_client = redis.from_url(_redis_url, **kwargs)

async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    """
    Dependency to yield the Redis client for FastAPI routes or services.
    """
    try:
        yield redis_client
    finally:
        pass # The global pool manages connections automatically
