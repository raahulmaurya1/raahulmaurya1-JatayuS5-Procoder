"""
app/rate_limiter.py
====================
Centralized rate-limiter instance, imported by both main.py and route files.
Extracted into its own module to avoid circular imports (main.py ↔ routes).

IP Extraction Priority (for reverse proxy / CDN deployments):
  1. CF-Connecting-IP  — Cloudflare
  2. X-Forwarded-For   — Generic reverse proxy (DigitalOcean, nginx, etc.)
  3. request.client.host — Direct connection fallback
"""

from fastapi import Request
from slowapi import Limiter
from app.config import settings


def get_real_client_ip(request: Request) -> str:
    """
    Extract the real client IP behind a reverse proxy / CDN.
    """
    # Cloudflare always sets this header with the true client IP
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip

    # Standard proxy header — take the last (rightmost) IP which the ALB itself appends
    # to prevent client-side header spoofing.
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[-1].strip()

    # Direct connection (no proxy)
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(
    key_func=get_real_client_ip,
    storage_uri=settings.REDIS_URL.split("?")[0],
)
