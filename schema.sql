-- AI Hunter full schema — apply ONCE on an EMPTY database:
--   psql "$PSQL_URL" -f schema.sql
-- Generated from persistence.models at build time.

CREATE TABLE campaign_jobs (
	id VARCHAR NOT NULL, 
	hunt_id VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'queued' NOT NULL, 
	payload_json TEXT NOT NULL, 
	campaign_id VARCHAR DEFAULT '', 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	available_at VARCHAR DEFAULT '' NOT NULL, 
	claimed_at VARCHAR DEFAULT '', 
	finished_at VARCHAR DEFAULT '', 
	claimed_by VARCHAR DEFAULT '', 
	attempt_count INTEGER DEFAULT 0 NOT NULL, 
	last_error TEXT DEFAULT '', 
	PRIMARY KEY (id)
);

CREATE TABLE customs_import_records (
	id VARCHAR NOT NULL, 
	lead_id VARCHAR DEFAULT '', 
	lead_key VARCHAR DEFAULT '', 
	company_name VARCHAR DEFAULT '', 
	domain VARCHAR DEFAULT '', 
	consignee_name VARCHAR DEFAULT '', 
	supplier_name VARCHAR DEFAULT '', 
	country VARCHAR DEFAULT '', 
	hs_code VARCHAR DEFAULT '', 
	product_description TEXT DEFAULT '', 
	arrival_date VARCHAR DEFAULT '', 
	quantity VARCHAR DEFAULT '', 
	weight VARCHAR DEFAULT '', 
	source VARCHAR DEFAULT 'importyeti_csv' NOT NULL, 
	source_ref VARCHAR DEFAULT '', 
	record_hash VARCHAR NOT NULL, 
	raw JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at VARCHAR DEFAULT '', 
	PRIMARY KEY (id), 
	UNIQUE (record_hash)
);

CREATE TABLE customs_sync_runs (
	id VARCHAR NOT NULL, 
	run_date VARCHAR NOT NULL, 
	trigger VARCHAR DEFAULT 'scheduled' NOT NULL, 
	status VARCHAR DEFAULT 'running' NOT NULL, 
	files_processed INTEGER DEFAULT 0 NOT NULL, 
	records_ingested INTEGER DEFAULT 0 NOT NULL, 
	leads_checked INTEGER DEFAULT 0 NOT NULL, 
	leads_active INTEGER DEFAULT 0 NOT NULL, 
	new_leads INTEGER DEFAULT 0 NOT NULL, 
	stats JSONB DEFAULT '{}'::jsonb NOT NULL, 
	error TEXT DEFAULT '', 
	started_at VARCHAR DEFAULT '', 
	finished_at VARCHAR DEFAULT '', 
	PRIMARY KEY (id)
);

CREATE TABLE customs_watchlist (
	id VARCHAR NOT NULL, 
	hs_code VARCHAR NOT NULL, 
	product_keywords JSONB DEFAULT '[]'::jsonb NOT NULL, 
	countries JSONB DEFAULT '[]'::jsonb NOT NULL, 
	note VARCHAR DEFAULT '', 
	enabled INTEGER DEFAULT 1 NOT NULL, 
	created_at VARCHAR DEFAULT '', 
	updated_at VARCHAR DEFAULT '', 
	PRIMARY KEY (id), 
	CONSTRAINT uq_customs_watch_hs UNIQUE (hs_code)
);

