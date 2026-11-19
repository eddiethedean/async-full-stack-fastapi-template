"""Add max length for string(varchar) fields in User and Items models

Revision ID: 9c0a54914c78
Revises: e2412789c190
Create Date: 2024-06-17 14:42:44.639457

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


# revision identifiers, used by Alembic.
revision = '9c0a54914c78'
down_revision = 'e2412789c190'
branch_labels = None
depends_on = None


def upgrade():
    # Hold the same locks as the ALTERs so writes cannot invalidate the preflight.
    op.execute('LOCK TABLE "user", item IN ACCESS EXCLUSIVE MODE')
    op.execute("""
        DO $$
        DECLARE
            oversized record;
        BEGIN
            SELECT * INTO oversized
            FROM (
                SELECT 'user' AS table_name, u.id, fields.*
                FROM "user" AS u
                CROSS JOIN LATERAL (VALUES
                    ('email', char_length(u.email)),
                    ('full_name', char_length(u.full_name))
                ) AS fields(column_name, characters)
                UNION ALL
                SELECT 'item', i.id, fields.*
                FROM item AS i
                CROSS JOIN LATERAL (VALUES
                    ('title', char_length(i.title)),
                    ('description', char_length(i.description))
                ) AS fields(column_name, characters)
            ) AS candidates
            WHERE characters > 255
            LIMIT 1;

            IF FOUND THEN
                -- Offline SQL generation escapes percent-format placeholders.
                RAISE EXCEPTION USING
                    MESSAGE = 'Revision 9c0a54914c78 cannot narrow '
                        || oversized.table_name || '.' || oversized.column_name
                        || ' for row ID ' || oversized.id || ' with '
                        || oversized.characters || ' characters (limit 255).',
                    HINT = 'Preserve the original data and review oversized values before retrying. See deployment.md under Historical text migration.';
            END IF;
        END
        $$;
    """)

    # Adjust the length of the email field in the User table
    op.alter_column('user', 'email',
               existing_type=sa.String(),
               type_=sa.String(length=255),
               existing_nullable=False)

    # Adjust the length of the full_name field in the User table
    op.alter_column('user', 'full_name',
               existing_type=sa.String(),
               type_=sa.String(length=255),
               existing_nullable=True)

    # Adjust the length of the title field in the Item table
    op.alter_column('item', 'title',
               existing_type=sa.String(),
               type_=sa.String(length=255),
               existing_nullable=False)

    # Adjust the length of the description field in the Item table
    op.alter_column('item', 'description',
               existing_type=sa.String(),
               type_=sa.String(length=255),
               existing_nullable=True)


def downgrade():
    # Revert the length of the email field in the User table
    op.alter_column('user', 'email',
               existing_type=sa.String(length=255),
               type_=sa.String(),
               existing_nullable=False)

    # Revert the length of the full_name field in the User table
    op.alter_column('user', 'full_name',
               existing_type=sa.String(length=255),
               type_=sa.String(),
               existing_nullable=True)

    # Revert the length of the title field in the Item table
    op.alter_column('item', 'title',
               existing_type=sa.String(length=255),
               type_=sa.String(),
               existing_nullable=False)

    # Revert the length of the description field in the Item table
    op.alter_column('item', 'description',
               existing_type=sa.String(length=255),
               type_=sa.String(),
               existing_nullable=True)
