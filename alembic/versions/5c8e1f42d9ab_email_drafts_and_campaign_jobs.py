"""email_drafts and campaign_jobs cross-domain contract tables

Revision ID: 5c8e1f42d9ab
Revises: 72b3332a736a
Create Date: 2026-09-15 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5c8e1f42d9ab'
down_revision: Union[str, None] = '72b3332a736a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('email_drafts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('hunt_id', sa.String(), nullable=False),
    sa.Column('sequence_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('lead_id', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('lead_key', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('company_name', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('website', sa.Text(), server_default=sa.text("''"), nullable=True),
    sa.Column('locale', sa.String(), server_default=sa.text("'en'"), nullable=False),
    sa.Column('target', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('targets', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('emails', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('language_choice', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('strategy_brief', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('validation_summary', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('review_summary', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('review_status', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('generation_mode', sa.String(), server_default=sa.text("'personalized'"), nullable=False),
    sa.Column('template_id', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('template_group', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('template_usage_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('template_max_send_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('template_seed_source', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('status', sa.String(), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('manual_review', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('error', sa.Text(), server_default=sa.text("''"), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('updated_at', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('hunt_id', 'sequence_index', name='uq_email_draft_hunt_index')
    )
    op.create_index('idx_email_drafts_status', 'email_drafts', ['status', 'created_at'], unique=False)
    op.create_index('idx_email_drafts_lead_key', 'email_drafts', ['lead_key'], unique=False)

    op.create_table('campaign_jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('hunt_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('payload_json', sa.Text(), nullable=False),
    sa.Column('campaign_id', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('created_at', sa.String(), nullable=False),
    sa.Column('updated_at', sa.String(), nullable=False),
    sa.Column('claimed_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('finished_at', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('claimed_by', sa.String(), server_default=sa.text("''"), nullable=True),
    sa.Column('attempt_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_error', sa.Text(), server_default=sa.text("''"), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_campaign_jobs_status', 'campaign_jobs', ['status', 'created_at'], unique=False)
    op.create_index('idx_campaign_jobs_hunt', 'campaign_jobs', ['hunt_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_campaign_jobs_hunt', table_name='campaign_jobs')
    op.drop_index('idx_campaign_jobs_status', table_name='campaign_jobs')
    op.drop_table('campaign_jobs')
    op.drop_index('idx_email_drafts_lead_key', table_name='email_drafts')
    op.drop_index('idx_email_drafts_status', table_name='email_drafts')
    op.drop_table('email_drafts')
