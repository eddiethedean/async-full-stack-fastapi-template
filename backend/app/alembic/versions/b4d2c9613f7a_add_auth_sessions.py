"""Store auth sessions and single-use account links."""

import sqlalchemy as sa
from alembic import op

revision = "b4d2c9613f7a"
down_revision = "42e78dd96a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("auth_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "authsession",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("cookie_digest", sa.String(64), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_authsession_user_id", "authsession", ["user_id"])
    op.create_index("ix_authsession_expires_at", "authsession", ["expires_at"])
    op.create_table(
        "accountactiontoken",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False, unique=True),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_accountactiontoken_user_id", "accountactiontoken", ["user_id"])
    op.create_index(
        "ix_accountactiontoken_expires_at", "accountactiontoken", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("accountactiontoken")
    op.drop_table("authsession")
    op.drop_column("user", "auth_version")
