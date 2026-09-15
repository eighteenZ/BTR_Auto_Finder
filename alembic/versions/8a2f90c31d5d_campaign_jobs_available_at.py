"""campaign_jobs.available_at for deferred re-claim

Revision ID: 8a2f90c31d5d
Revises: 5c8e1f42d9ab
Create Date: 2026-09-15 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8a2f90c31d5d'
down_revision: Union[str, None] = '5c8e1f42d9ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('campaign_jobs', sa.Column('available_at', sa.String(), server_default=sa.text("''"), nullable=False))


def downgrade() -> None:
    op.drop_column('campaign_jobs', 'available_at')
