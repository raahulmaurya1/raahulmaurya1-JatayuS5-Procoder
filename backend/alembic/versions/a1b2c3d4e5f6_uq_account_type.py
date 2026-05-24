"""Add multiple account types constraints

Revision ID: a1b2c3d4e5f6
Revises: 5d971737cc6a
Create Date: 2026-03-29 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5d971737cc6a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The unique constraints uq_phone_account_type / uq_email_account_type
    # are already defined in the initial migration's CREATE TABLE.
    # Only do index cleanup here — non-unique indexes on phone/email already exist.

    # Drop existing non-unique indexes (created in initial migration as non-unique)
    # and re-create them without uniqueness to be idempotent.
    op.execute("DROP INDEX IF EXISTS ix_user_initial_phone")
    op.execute("DROP INDEX IF EXISTS ix_user_initial_email")
    op.create_index('ix_user_initial_phone', 'user_initial', ['phone'], unique=False)
    op.create_index('ix_user_initial_email', 'user_initial', ['email'], unique=False)

    # Create unique constraints only if they don't already exist
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_phone_account_type'
            ) THEN
                ALTER TABLE user_initial ADD CONSTRAINT uq_phone_account_type UNIQUE (phone, account_type);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_email_account_type'
            ) THEN
                ALTER TABLE user_initial ADD CONSTRAINT uq_email_account_type UNIQUE (email, account_type);
            END IF;
        END
        $$;
    """)



def downgrade() -> None:
    op.drop_constraint('uq_phone_account_type', 'user_initial', type_='unique')
    op.drop_constraint('uq_email_account_type', 'user_initial', type_='unique')

    op.drop_index('ix_user_initial_phone', table_name='user_initial')
    op.drop_index('ix_user_initial_email', table_name='user_initial')

    op.create_index('ix_user_initial_phone', 'user_initial', ['phone'], unique=True)
    op.create_index('ix_user_initial_email', 'user_initial', ['email'], unique=True)
