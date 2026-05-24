"""
app/api/risk_review_routes.py
==============================
API endpoints for the human-review workflow.

Provides:
    POST /v1/risk-review/approve   — approve a pending_review application
    POST /v1/risk-review/reject    — reject  a pending_review application

Both endpoints validate that the user exists and is currently in
``pending_review`` status before taking action.
"""

from __future__ import annotations

from loguru import logger
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.models.user import UserInitial
from app.storage.postgres import get_db
from app.services.risk_engine import collect_review_data

# ---------------------------------------------------------------------------
# Module-level logger — MUST NOT emit PII
# ---------------------------------------------------------------------------
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ReviewDecisionRequest(BaseModel):
    """Payload for approve / reject endpoints."""
    user_id: str


class ReviewDecisionResponse(BaseModel):
    """Standardised response for review decisions."""
    status: str
    message: str


# ---------------------------------------------------------------------------
# Helper: fetch and validate user
# ---------------------------------------------------------------------------


async def _get_pending_review_user(
    user_id: str,
    db: AsyncSession,
) -> UserInitial:
    """
    Retrieve the ``UserInitial`` row for *user_id* and verify it is
    currently in ``pending_review`` status.

    Raises
    ------
    HTTPException 404
        If no user row is found.
    HTTPException 409
        If the user exists but is not in ``pending_review`` status.
    """
    result = await db.execute(
        select(UserInitial).where(UserInitial.id == user_id)
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail=f"User not found.",
        )

    if user.status not in ("MANUAL_REVIEW", "UNDER_REVIEW"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"User is not in 'MANUAL_REVIEW' or 'UNDER_REVIEW' status "
                f"(current: '{user.status}'). Cannot proceed."
            ),
        )

    return user


# ---------------------------------------------------------------------------
# GET /v1/risk-review/pending
# ---------------------------------------------------------------------------

@router.get(
    "/v1/risk-review/pending",
    summary="Get all applications currently waiting for manual review",
)
async def get_pending_reviews(
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve all users with status='MANUAL_REVIEW' or 'UNDER_REVIEW'."""
    result = await db.execute(
        select(UserInitial)
        .where(UserInitial.status.in_(["MANUAL_REVIEW", "UNDER_REVIEW"]))
        .order_by(UserInitial.created_at.desc())
    )
    users = result.scalars().all()

    queue = [
        {
            "id": u.id,
            "name": u.name or "Unknown Applicant",
            "email": u.email or "No Email",
            "phone": u.phone or "No Phone",
            "status": u.status,
            "created_at": str(u.created_at) if u.created_at else None
        }
        for u in users
    ]

    return {"status": "success", "count": len(queue), "queue": queue}


# ---------------------------------------------------------------------------
# GET /v1/risk-review/data
# ---------------------------------------------------------------------------


@router.get(
    "/v1/risk-review/data",
    summary="Get review data for a pending_review application",
)
async def get_review_data(
    user_id: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Retrieve all data needed for a bank reviewer to evaluate an application.

    Returns user info, additional info, documents (with base64 content
    from MinIO), and risk flags.

    Query Parameters
    ----------------
    user_id : str
        The ``user_initial.id`` (ULID) of the applicant.
    """
    # Validate user exists
    result = await db.execute(
        select(UserInitial).where(UserInitial.id == user_id)
    )
    user = result.scalars().first()

    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found.",
        )

    from app.db.vector_store import get_risk_flags_for_request
    flags_data = await get_risk_flags_for_request(user_id)
    risk_flags = flags_data.get("risk_flags", [])
    llm_flags = flags_data.get("llm_flags", [])

    # Collect all review data using the risk engine's collector
    review_data = await collect_review_data(
        user_id=user_id,
        risk_flags=risk_flags,
        llm_flags=llm_flags,
    )

    logger.info(
        "risk_review: data fetched for user=%s",
        user_id[:8] + "…",
    )

    return review_data


