"""
app/middleware/prefix_validation.py
====================================
High-performance session validation middleware.

Validates Bearer tokens against Upstash Redis (sub-millisecond lookups)
instead of hitting Postgres on every request. Session data is mirrored
to Redis at registration time by `app.agents.entry_agent.register_user`.

Key format:  session:{ULID}
Value:       JSON  {"uid": "...", "phone_number": "+91...", "email": "..."}
TTL:         30 minutes (auto-expires — no stale session risk)
"""

import json
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from loguru import logger


class PrefixValidationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Always allow browser preflight requests to pass through to CORSMiddleware
        if request.method == "OPTIONS":
            return await call_next(request)
            
        # We only apply this check for secure downstream paths
        if request.url.path.startswith(("/api/v1/upload", "/api/intent", "/api/upload", "/api/confirm-documents", "/api/finalize-documents")):
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return JSONResponse(status_code=401, content={"detail": "Missing Authorization header"})
            
            # Robust extraction, handle both "Bearer <token>" or raw "<token>" safely
            token = auth_header.replace("Bearer ", "").strip()
            
            try:
                # ---------------------------------------------------------------
                # HIGH-SPEED REDIS LOOKUP (replaces Postgres query)
                # Session data is mirrored to Redis at registration time.
                # Redis TTL auto-expires sessions — no stale token risk.
                # ---------------------------------------------------------------
                from app.storage.redis import redis_client as async_redis
                session_raw = await async_redis.get(f"session:{token}")
                
                if not session_raw:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Session expired or invalid. Please re-authenticate."}
                    )
                
                session_data = json.loads(session_raw)
                
                # Validate phone prefix constraint
                phone = session_data.get("phone_number", "")
                if not phone.startswith("+91"):
                    return JSONResponse(status_code=403, content={"detail": "Only phone numbers starting with +91 are permitted."})
                
                # Attach user data to request state for downstream use
                request.state.user = session_data
                
            except Exception as e:
                logger.exception(f"[AuthMiddleware] Token validation error: {e}")
                return JSONResponse(status_code=401, content={"detail": "Invalid authentication credentials"})

        response = await call_next(request)
        return response
