"""Associate account mail with its owner and cancel it on deletion."""

import sqlalchemy as sa
from alembic import op

revision = "6424bd25bf26"
down_revision = "1c89be4a2070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_outbox", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE email_outbox AS job
        SET user_id = account.id
        FROM "user" AS account
        WHERE job.kind IN ('invite', 'welcome')
          AND job.deduplication_key = job.kind || ':' || account.id::text
        """
    )
    op.execute(
        """
        UPDATE email_outbox AS job
        SET user_id = action.user_id
        FROM accountactiontoken AS action
        WHERE job.kind = 'password_reset'
          AND action.purpose = 'password_reset'
          AND job.deduplication_key = 'reset:' || action.digest
        """
    )
    # A legacy job with no surviving account or reset action has no valid target.
    op.execute(
        """
        UPDATE email_outbox
        SET failed_at = now(), last_error = 'account_unavailable',
            payload = NULL, key_version = NULL,
            lease_id = NULL, lease_expires_at = NULL
        WHERE user_id IS NULL AND delivered_at IS NULL AND failed_at IS NULL
          AND kind IN ('invite', 'welcome', 'password_reset')
        """
    )
    op.create_foreign_key(
        "fk_email_outbox_user_id_user",
        "email_outbox", "user", ["user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_email_outbox_user_id", "email_outbox", ["user_id"])
    op.execute(
        """
        CREATE FUNCTION cancel_deleted_account_email() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE email_outbox
            SET failed_at = now(), last_error = 'account_deleted',
                payload = NULL, key_version = NULL,
                lease_id = NULL, lease_expires_at = NULL
            WHERE user_id = OLD.id AND delivered_at IS NULL AND failed_at IS NULL;
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


def downgrade() -> None:
    op.execute('DROP TRIGGER cancel_deleted_account_email ON "user"')
    op.execute("DROP FUNCTION cancel_deleted_account_email()")
    op.drop_index("ix_email_outbox_user_id", table_name="email_outbox")
    op.drop_constraint(
        "fk_email_outbox_user_id_user", "email_outbox", type_="foreignkey"
    )
    op.drop_column("email_outbox", "user_id")
