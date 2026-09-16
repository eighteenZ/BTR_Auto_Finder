"""email_drafts.edited_by_review for human-edited outreach content

Revision ID: 7b4c9d81e2f3
Revises: 8a2f90c31d5d
Create Date: 2026-09-16 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7b4c9d81e2f3'
down_revision: Union[str, None] = '8a2f90c31d5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('email_drafts', sa.Column('edited_by_review', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def downgrade() -> None:
    op.drop_column('email_drafts', 'edited_by_review')