CREATE TABLE email_accounts (
	id VARCHAR NOT NULL, 
	provider_type VARCHAR NOT NULL, 
	from_name VARCHAR NOT NULL, 
	from_email VARCHAR NOT NULL, 
	reply_to VARCHAR DEFAULT '', 
	smtp_host VARCHAR DEFAULT '', 
	smtp_port INTEGER DEFAULT 587, 
	smtp_username VARCHAR DEFAULT '', 
	smtp_secret_encrypted TEXT DEFAULT '', 
	imap_host VARCHAR DEFAULT '', 
	imap_port INTEGER DEFAULT 993, 
	imap_username VARCHAR DEFAULT '', 
	imap_secret_encrypted TEXT DEFAULT '', 
	use_tls INTEGER DEFAULT 1 NOT NULL, 
	status VARCHAR DEFAULT 'active' NOT NULL, 
	daily_send_limit INTEGER DEFAULT 50 NOT NULL, 
	hourly_send_limit INTEGER DEFAULT 10 NOT NULL, 
	last_test_at VARCHAR DEFAULT '', 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE email_campaigns (
	id VARCHAR NOT NULL, 
	hunt_id VARCHAR NOT NULL, 
	email_account_id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'draft' NOT NULL, 
	language_mode VARCHAR DEFAULT 'auto_by_region' NOT NULL, 
	default_language VARCHAR DEFAULT 'en' NOT NULL, 
	fallback_language VARCHAR DEFAULT 'en' NOT NULL, 
	tone VARCHAR DEFAULT 'professional' NOT NULL, 
	step1_delay_days INTEGER DEFAULT 0 NOT NULL, 
	step2_delay_days INTEGER DEFAULT 3 NOT NULL, 
	step3_delay_days INTEGER DEFAULT 3 NOT NULL, 
	min_fit_score FLOAT DEFAULT 0.6 NOT NULL, 
	min_contactability_score FLOAT DEFAULT 0.45 NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE email_drafts (
	id VARCHAR NOT NULL, 
	hunt_id VARCHAR NOT NULL, 
	sequence_index INTEGER DEFAULT 0 NOT NULL, 
	lead_id VARCHAR DEFAULT '', 
	lead_key VARCHAR DEFAULT '', 
	company_name VARCHAR DEFAULT '', 
	website TEXT DEFAULT '', 
	locale VARCHAR DEFAULT 'en' NOT NULL, 
	target JSONB DEFAULT '{}'::jsonb NOT NULL, 
	targets JSONB DEFAULT '[]'::jsonb NOT NULL, 
	emails JSONB DEFAULT '[]'::jsonb NOT NULL, 
	language_choice JSONB DEFAULT '{}'::jsonb NOT NULL, 
	strategy_brief JSONB DEFAULT '{}'::jsonb NOT NULL, 
	validation_summary JSONB DEFAULT '{}'::jsonb NOT NULL, 
	review_summary JSONB DEFAULT '{}'::jsonb NOT NULL, 
	review_status VARCHAR DEFAULT '', 
	generation_mode VARCHAR DEFAULT 'personalized' NOT NULL, 
	template_id VARCHAR DEFAULT '', 
	template_group VARCHAR DEFAULT '', 
	template_usage_index INTEGER DEFAULT 0 NOT NULL, 
	template_max_send_count INTEGER DEFAULT 0 NOT NULL, 
	template_seed_source VARCHAR DEFAULT '', 
	status VARCHAR DEFAULT 'draft' NOT NULL, 
	edited_by_review BOOLEAN DEFAULT false NOT NULL, 
	manual_review JSONB DEFAULT '{}'::jsonb NOT NULL, 
	error TEXT DEFAULT '', 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_email_draft_hunt_index UNIQUE (hunt_id, sequence_index)
);

CREATE TABLE email_messages (
	id VARCHAR NOT NULL, 
	sequence_id VARCHAR NOT NULL, 
	step_number INTEGER NOT NULL, 
	goal VARCHAR NOT NULL, 
	locale VARCHAR NOT NULL, 
	subject TEXT NOT NULL, 
	body_text TEXT NOT NULL, 
	status VARCHAR DEFAULT 'pending' NOT NULL, 
	scheduled_at VARCHAR NOT NULL, 
	sent_at VARCHAR DEFAULT '', 
	provider_message_id VARCHAR DEFAULT '', 
	thread_key VARCHAR DEFAULT '', 
	failure_reason TEXT DEFAULT '', 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_email_message_sequence_step UNIQUE (sequence_id, step_number)
);

CREATE TABLE email_reply_events (
	id VARCHAR NOT NULL, 
	sequence_id VARCHAR NOT NULL, 
	message_id VARCHAR DEFAULT '', 
	from_email VARCHAR NOT NULL, 
	subject TEXT DEFAULT '', 
	snippet TEXT DEFAULT '', 
	received_at VARCHAR NOT NULL, 
	raw_ref VARCHAR DEFAULT '', 
	created_at VARCHAR NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE hunt_jobs (
	id VARCHAR NOT NULL, 
	payload_json TEXT NOT NULL, 
	status VARCHAR DEFAULT 'queued' NOT NULL, 
	available_at VARCHAR NOT NULL, 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	started_at VARCHAR DEFAULT '', 
	finished_at VARCHAR DEFAULT '', 
	claimed_by VARCHAR DEFAULT '', 
	attempt_count INTEGER DEFAULT 0 NOT NULL, 
	last_error TEXT DEFAULT '', 
	last_hunt_id VARCHAR DEFAULT '', 
	progress_stage VARCHAR DEFAULT '', 
	progress_message TEXT DEFAULT '', 
	template_seed_status VARCHAR DEFAULT '', 
	template_seed_source VARCHAR DEFAULT '', 
	PRIMARY KEY (id)
);

CREATE TABLE hunts (
	id VARCHAR NOT NULL, 
	status VARCHAR DEFAULT 'pending' NOT NULL, 
	current_stage VARCHAR DEFAULT '', 
	hunt_round INTEGER DEFAULT 0 NOT NULL, 
	leads_count INTEGER DEFAULT 0 NOT NULL, 
	email_sequences_count INTEGER DEFAULT 0 NOT NULL, 
	error TEXT DEFAULT '', 
	website_url TEXT DEFAULT '', 
	data JSONB DEFAULT '{}'::jsonb NOT NULL, 
	created_at VARCHAR DEFAULT '', 
	updated_at VARCHAR DEFAULT '', 
	PRIMARY KEY (id)
);

CREATE TABLE lead_email_sequences (
	id VARCHAR NOT NULL, 
	campaign_id VARCHAR NOT NULL, 
	hunt_id VARCHAR NOT NULL, 
	lead_key VARCHAR NOT NULL, 
	lead_email VARCHAR NOT NULL, 
	lead_name VARCHAR DEFAULT '', 
	decision_maker_name VARCHAR DEFAULT '', 
	decision_maker_title VARCHAR DEFAULT '', 
	locale VARCHAR DEFAULT 'en' NOT NULL, 
	generation_mode VARCHAR DEFAULT 'personalized' NOT NULL, 
	template_id VARCHAR DEFAULT '', 
	template_group VARCHAR DEFAULT '', 
	template_usage_index INTEGER DEFAULT 0 NOT NULL, 
	template_max_send_count INTEGER DEFAULT 0 NOT NULL, 
	status VARCHAR DEFAULT 'draft' NOT NULL, 
	current_step INTEGER DEFAULT 0 NOT NULL, 
	stop_reason VARCHAR DEFAULT '', 
	replied_at VARCHAR DEFAULT '', 
	last_sent_at VARCHAR DEFAULT '', 
	next_scheduled_at VARCHAR DEFAULT '', 
	created_at VARCHAR NOT NULL, 
	updated_at VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_sequence_campaign_lead UNIQUE (campaign_id, lead_key)
);

CREATE TABLE leads (
	id VARCHAR NOT NULL, 
	lead_key VARCHAR NOT NULL, 
	domain VARCHAR DEFAULT '' NOT NULL, 
	company_name VARCHAR DEFAULT '', 
	website TEXT DEFAULT '', 
	industry VARCHAR DEFAULT '', 
	country_code VARCHAR DEFAULT '', 
	address TEXT DEFAULT '', 
	emails JSONB DEFAULT '[]'::jsonb NOT NULL, 
	phone_numbers JSONB DEFAULT '[]'::jsonb NOT NULL, 
	social_media JSONB DEFAULT '{}'::jsonb NOT NULL, 
	decision_makers JSONB DEFAULT '[]'::jsonb NOT NULL, 
	customs_data TEXT DEFAULT '', 
	evidence JSONB DEFAULT '[]'::jsonb NOT NULL, 
	match_score FLOAT DEFAULT 0 NOT NULL, 
	fit_score FLOAT DEFAULT 0 NOT NULL, 
	contactability_score FLOAT DEFAULT 0 NOT NULL, 
	priority_tier VARCHAR DEFAULT '', 
	procurement_status VARCHAR DEFAULT 'unknown', 
	last_import_at VARCHAR DEFAULT '', 
	import_count_90d INTEGER DEFAULT 0 NOT NULL, 
	customs_last_checked_at VARCHAR DEFAULT '', 
	first_seen_at VARCHAR DEFAULT '', 
	last_seen_at VARCHAR DEFAULT '', 
	seen_count INTEGER DEFAULT 1 NOT NULL, 
	first_hunt_id VARCHAR DEFAULT '', 
	last_hunt_id VARCHAR DEFAULT '', 
	raw JSONB DEFAULT '{}'::jsonb NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (lead_key)
);

CREATE TABLE users (
	id VARCHAR NOT NULL, 
	email VARCHAR NOT NULL, 
	role VARCHAR DEFAULT 'member' NOT NULL, 
	api_key VARCHAR DEFAULT '' NOT NULL, 
	auth_provider VARCHAR DEFAULT 'imap' NOT NULL, 
	active BOOLEAN DEFAULT true NOT NULL, 
	created_at VARCHAR NOT NULL, 
	last_login_at VARCHAR DEFAULT '', 
	PRIMARY KEY (id), 
	UNIQUE (email), 
	UNIQUE (api_key)
);

CREATE TABLE hunt_leads (
	hunt_id VARCHAR NOT NULL, 
	lead_id VARCHAR NOT NULL, 
	reused INTEGER DEFAULT 0 NOT NULL, 
	added_at VARCHAR DEFAULT '', 
	PRIMARY KEY (hunt_id, lead_id), 
	FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE
);

CREATE TABLE lead_sightings (
	id BIGSERIAL NOT NULL, 
	lead_id VARCHAR NOT NULL, 
	hunt_id VARCHAR NOT NULL, 
	source_keyword VARCHAR DEFAULT '', 
	seen_at VARCHAR DEFAULT '', 
	PRIMARY KEY (id), 
	CONSTRAINT uq_lead_sighting UNIQUE (lead_id, hunt_id, source_keyword), 
	FOREIGN KEY(lead_id) REFERENCES leads (id) ON DELETE CASCADE
);
