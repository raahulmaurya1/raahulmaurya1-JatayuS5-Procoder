"""
app/api/admin_routes.py
========================
API endpoints for Admin Panel operations.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Dict, Any

from app.db.models.user import UserInitial, AdditionalInfo
from app.storage.postgres import get_db

router = APIRouter()

@router.get(
    "/v1/admin/accounts/active",
    summary="Get all active accounts (with account numbers)",
)
async def get_active_accounts(
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """
    Retrieve all users whose account_number is set in additional_info.
    """
    stmt = (
        select(UserInitial, AdditionalInfo)
        .join(AdditionalInfo, UserInitial.id == AdditionalInfo.session_ulid)
        .where(AdditionalInfo.account_number.isnot(None))
        .order_by(UserInitial.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    accounts = []
    for user, info in rows:
        accounts.append({
            "id": user.id,
            "name": user.name or "Unknown",
            "email": user.email,
            "phone": user.phone,
            "status": user.status,
            "account_number": info.account_number,
            "account_type": user.account_type,
            "created_at": str(user.created_at) if user.created_at else None,
            "branch_ifsc": info.pref_branch
        })

    return {"status": "success", "count": len(accounts), "accounts": accounts}
