"""Add Pref_branch + account_number to additional_info; seed digital default branch

Revision ID: f1a2b3c4d5e6
Revises: 5d971737cc6a
Create Date: 2026-05-21 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '5d971737cc6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    1. Add Pref_branch + account_number columns to additional_info
       (both nullable — existing rows keep NULL values).
    2. Seed the digital-only default branch row into bank_branch
       (HDFC0001234 already exists; we insert CDBB0008863 only).
    """

    # ── 1. additional_info columns ────────────────────────────────────────────
    op.add_column(
        'additional_info',
        sa.Column('pref_branch', sa.String(), nullable=True)
    )
    op.add_column(
        'additional_info',
        sa.Column('account_number', sa.String(), nullable=True)
    )

    # ── 2. Seed digital-only default branch ───────────────────────────────────
    op.execute("""
        INSERT INTO bank_branch (
            ifsc,
            branch_name,
            branch_address,
            supported_account_type,
            manager_name,
            manager_email,
            manager_phone,
            relationship_officer
        ) VALUES (
            'CDBB0008863',
            'Digital Banking Centre',
            '7th Floor, Cyber Tower, Hitech City, Hyderabad 500081',
            'Digital',
            'Anil Mehta',
            'anil.mehta@bank.in',
            '+919800000001',
            'Pooja Rao'
        )
        ON CONFLICT (ifsc) DO NOTHING;
    """)


def downgrade() -> None:
    """Remove the two new columns (seed data is intentionally left in place)."""
    op.drop_column('additional_info', 'account_number')
    op.drop_column('additional_info', 'pref_branch')