# ---------------------------------------------------------------------------
# POST /v1/risk-review/approve
# ---------------------------------------------------------------------------


@router.post(
    "/v1/risk-review/approve",
    response_model=ReviewDecisionResponse,
    summary="Approve a pending review application",
)
async def approve_application(
    payload: ReviewDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Mark a ``pending_review`` application as **approved**.

    Expects ``{ "user_id": "<ULID>" }`` in the request body.
    """
    import asyncio
    import random
    from app.db.models.user import AdditionalInfo
    from app.db.models.bank_branch import BankBranch
    from app.services.email_service import send_approval_email

    user = await _get_pending_review_user(payload.user_id, db)

    # 1. Update User status
    await db.execute(
        update(UserInitial)
        .where(UserInitial.id == payload.user_id)
        .values(status="approved")
    )
    
    # 2. Generate Account Number and save it
    acct_no = str(random.randint(1_000_000_000, 9_999_999_999))
    
    _stmt_info = select(AdditionalInfo).where(AdditionalInfo.session_ulid == payload.user_id)
    _res_info = await db.execute(_stmt_info)
    additional_info = _res_info.scalar_one_or_none()
    
    pref_ifsc = ""
    if additional_info:
        additional_info.account_number = acct_no
        pref_ifsc = additional_info.pref_branch or ""

    # 3. Update Risk Outcome
    from app.db.vector_store import update_risk_outcome
    await update_risk_outcome(payload.user_id, "APPROVED")
    
    await db.commit()
    
    # 4. Fetch Branch details for the email
    branch_name = ""
    branch_address = ""
    manager_name = None
    relationship_officer = None
    
    if pref_ifsc:
        _br_res = await db.execute(select(BankBranch).where(BankBranch.ifsc == pref_ifsc))
        branch = _br_res.scalars().first()
        if branch:
            branch_name = branch.branch_name
            branch_address = branch.branch_address
            manager_name = branch.manager_name
            relationship_officer = branch.relationship_officer

    # 5. Send Email
    if user.email:
        asyncio.create_task(send_approval_email(
            email=user.email,
            name=user.name or "",
            application_id=payload.user_id,
            account_number=acct_no,
            branch_name=branch_name,
            branch_address=branch_address,
            ifsc=pref_ifsc,
            account_type=user.account_type or "",
            manager_name=manager_name,
            relationship_officer=relationship_officer,
        ))

    logger.info(
        "risk_review: APPROVED user=%s (reviewer action)",
        payload.user_id[:8] + "…",
    )
    return {
        "status": "success",
        "message": "Application approved successfully.",
    }


# ---------------------------------------------------------------------------
# POST /v1/risk-review/reject
# ---------------------------------------------------------------------------


@router.post(
    "/v1/risk-review/reject",
    response_model=ReviewDecisionResponse,
    summary="Reject a pending review application",
)
async def reject_application(
    payload: ReviewDecisionRequest,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Mark a ``pending_review`` application as **rejected**.

    The ``user_initial`` row is preserved for audit trail; only the
    status column is updated.  Associated data (``additional_info``,
    ``user_documents``, MinIO files) is intentionally kept.

    Expects ``{ "user_id": "<ULID>" }`` in the request body.
    """
    user = await _get_pending_review_user(payload.user_id, db)

    await db.execute(
        update(UserInitial)
        .where(UserInitial.id == payload.user_id)
        .values(status="rejected")
    )
    from app.db.vector_store import update_risk_outcome
    await update_risk_outcome(payload.user_id, "REJECTED")
    
    await db.commit()

    import asyncio
    from app.services.email_service import send_rejection_email
    
    if user.email:
        asyncio.create_task(send_rejection_email(
            email=user.email,
            name=user.name or "",
            application_id=payload.user_id,
        ))
    logger.info(
        "risk_review: REJECTED user=%s (reviewer action)",
        payload.user_id[:8] + "…",
    )
    return {
        "status": "success",
        "message": "Application rejected.",
    }
