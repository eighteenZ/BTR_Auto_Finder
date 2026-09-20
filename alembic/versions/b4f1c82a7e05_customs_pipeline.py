"""customs pipeline: import records, sync runs, watchlist, lead procurement columns

Revision ID: b4f1c82a7e05
Revises: 9d5e2f04a8b1
Create Date: 2026-09-20 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'b4f1c82a7e05'
down_revision: Union[str, None] = '9d5e2f04a8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('procurement_status', sa.String(), server_default=sa.text("'unknown'"), nullable=True))
    op.add_column('leads', sa.Column('last_import_at', sa.String(), server_default=sa.text("''"), nullable=True))
    op.add_column('leads', sa.Column('import_count_90d', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.add_column('leads', sa.Column('customs_last_checked_at', sa.String(), server_default=sa.text("''"), nullable=True))

    op.create_table('customs_import_records',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('lead_id', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('lead_key', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('company_name', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('domain', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('consignee_name', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('supplier_name', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('country', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('hs_code', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('product_description', sa.Text(), server_default=sa.text("''"), nullable=True),
    sa.Column('arrival_date', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('quantity', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('weight', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('source', sa.String(), nullable=False, server_default=sa.text("'importyeti_csv'")),
    sa.Column('source_ref', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('record_hash', sa.String(), nullable=False),
    sa.Column('raw', JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column('created_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('record_hash', name='uq_customs_record_hash')
    )
    op.create_index('idx_customs_records_domain_date', 'customs_import_records', ['domain', 'arrival_date'], unique=False)
    op.create_index('idx_customs_records_lead', 'customs_import_records', ['lead_id'], unique=False)
    op.create_index('idx_customs_records_company', 'customs_import_records', ['company_name'], unique=False)

    op.create_table('customs_sync_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('run_date', sa.String(), nullable=False),
    sa.Column('trigger', sa.String(), nullable=False, server_default=sa.text("'scheduled'")),
    sa.Column('status', sa.String(), nullable=False, server_default=sa.text("'running'")),
    sa.Column('files_processed', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('records_ingested', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('leads_checked', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('leads_active', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('new_leads', sa.Integer(), nullable=False, server_default=sa.text('0')),
    sa.Column('stats', JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    sa.Column('error', sa.Text(), server_default=sa.text("''"), nullable=True),
    sa.Column('started_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('finished_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_customs_runs_date', 'customs_sync_runs', ['run_date', 'status'], unique=False)

    op.create_table('customs_watchlist',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('hs_code', sa.String(), nullable=False),
    sa.Column('product_keywords', JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column('countries', JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    sa.Column('note', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('enabled', sa.Integer(), nullable=False, server_default=sa.text('1')),
    sa.Column('created_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('updated_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hs_code', name='uq_customs_watch_hs')
    )


def downgrade() -> None:
    op.drop_table('customs_watchlist')
    op.drop_index('idx_customs_runs_date', table_name='customs_sync_runs')
    op.drop_table('customs_sync_runs')
    op.drop_index('idx_customs_records_company', table_name='customs_import_records')
    op.drop_index('idx_customs_records_lead', table_name='customs_import_records')
    op.drop_index('idx_customs_records_domain_date', table_name='customs_import_records')
    op.drop_table('customs_import_records')
    op.drop_column('leads', 'customs_last_checked_at')
    op.drop_column('leads', 'import_count_90d')
    op.drop_column('leads', 'last_import_at')
    op.drop_column('leads', 'procurement_status')
