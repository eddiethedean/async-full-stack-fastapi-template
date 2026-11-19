"""Add shared authentication rate limit buckets."""

import sqlalchemy as sa
from alembic import op

revision = "1c89be4a2070"
down_revision = "e3bb8c28d410"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_bucket",
        sa.Column("action", sa.String(16), primary_key=True),
        sa.Column("kind", sa.String(8), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("window", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_rate_limit_bucket_expires_at", "rate_limit_bucket", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("rate_limit_bucket")
