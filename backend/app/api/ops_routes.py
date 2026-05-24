from fastapi import APIRouter, Request
from pydantic import BaseModel
from loguru import logger
from app.rate_limiter import limiter
router = APIRouter()

class EscalationRequest(BaseModel):
    session_id: str
    risk_score: float
    reason: str

@router.post("/v1/ops/notify")
@limiter.limit("10/minute")
async def notify_bank_staff(request: Request, payload: EscalationRequest):
    """
    Accepts high-risk session IDs and simulates sending a notification to bank staff.
    """
    logger.critical(f"STAFF ESCALATION TRIGGERED for Session {payload.session_id}")
    logger.critical(f"Reason: {payload.reason} (Risk Score: {payload.risk_score})")
    
    return {
        "status": "notified",
        "message": f"Bank staff have been alerted for session {payload.session_id}"
    }
