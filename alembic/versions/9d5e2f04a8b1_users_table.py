"""users table for corporate-email login

Revision ID: 9d5e2f04a8b1
Revises: 7b4c9d81e2f3
Create Date: 2026-09-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9d5e2f04a8b1'
down_revision: Union[str, None] = '7b4c9d81e2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('email', sa.String(), nullable=False),
    sa.Column('role', sa.String(), server_default=sa.text("'member'"), nullable=False),
    sa.Column('api_key', sa.String(), server_default=sa.text("''"), nullable=False),
    sa.Column('auth_provider', sa.String(), server_default=sa.text("'imap'"), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('last_login_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email', name='uq_users_email'),
    sa.UniqueConstraint('api_key', name='uq_users_api_key')
    )
    op.create_index('idx_users_role', 'users', ['role'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_users_role', table_name='users')
    op.drop_table('users')
