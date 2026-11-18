"""Index item pages by owner.

Revision ID: 42e78dd96a11
Revises: fe56fa70289e
"""

import sqlalchemy as sa
from alembic import op

revision = "42e78dd96a11"
down_revision = "fe56fa70289e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_item_owner_created_at_id",
        "item",
        ["owner_id", sa.text("created_at DESC NULLS LAST"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_item_owner_created_at_id", table_name="item")
