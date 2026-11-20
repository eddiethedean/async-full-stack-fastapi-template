"""Create the initial PostgreSQL schema for a fresh project."""

import sqlalchemy as sa
from alembic import op

revision = "a7f8e3621c04"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_bucket",
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("window", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("action", "kind", "key_hash", "window"),
    )
    op.create_index(
        op.f("ix_rate_limit_bucket_expires_at"),
        "rate_limit_bucket",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "user",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column(
            "invitation_pending",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "auth_version", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "NOT invitation_pending OR NOT is_active",
            name="ck_user_pending_invitation_inactive",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_user_email"), "user", ["email"], unique=True)
    op.create_table(
        "accountactiontoken",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("digest"),
    )
    op.create_index(
        op.f("ix_accountactiontoken_expires_at"),
        "accountactiontoken",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_accountactiontoken_user_id"),
        "accountactiontoken",
        ["user_id"],
        unique=False,
    )
    op.create_table(
        "authsession",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("cookie_digest", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cookie_digest"),
    )
    op.create_index(
        op.f("ix_authsession_expires_at"), "authsession", ["expires_at"], unique=False
    )
    op.create_index(
        op.f("ix_authsession_user_id"), "authsession", ["user_id"], unique=False
    )
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("recipient", sa.String(length=255), nullable=False),
        sa.Column(
            "deduplication_key",
            sa.String(length=128),
            nullable=False,
        ),
        sa.Column("payload", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.String(length=32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=32), nullable=True),
        sa.CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_email_outbox_user_id_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_index(
        "ix_email_outbox_pending",
        "email_outbox",
        ["available_at"],
        unique=False,
        postgresql_where=sa.text("delivered_at IS NULL AND failed_at IS NULL"),
    )
    op.create_index(
        op.f("ix_email_outbox_user_id"), "email_outbox", ["user_id"], unique=False
    )
    op.create_table(
        "item",
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_item_owner_created_at_id",
        "item",
        [
            "owner_id",
            sa.text("created_at DESC NULLS LAST"),
            sa.text("id DESC"),
        ],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION cancel_deleted_account_email() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE email_outbox
            SET failed_at = now(), last_error = 'account_deleted',
                payload = NULL, key_version = NULL,
                lease_id = NULL, lease_expires_at = NULL
            WHERE delivered_at IS NULL AND failed_at IS NULL
              AND user_id = OLD.id;
            RETURN OLD;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER cancel_deleted_account_email
        BEFORE DELETE ON "user"
        FOR EACH ROW EXECUTE FUNCTION cancel_deleted_account_email()
        """
    )

    op.execute(
        """
        CREATE FUNCTION cancel_invalidated_account_recovery() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE email_outbox
            SET failed_at = now(),
                last_error = CASE WHEN OLD.email IS DISTINCT FROM NEW.email
                    THEN 'account_email_changed' ELSE 'account_credentials_changed' END,
                payload = NULL, key_version = NULL,
                lease_id = NULL, lease_expires_at = NULL
            WHERE kind = 'password_reset'
              AND delivered_at IS NULL AND failed_at IS NULL
              AND user_id = NEW.id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER cancel_invalidated_account_recovery
        AFTER UPDATE OF email, auth_version ON "user"
        FOR EACH ROW WHEN (
            OLD.email IS DISTINCT FROM NEW.email
            OR OLD.auth_version IS DISTINCT FROM NEW.auth_version
        )
        EXECUTE FUNCTION cancel_invalidated_account_recovery()
        """
    )


def downgrade() -> None:
    op.execute('DROP TRIGGER cancel_invalidated_account_recovery ON "user"')
    op.execute("DROP FUNCTION cancel_invalidated_account_recovery()")
    op.execute('DROP TRIGGER cancel_deleted_account_email ON "user"')
    op.execute("DROP FUNCTION cancel_deleted_account_email()")

    op.drop_table("item")
    op.drop_table("email_outbox")
    op.drop_table("authsession")
    op.drop_table("accountactiontoken")
    op.drop_table("user")
    op.drop_table("rate_limit_bucket")
