from sqlalchemy import Column, String, DateTime
from sqlalchemy.sql import func
from app.db.base import Base


class BankBranch(Base):
    """
    Mirrors the existing `bank_branch` table in the database.

    supported_account_type is a comma-separated string, e.g.:
        "Retail, SME, Digital"
        "Retail, SME"
        "Digital"

    Filter logic uses case-insensitive substring matching on this field.
    """
    __tablename__ = "bank_branch"

    ifsc                   = Column(String, primary_key=True, index=True)
    branch_name            = Column(String, nullable=False)
    branch_address         = Column(String, nullable=False)
    supported_account_type = Column(String, nullable=False)   # e.g. "Retail, SME, Digital"
    manager_name           = Column(String, nullable=True)
    manager_email          = Column(String, nullable=True)
    manager_phone          = Column(String, nullable=True)
    relationship_officer   = Column(String, nullable=True)
