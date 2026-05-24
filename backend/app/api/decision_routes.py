from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timezone
from app.agents.decision_agent import orchestrate_session
from app.db.base import AsyncSessionLocal
from app.rate_limiter import limiter

router = APIRouter()

class ChatRequest(BaseModel):
    user_message: str
    session_ulid: Optional[str] = None
    source: Optional[str] = None
    final_data: Optional[Dict[str, Any]] = None
    current_state: Optional[Dict[str, Any]] = {}

class ChatResponse(BaseModel):
    ui_action: str
    agent_message: str
    data_required: Optional[List[Union[str, Dict[str, Any]]]] = []
    session_ulid: Optional[str] = None
    extracted_data: Optional[Dict[str, Any]] = None
    current_state: Optional[Dict[str, Any]] = None

@router.post("/v1/orchestrator/chat", response_model=ChatResponse)
@limiter.limit("30/minute")
async def chat_orchestrator(request: Request, payload: ChatRequest):
    """
    Master Account Opening Orchestrator
    Acts as the dynamic entrypoint for the React Frontend Chat UI.
    """
    try:
        # ── Inject request-level telemetry into current_state for risk pipeline ──
        _state = payload.current_state or {}
        if request.client:
            _state.setdefault("client_ip", request.client.host)
        _state.setdefault(
            "account_created_at_utc",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        async with AsyncSessionLocal() as db:
            result = await orchestrate_session(
                message=payload.user_message,
                session_ulid=payload.session_ulid,
                current_state=_state,
                source=payload.source,
                final_data=payload.final_data,
                db=db
            )
            return result
    except Exception as e:
        from loguru import logger
        logger.error(f"Orchestration Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
