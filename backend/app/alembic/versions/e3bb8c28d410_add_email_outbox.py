"""Add the email outbox and pending invitations."""

import sqlalchemy as sa
from alembic import op

revision = "e3bb8c28d410"
down_revision = "b4d2c9613f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("invitation_pending", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        "ck_user_pending_invitation_inactive", "user",
        "NOT invitation_pending OR NOT is_active",
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("deduplication_key", sa.String(128), nullable=False, unique=True),
        sa.Column("payload", sa.LargeBinary()),
        sa.Column("key_version", sa.String(32)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(32)),
        sa.CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts"),
    )
    op.create_index(
        "ix_email_outbox_pending",
        "email_outbox",
        ["available_at"],
        postgresql_where=sa.text("delivered_at IS NULL AND failed_at IS NULL"),
    )


def downgrade() -> None:
    # Pending accounts have no usable password, so they remain inactive.
    op.drop_table("email_outbox")
    op.drop_constraint("ck_user_pending_invitation_inactive", "user", type_="check")
    op.drop_column("user", "invitation_pending")
