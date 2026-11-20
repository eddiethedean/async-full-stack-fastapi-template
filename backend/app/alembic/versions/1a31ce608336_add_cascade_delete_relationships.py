"""Add cascade delete relationships

Revision ID: 1a31ce608336
Revises: d98dd8ec85a3
Create Date: 2024-07-31 22:24:34.447891

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = '1a31ce608336'
down_revision = 'd98dd8ec85a3'
branch_labels = None
depends_on = None


def upgrade():
    # Older databases at the UUID revision may still allow null owners.
    # Hold the lock through the constraint change so no writer can add one.
    op.execute("LOCK TABLE item IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$
        DECLARE
            ownerless_item uuid;
        BEGIN
            SELECT id INTO ownerless_item
            FROM item
            WHERE owner_id IS NULL
            ORDER BY id
            LIMIT 1;

            IF FOUND THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'Revision 1a31ce608336 cannot restore required ownership. Item '
                        || ownerless_item || ' has no owner.',
                    HINT = 'Assign a verified owner before retrying. See deployment.md under Item ownership during UUID migrations.';
            END IF;
        END
        $$;
    """)
    op.alter_column('item', 'owner_id', existing_type=sa.UUID(), nullable=False)
    op.drop_constraint('item_owner_id_fkey', 'item', type_='foreignkey')
    op.create_foreign_key(None, 'item', 'user', ['owner_id'], ['id'], ondelete='CASCADE')


def downgrade():
    op.drop_constraint('item_owner_id_fkey', 'item', type_='foreignkey')
    op.create_foreign_key('item_owner_id_fkey', 'item', 'user', ['owner_id'], ['id'])
