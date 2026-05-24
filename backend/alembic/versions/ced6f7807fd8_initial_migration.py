"""Initial migration

Revision ID: ced6f7807fd8
Revises: 
Create Date: 2026-03-01 03:51:11.151820

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ced6f7807fd8'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial schema from scratch (was previously created via create_all on Docker)."""

    # Enable pgvector extension (required for agent_context.embedding column)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # -- user_initial --
    op.create_table(
        'user_initial',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('phone', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='draft'),
        sa.Column('verified_data', sa.JSON(), nullable=True),
        sa.Column('account_type', sa.String(), nullable=True),
        sa.Column('face_verified', sa.Boolean(), nullable=True, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone', 'account_type', name='uq_phone_account_type'),
        sa.UniqueConstraint('email', 'account_type', name='uq_email_account_type'),
    )
    op.create_index('ix_user_initial_id', 'user_initial', ['id'], unique=False)
    op.create_index('ix_user_initial_phone', 'user_initial', ['phone'], unique=False)
    op.create_index('ix_user_initial_email', 'user_initial', ['email'], unique=False)

    # -- additional_info --
    op.create_table(
        'additional_info',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('session_ulid', sa.String(), sa.ForeignKey('user_initial.id'), nullable=True, unique=True),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_additional_info_id', 'additional_info', ['id'], unique=False)

    # -- sessions --
    op.create_table(
        'sessions',
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('user_initial.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('expires_at', sa.DateTime(timezone=True),
                  server_default=sa.text("NOW() + INTERVAL '30 minutes'"), nullable=True),
        sa.PrimaryKeyConstraint('session_id'),
    )
    op.create_index('ix_sessions_session_id', 'sessions', ['session_id'], unique=False)
    op.create_index('ix_sessions_user_id', 'sessions', ['user_id'], unique=False)

    # -- user_documents --
    op.create_table(
        'user_documents',
        sa.Column('document_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(),
                  sa.ForeignKey('sessions.session_id', ondelete='CASCADE'), nullable=False),
        sa.Column('file_type', sa.String(), nullable=False),
        sa.Column('file_url', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=True, server_default='PENDING'),
        sa.Column('extracted_data', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('document_id'),
    )
    op.create_index('ix_user_documents_document_id', 'user_documents', ['document_id'], unique=False)
    op.create_index('ix_user_documents_session_id', 'user_documents', ['session_id'], unique=False)

    # -- agent_context (pgvector) --
    op.create_table(
        'agent_context',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('session_id', sa.String(), sa.ForeignKey('user_initial.id'), nullable=False),
        sa.Column('summary', sa.String(), nullable=False),
        sa.Column('embedding', sa.Text(), nullable=True),  # raw placeholder; vector cast applied below
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    # Re-create the embedding column with the proper vector type
    op.execute("ALTER TABLE agent_context DROP COLUMN embedding")
    op.execute("ALTER TABLE agent_context ADD COLUMN embedding vector(768)")
    op.create_index('ix_agent_context_id', 'agent_context', ['id'], unique=False)
    op.create_index('ix_agent_context_session_id', 'agent_context', ['session_id'], unique=False)

    # -- temporary_extraction (existed in old Docker DB, dropped in next migration) --
    op.create_table(
        'temporary_extraction',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('user_initial.id'), nullable=False),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('extracted_data', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_temporary_extraction_id', 'temporary_extraction', ['id'], unique=False)
    op.create_index('ix_temporary_extraction_user_id', 'temporary_extraction', ['user_id'], unique=False)


def downgrade() -> None:
    """Drop all tables created in the initial migration."""
    op.drop_table('temporary_extraction')
    op.drop_table('agent_context')
    op.drop_table('user_documents')
    op.drop_table('sessions')
    op.drop_table('additional_info')
    op.drop_table('user_initial')

