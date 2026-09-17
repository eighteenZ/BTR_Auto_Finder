"""SQLAlchemy 2.0 declarative models — the schema source of truth for Alembic.

Tables mirror the former SQLite stores and add the lead repository used for
cross-hunt deduplication and reuse.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ── Hunts ────────────────────────────────────────────────────────────────────

class Hunt(Base):
    __tablename__ = "hunts"

    id = Column(String, primary_key=True)
    status = Column(String, nullable=False, server_default=text("'pending'"))
    current_stage = Column(String, server_default=text("''"))
    hunt_round = Column(Integer, nullable=False, server_default=text("0"))
    leads_count = Column(Integer, nullable=False, server_default=text("0"))
    email_sequences_count = Column(Integer, nullable=False, server_default=text("0"))
    error = Column(Text, server_default=text("''"))
    website_url = Column(Text, server_default=text("''"))
    data = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(String, server_default=text("''"))
    updated_at = Column(String, server_default=text("''"))

    __table_args__ = (Index("idx_hunts_status", "status", "created_at"),)


# ── Lead repository (global dedup / reuse) ───────────────────────────────────

class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True)
    lead_key = Column(String, nullable=False, unique=True)
    domain = Column(String, nullable=False, server_default=text("''"))
    company_name = Column(String, server_default=text("''"))
    website = Column(Text, server_default=text("''"))
    industry = Column(String, server_default=text("''"))
    country_code = Column(String, server_default=text("''"))
    address = Column(Text, server_default=text("''"))
    emails = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    phone_numbers = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    social_media = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    decision_makers = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    customs_data = Column(Text, server_default=text("''"))
    evidence = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    match_score = Column(Float, nullable=False, server_default=text("0"))
    fit_score = Column(Float, nullable=False, server_default=text("0"))
    contactability_score = Column(Float, nullable=False, server_default=text("0"))
    priority_tier = Column(String, server_default=text("''"))
    first_seen_at = Column(String, server_default=text("''"))
    last_seen_at = Column(String, server_default=text("''"))
    seen_count = Column(Integer, nullable=False, server_default=text("1"))
    first_hunt_id = Column(String, server_default=text("''"))
    last_hunt_id = Column(String, server_default=text("''"))
    raw = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        Index("idx_leads_domain", "domain"),
        Index("idx_leads_company_name", "company_name"),
        Index("idx_leads_last_seen", "last_seen_at"),
    )


class LeadSighting(Base):
    __tablename__ = "lead_sightings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    hunt_id = Column(String, nullable=False)
    source_keyword = Column(String, server_default=text("''"))
    seen_at = Column(String, server_default=text("''"))

    __table_args__ = (
        UniqueConstraint("lead_id", "hunt_id", "source_keyword", name="uq_lead_sighting"),
        Index("idx_lead_sightings_lead", "lead_id"),
        Index("idx_lead_sightings_hunt", "hunt_id"),
    )


class HuntLead(Base):
    __tablename__ = "hunt_leads"

    hunt_id = Column(String, primary_key=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), primary_key=True)
    reused = Column(Integer, nullable=False, server_default=text("0"))
    added_at = Column(String, server_default=text("''"))

    __table_args__ = (Index("idx_hunt_leads_hunt", "hunt_id"),)


# ── Automation job queue ─────────────────────────────────────────────────────

class HuntJob(Base):
    __tablename__ = "hunt_jobs"

    id = Column(String, primary_key=True)
    payload_json = Column(Text, nullable=False)
    status = Column(String, nullable=False, server_default=text("'queued'"))
    available_at = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    started_at = Column(String, server_default=text("''"))
    finished_at = Column(String, server_default=text("''"))
    claimed_by = Column(String, server_default=text("''"))
    attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    last_error = Column(Text, server_default=text("''"))
    last_hunt_id = Column(String, server_default=text("''"))
    progress_stage = Column(String, server_default=text("''"))
    progress_message = Column(Text, server_default=text("''"))
    template_seed_status = Column(String, server_default=text("''"))
    template_seed_source = Column(String, server_default=text("''"))

    __table_args__ = (
        Index("idx_hunt_jobs_status_available", "status", "available_at", "created_at"),
    )


# ── Email automation ─────────────────────────────────────────────────────────

class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(String, primary_key=True)
    provider_type = Column(String, nullable=False)
    from_name = Column(String, nullable=False)
    from_email = Column(String, nullable=False)
    reply_to = Column(String, server_default=text("''"))
    smtp_host = Column(String, server_default=text("''"))
    smtp_port = Column(Integer, server_default=text("587"))
    smtp_username = Column(String, server_default=text("''"))
    smtp_secret_encrypted = Column(Text, server_default=text("''"))
    imap_host = Column(String, server_default=text("''"))
    imap_port = Column(Integer, server_default=text("993"))
    imap_username = Column(String, server_default=text("''"))
    imap_secret_encrypted = Column(Text, server_default=text("''"))
    use_tls = Column(Integer, nullable=False, server_default=text("1"))
    status = Column(String, nullable=False, server_default=text("'active'"))
    daily_send_limit = Column(Integer, nullable=False, server_default=text("50"))
    hourly_send_limit = Column(Integer, nullable=False, server_default=text("10"))
    last_test_at = Column(String, server_default=text("''"))
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)


class EmailCampaign(Base):
    __tablename__ = "email_campaigns"

    id = Column(String, primary_key=True)
    hunt_id = Column(String, nullable=False)
    email_account_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, server_default=text("'draft'"))
    language_mode = Column(String, nullable=False, server_default=text("'auto_by_region'"))
    default_language = Column(String, nullable=False, server_default=text("'en'"))
    fallback_language = Column(String, nullable=False, server_default=text("'en'"))
    tone = Column(String, nullable=False, server_default=text("'professional'"))
    step1_delay_days = Column(Integer, nullable=False, server_default=text("0"))
    step2_delay_days = Column(Integer, nullable=False, server_default=text("3"))
    step3_delay_days = Column(Integer, nullable=False, server_default=text("3"))
    min_fit_score = Column(Float, nullable=False, server_default=text("0.6"))
    min_contactability_score = Column(Float, nullable=False, server_default=text("0.45"))
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (Index("idx_email_campaigns_hunt", "hunt_id"),)


class LeadEmailSequence(Base):
    __tablename__ = "lead_email_sequences"

    id = Column(String, primary_key=True)
    campaign_id = Column(String, nullable=False)
    hunt_id = Column(String, nullable=False)
    lead_key = Column(String, nullable=False)
    lead_email = Column(String, nullable=False)
    lead_name = Column(String, server_default=text("''"))
    decision_maker_name = Column(String, server_default=text("''"))
    decision_maker_title = Column(String, server_default=text("''"))
    locale = Column(String, nullable=False, server_default=text("'en'"))
    generation_mode = Column(String, nullable=False, server_default=text("'personalized'"))
    template_id = Column(String, server_default=text("''"))
    template_group = Column(String, server_default=text("''"))
    template_usage_index = Column(Integer, nullable=False, server_default=text("0"))
    template_max_send_count = Column(Integer, nullable=False, server_default=text("0"))
    status = Column(String, nullable=False, server_default=text("'draft'"))
    current_step = Column(Integer, nullable=False, server_default=text("0"))
    stop_reason = Column(String, server_default=text("''"))
    replied_at = Column(String, server_default=text("''"))
    last_sent_at = Column(String, server_default=text("''"))
    next_scheduled_at = Column(String, server_default=text("''"))
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_key", name="uq_sequence_campaign_lead"),
        Index("idx_sequences_lead_key", "lead_key"),
        Index("idx_sequences_campaign", "campaign_id"),
    )


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id = Column(String, primary_key=True)
    sequence_id = Column(String, nullable=False)
    step_number = Column(Integer, nullable=False)
    goal = Column(String, nullable=False)
    locale = Column(String, nullable=False)
    subject = Column(Text, nullable=False)
    body_text = Column(Text, nullable=False)
    status = Column(String, nullable=False, server_default=text("'pending'"))
    scheduled_at = Column(String, nullable=False)
    sent_at = Column(String, server_default=text("''"))
    provider_message_id = Column(String, server_default=text("''"))
    thread_key = Column(String, server_default=text("''"))
    failure_reason = Column(Text, server_default=text("''"))
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("sequence_id", "step_number", name="uq_email_message_sequence_step"),
        Index("idx_email_message_status_schedule", "status", "scheduled_at"),
        Index("idx_email_messages_sequence", "sequence_id"),
    )


class EmailReplyEvent(Base):
    __tablename__ = "email_reply_events"

    id = Column(String, primary_key=True)
    sequence_id = Column(String, nullable=False)
    message_id = Column(String, server_default=text("''"))
    from_email = Column(String, nullable=False)
    subject = Column(Text, server_default=text("''"))
    snippet = Column(Text, server_default=text("''"))
    received_at = Column(String, nullable=False)
    raw_ref = Column(String, server_default=text("''"))
    created_at = Column(String, nullable=False)

    __table_args__ = (Index("idx_reply_sequence_id", "sequence_id"),)


# ── Cross-domain contracts (hunter → marketing handoff) ──────────────────────


class EmailDraft(Base):
    """Generated outreach sequence handed from the hunter pipeline to the
    marketing service. Written by the hunter domain; approval and campaign
    creation are owned by the marketing service."""

    __tablename__ = "email_drafts"

    id = Column(String, primary_key=True)
    hunt_id = Column(String, nullable=False)
    sequence_index = Column(Integer, nullable=False, server_default=text("0"))
    lead_id = Column(String, server_default=text("''"))
    lead_key = Column(String, server_default=text("''"))
    company_name = Column(String, server_default=text("''"))
    website = Column(Text, server_default=text("''"))
    locale = Column(String, nullable=False, server_default=text("'en'"))
    target = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    targets = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    emails = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    language_choice = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    strategy_brief = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    validation_summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    review_summary = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    review_status = Column(String, server_default=text("''"))
    generation_mode = Column(String, nullable=False, server_default=text("'personalized'"))
    template_id = Column(String, server_default=text("''"))
    template_group = Column(String, server_default=text("''"))
    template_usage_index = Column(Integer, nullable=False, server_default=text("0"))
    template_max_send_count = Column(Integer, nullable=False, server_default=text("0"))
    template_seed_source = Column(String, server_default=text("''"))
    status = Column(String, nullable=False, server_default=text("'draft'"))
    edited_by_review = Column(Boolean, nullable=False, server_default=text("false"))
    manual_review = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    error = Column(Text, server_default=text("''"))
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    __table_args__ = (
        UniqueConstraint("hunt_id", "sequence_index", name="uq_email_draft_hunt_index"),
        Index("idx_email_drafts_status", "status", "created_at"),
        Index("idx_email_drafts_lead_key", "lead_key"),
    )


class CampaignJob(Base):
    """DB-mediated handoff: the hunter domain requests a campaign for a
    finished hunt; the marketing service claims and executes it."""

    __tablename__ = "campaign_jobs"

    id = Column(String, primary_key=True)
    hunt_id = Column(String, nullable=False)
    status = Column(String, nullable=False, server_default=text("'queued'"))
    payload_json = Column(Text, nullable=False)
    campaign_id = Column(String, server_default=text("''"))
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
    available_at = Column(String, nullable=False, server_default=text("''"))
    claimed_at = Column(String, server_default=text("''"))
    finished_at = Column(String, server_default=text("''"))
    claimed_by = Column(String, server_default=text("''"))
    attempt_count = Column(Integer, nullable=False, server_default=text("0"))
    last_error = Column(Text, server_default=text("''"))

    __table_args__ = (
        Index("idx_campaign_jobs_status", "status", "created_at"),
        Index("idx_campaign_jobs_hunt", "hunt_id"),
    )


class User(Base):
    """Login account backed by the corporate mailbox.

    Passwords are never stored: authentication is an IMAP LOGIN against the
    company mail server with the caller-supplied credentials. ``api_key`` is
    a per-user programmatic credential replacing the shared API token.
    """

    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, nullable=False, unique=True)
    role = Column(String, nullable=False, server_default=text("'member'"))
    api_key = Column(String, nullable=False, unique=True, server_default=text("''"))
    auth_provider = Column(String, nullable=False, server_default=text("'imap'"))
    active = Column(Boolean, nullable=False, server_default=text("true"))
    created_at = Column(String, nullable=False)
    last_login_at = Column(String, server_default=text("''"))

    __table_args__ = (Index("idx_users_role", "role"),)
