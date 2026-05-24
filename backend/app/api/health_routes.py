from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.storage.postgres import get_db
from app.storage.redis import redis_client
from loguru import logger
import time
from app.rate_limiter import limiter
import json

router = APIRouter(tags=["Health"])

@router.get("/health", status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
async def health_check(request: Request):
    """Basic health check for K8s/Docker liveness probe."""
    return {"status": "up", "timestamp": time.time()}

@router.get("/health/ready", status_code=status.HTTP_200_OK)
@limiter.limit("100/minute")
async def readiness_check(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Readiness probe to ensure database and cache dependencies are reachable.
    Used for traffic routing in cloud-native deployments.
    """
    health_status = {
        "status": "ready",
        "database": "unknown",
        "redis": "unknown"
    }
    
    try:
        # Check Database
        await db.execute(text("SELECT 1"))
        health_status["database"] = "up"
    except Exception as e:
        logger.error(f"Readiness Check Failed (Database): {e}")
        health_status["database"] = "down"
        health_status["status"] = "not_ready"
        
    try:
        # Check Redis
        if await redis_client.ping():  # type: ignore
            health_status["redis"] = "up"
        else:
            health_status["redis"] = "down"
            health_status["status"] = "not_ready"
    except Exception as e:
        logger.error(f"Readiness Check Failed (Redis): {e}")
        health_status["redis"] = "down"
        health_status["status"] = "not_ready"

    if health_status["status"] == "not_ready":
        # Return 503 Service Unavailable if any core dependency is down
        from fastapi import Response
        return Response(content=json.dumps(health_status), status_code=status.HTTP_503_SERVICE_UNAVAILABLE, media_type="application/json")
        
    return health_status
