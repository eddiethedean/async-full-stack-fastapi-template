"""Cancel queued password recovery when an account email changes."""

from alembic import op

revision = "2f7a14c018c2"
down_revision = "6424bd25bf26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION cancel_recovery_on_account_email_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE email_outbox
            SET failed_at = now(), last_error = 'account_email_changed',
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
        CREATE TRIGGER cancel_recovery_on_account_email_change
        AFTER UPDATE OF email ON "user"
        FOR EACH ROW WHEN (OLD.email IS DISTINCT FROM NEW.email)
        EXECUTE FUNCTION cancel_recovery_on_account_email_change()
        """
    )


def downgrade() -> None:
    op.execute('DROP TRIGGER cancel_recovery_on_account_email_change ON "user"')
    op.execute("DROP FUNCTION cancel_recovery_on_account_email_change()")
