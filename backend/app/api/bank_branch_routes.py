"""
app/api/bank_branch_routes.py
==============================
API endpoints for the bank_branch table.

Public:
    GET /api/v1/bank-branches          — list all; optional ?account_type= filter
    GET /api/v1/bank-branches/{ifsc}   — single branch detail

Admin (no auth enforced at router level — add middleware/API-key as needed):
    POST   /api/v1/admin/bank-branches          — create a branch
    PUT    /api/v1/admin/bank-branches/{ifsc}   — update a branch
    DELETE /api/v1/admin/bank-branches/{ifsc}   — delete a branch
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.bank_branch import BankBranch
from app.storage.postgres import get_db

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class BranchOut(BaseModel):
    ifsc: str
    branch_name: str
    branch_address: str
    supported_account_type: str
    manager_name: Optional[str] = None
    manager_email: Optional[str] = None
    manager_phone: Optional[str] = None
    relationship_officer: Optional[str] = None

    class Config:
        from_attributes = True


class BranchCreate(BaseModel):
    ifsc: str
    branch_name: str
    branch_address: str
    supported_account_type: str          # e.g. "Retail, SME, Digital"
    manager_name: Optional[str] = None
    manager_email: Optional[str] = None
    manager_phone: Optional[str] = None
    relationship_officer: Optional[str] = None


class BranchUpdate(BaseModel):
    branch_name: Optional[str] = None
    branch_address: Optional[str] = None
    supported_account_type: Optional[str] = None
    manager_name: Optional[str] = None
    manager_email: Optional[str] = None
    manager_phone: Optional[str] = None
    relationship_officer: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Map UI / backend account_type strings to the substrings stored in
# supported_account_type (comma-separated, e.g. "Retail, SME, Digital")
_TYPE_KEYWORD_MAP: Dict[str, str] = {
    "retail_savings": "Retail",
    "retail":         "Retail",
    "sme_current":    "SME",
    "sme":            "SME",
    "digital_only":   "Digital",
    "digital":        "Digital",
}


def _keyword_for(account_type: str) -> str:
    """Return the keyword to search for in supported_account_type."""
    lower_type = account_type.lower().strip()
    if "retail" in lower_type:
        return "Retail"
    if "sme" in lower_type:
        return "SME"
    if "digital" in lower_type:
        return "Digital"
    return _TYPE_KEYWORD_MAP.get(lower_type, account_type.title())


def _branch_to_dict(branch: BankBranch) -> Dict[str, Any]:
    return {
        "ifsc":                   branch.ifsc,
        "branch_name":            branch.branch_name,
        "branch_address":         branch.branch_address,
        "supported_account_type": branch.supported_account_type,
        "manager_name":           branch.manager_name,
        "manager_email":          branch.manager_email,
        "manager_phone":          branch.manager_phone,
        "relationship_officer":   branch.relationship_officer,
    }


# ---------------------------------------------------------------------------
# Public: GET /api/v1/bank-branches
# ---------------------------------------------------------------------------

@router.get(
    "/v1/bank-branches",
    summary="List bank branches, optionally filtered by account type",
)
async def list_branches(
    account_type: Optional[str] = Query(
        None,
        description="Filter branches that support this account type "
                    "(e.g. retail_savings, sme_current, digital_only)",
    ),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    stmt = select(BankBranch)

    if account_type:
        keyword = _keyword_for(account_type)
        # Case-insensitive substring match on the comma-separated string
        stmt = stmt.where(
            BankBranch.supported_account_type.ilike(f"%{keyword}%")
        )

    result = await db.execute(stmt)
    branches = result.scalars().all()

    logger.info(
        "bank_branch: list requested | account_type=%s | found=%d",
        account_type,
        len(branches),
    )
    return [_branch_to_dict(b) for b in branches]


# ---------------------------------------------------------------------------
# Public: GET /api/v1/bank-branches/{ifsc}
# ---------------------------------------------------------------------------

@router.get(
    "/v1/bank-branches/{ifsc}",
    summary="Get a single branch by IFSC",
)
async def get_branch(
    ifsc: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    result = await db.execute(
        select(BankBranch).where(BankBranch.ifsc == ifsc.upper())
    )
    branch = result.scalars().first()
    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch with IFSC '{ifsc}' not found.")
    return _branch_to_dict(branch)


# ---------------------------------------------------------------------------
# Admin: POST /api/v1/admin/bank-branches
# ---------------------------------------------------------------------------

@router.post(
    "/v1/admin/bank-branches",
    summary="[Admin] Create a new bank branch",
    status_code=201,
)
async def create_branch(
    payload: BranchCreate,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    existing = await db.execute(
        select(BankBranch).where(BankBranch.ifsc == payload.ifsc.upper())
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail=f"Branch with IFSC '{payload.ifsc}' already exists.",
        )

    branch = BankBranch(
        ifsc=payload.ifsc.upper(),
        branch_name=payload.branch_name,
        branch_address=payload.branch_address,
        supported_account_type=payload.supported_account_type,
        manager_name=payload.manager_name,
        manager_email=payload.manager_email,
        manager_phone=payload.manager_phone,
        relationship_officer=payload.relationship_officer,
    )
    db.add(branch)
    await db.commit()
    await db.refresh(branch)
    logger.info("bank_branch: created ifsc=%s", branch.ifsc)
    return _branch_to_dict(branch)


# ---------------------------------------------------------------------------
# Admin: PUT /api/v1/admin/bank-branches/{ifsc}
# ---------------------------------------------------------------------------

@router.put(
    "/v1/admin/bank-branches/{ifsc}",
    summary="[Admin] Update a bank branch",
)
async def update_branch(
    ifsc: str,
    payload: BranchUpdate,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    values = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not values:
        raise HTTPException(status_code=400, detail="No update fields provided.")

    result = await db.execute(
        update(BankBranch)
        .where(BankBranch.ifsc == ifsc.upper())
        .values(**values)
        .returning(BankBranch)
    )
    updated = result.scalars().first()
    if not updated:
        raise HTTPException(status_code=404, detail=f"Branch '{ifsc}' not found.")
    await db.commit()
    logger.info("bank_branch: updated ifsc=%s", ifsc.upper())
    return _branch_to_dict(updated)


# ---------------------------------------------------------------------------
# Admin: DELETE /api/v1/admin/bank-branches/{ifsc}
# ---------------------------------------------------------------------------

@router.delete(
    "/v1/admin/bank-branches/{ifsc}",
    summary="[Admin] Delete a bank branch",
)
async def delete_branch(
    ifsc: str,
    db: AsyncSession = Depends(get_db),
) -> Dict[str, str]:
    result = await db.execute(
        delete(BankBranch).where(BankBranch.ifsc == ifsc.upper())
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Branch '{ifsc}' not found.")
    await db.commit()
    logger.info("bank_branch: deleted ifsc=%s", ifsc.upper())
    return {"status": "success", "message": f"Branch '{ifsc.upper()}' deleted."}
