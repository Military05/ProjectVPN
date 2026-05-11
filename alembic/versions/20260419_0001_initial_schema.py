"""initial schema

Revision ID: 20260419_0001
Revises: None
Create Date: 2026-04-19 00:00:00
"""
from __future__ import annotations

from alembic import op

from shop_bot.infrastructure.db.metadata_v1 import metadata

revision = "20260419_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    metadata.drop_all(bind=bind)
