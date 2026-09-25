CREATE TABLE IF NOT EXISTS ext_api_keys (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    key_prefix VARCHAR(16) NOT NULL,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    scopes VARCHAR(200) NOT NULL DEFAULT 'read',
    account_ids TEXT,
    owner_user_id INTEGER,
    actor_type VARCHAR(20) NOT NULL DEFAULT 'ai_agent',
    actor_name VARCHAR(40) NOT NULL DEFAULT 'agent',
    rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
    max_items_per_call INTEGER NOT NULL DEFAULT 200,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMP,
    requests_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_ext_api_keys_prefix ON ext_api_keys (key_prefix);

CREATE TABLE IF NOT EXISTS ext_audit (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(40),
    key_id INTEGER,
    actor VARCHAR(120),
    actor_type VARCHAR(20),
    source VARCHAR(40) DEFAULT 'ext_api',
    method VARCHAR(8),
    path VARCHAR(200),
    operation VARCHAR(60),
    account_id INTEGER,
    job_id BIGINT,
    status VARCHAR(20),
    status_code INTEGER,
    affected INTEGER DEFAULT 0,
    dry_run BOOLEAN DEFAULT FALSE,
    idempotency_key VARCHAR(120),
    params_json TEXT,
    result_json TEXT,
    error_code VARCHAR(60),
    error_text TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_audit_key ON ext_audit (key_id, created_at);
CREATE INDEX IF NOT EXISTS ix_ext_audit_account ON ext_audit (account_id, created_at);
CREATE INDEX IF NOT EXISTS ix_ext_audit_request ON ext_audit (request_id);

CREATE TABLE IF NOT EXISTS ext_idempotency (
    id BIGSERIAL PRIMARY KEY,
    key_id INTEGER NOT NULL,
    idempotency_key VARCHAR(120) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    status_code INTEGER NOT NULL,
    response_json TEXT,
    job_id BIGINT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ext_idem ON ext_idempotency (key_id, idempotency_key);

CREATE TABLE IF NOT EXISTS ext_jobs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(40),
    key_id INTEGER,
    actor VARCHAR(120),
    actor_type VARCHAR(20),
    fixed_by VARCHAR(40),
    next_action VARCHAR(40),
    handoff_id BIGINT,
    account_id INTEGER NOT NULL,
    account_ref VARCHAR(200),
    operation VARCHAR(60) NOT NULL,
    op_class VARCHAR(20) NOT NULL DEFAULT 'READ_ONLY',
    params_json TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    dry_run BOOLEAN NOT NULL DEFAULT FALSE,
    preflight_json TEXT,
    postflight_json TEXT,
    qa_status VARCHAR(20),
    result_json TEXT,
    error_code VARCHAR(60),
    error_text TEXT,
    retryable BOOLEAN,
    idempotency_key VARCHAR(120),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_jobs_status ON ext_jobs (status, account_id);
CREATE INDEX IF NOT EXISTS ix_ext_jobs_created ON ext_jobs (created_at);
CREATE INDEX IF NOT EXISTS ix_ext_jobs_request ON ext_jobs (request_id);

CREATE TABLE IF NOT EXISTS ext_job_attempts (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT NOT NULL,
    attempt_number INTEGER NOT NULL,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP,
    status VARCHAR(20),
    error_code VARCHAR(60),
    error_text TEXT,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_ext_job_attempts_job ON ext_job_attempts (job_id, attempt_number);

CREATE TABLE IF NOT EXISTS ext_handoffs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(40),
    job_id BIGINT,
    from_actor VARCHAR(40) NOT NULL,
    to_actor VARCHAR(40) NOT NULL,
    reason VARCHAR(60),
    context_json TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    resolution VARCHAR(30),
    resolution_note TEXT,
    resolved_by VARCHAR(40),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_handoffs_job ON ext_handoffs (job_id);
CREATE INDEX IF NOT EXISTS ix_ext_handoffs_to ON ext_handoffs (to_actor, status);

-- Догоняющие ALTER для обновления с прошлых версий шлюза.
-- В проекте нет alembic, а CREATE TABLE IF NOT EXISTS не добавляет колонки
-- в уже существующую таблицу — поэтому каждое новое поле дублируется здесь.
ALTER TABLE ext_api_keys ADD COLUMN IF NOT EXISTS actor_type VARCHAR(20) NOT NULL DEFAULT 'ai_agent';
ALTER TABLE ext_api_keys ADD COLUMN IF NOT EXISTS actor_name VARCHAR(40) NOT NULL DEFAULT 'agent';
ALTER TABLE ext_api_keys ADD COLUMN IF NOT EXISTS rate_limit_per_min INTEGER NOT NULL DEFAULT 60;
ALTER TABLE ext_api_keys ADD COLUMN IF NOT EXISTS max_items_per_call INTEGER NOT NULL DEFAULT 200;
ALTER TABLE ext_audit ADD COLUMN IF NOT EXISTS request_id VARCHAR(40);
ALTER TABLE ext_audit ADD COLUMN IF NOT EXISTS actor VARCHAR(120);
ALTER TABLE ext_audit ADD COLUMN IF NOT EXISTS actor_type VARCHAR(20);
ALTER TABLE ext_audit ADD COLUMN IF NOT EXISTS operation VARCHAR(60);
ALTER TABLE ext_audit ADD COLUMN IF NOT EXISTS status VARCHAR(20);
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS actor_type VARCHAR(20);
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS fixed_by VARCHAR(40);
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS next_action VARCHAR(40);
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS handoff_id BIGINT;
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS qa_status VARCHAR(20);
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS preflight_json TEXT;
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS postflight_json TEXT;
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS retryable BOOLEAN;
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS account_ref VARCHAR(200);
ALTER TABLE ext_jobs ADD COLUMN IF NOT EXISTS confirmed BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS ext_agent_state (
    agent VARCHAR(40) PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'unknown',
    note VARCHAR(500),
    priority INTEGER DEFAULT 50,
    last_seen TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ext_dev_jobs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(40),
    title VARCHAR(300) NOT NULL,
    goal TEXT NOT NULL,
    scope_json TEXT,
    acceptance_json TEXT,
    current_phase VARCHAR(20) NOT NULL DEFAULT 'PLANNING',
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 50,
    depends_on_json TEXT,
    locks_json TEXT,
    assigned_agent VARCHAR(40),
    wait_reason VARCHAR(300),
    owner_agent VARCHAR(40),
    reviewer_agent VARCHAR(40),
    created_by VARCHAR(40),
    phases_json TEXT,
    files_changed_json TEXT,
    tests_json TEXT,
    errors_json TEXT,
    decisions_json TEXT,
    fix_loops INTEGER NOT NULL DEFAULT 0,
    max_fix_loops INTEGER NOT NULL DEFAULT 5,
    deployment_status VARCHAR(30),
    qa_status VARCHAR(20),
    final_result_json TEXT,
    blocked_reason TEXT,
    batch_id VARCHAR(40),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_dev_jobs_status ON ext_dev_jobs (status, priority DESC, id);
CREATE INDEX IF NOT EXISTS ix_ext_dev_jobs_agent ON ext_dev_jobs (assigned_agent, status);
CREATE INDEX IF NOT EXISTS ix_ext_dev_jobs_batch ON ext_dev_jobs (batch_id);

CREATE TABLE IF NOT EXISTS ext_dev_events (
    id BIGSERIAL PRIMARY KEY,
    dev_job_id BIGINT NOT NULL,
    phase VARCHAR(20),
    actor VARCHAR(40),
    kind VARCHAR(40),
    payload_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_dev_events_job ON ext_dev_events (dev_job_id, id);

ALTER TABLE ext_handoffs ADD COLUMN IF NOT EXISTS dev_job_id BIGINT;
ALTER TABLE ext_handoffs ADD COLUMN IF NOT EXISTS phase VARCHAR(20);
ALTER TABLE ext_dev_jobs ADD COLUMN IF NOT EXISTS priority INTEGER NOT NULL DEFAULT 50;
ALTER TABLE ext_dev_jobs ADD COLUMN IF NOT EXISTS depends_on_json TEXT;
ALTER TABLE ext_dev_jobs ADD COLUMN IF NOT EXISTS locks_json TEXT;
ALTER TABLE ext_dev_jobs ADD COLUMN IF NOT EXISTS assigned_agent VARCHAR(40);
ALTER TABLE ext_dev_jobs ADD COLUMN IF NOT EXISTS wait_reason VARCHAR(300);
ALTER TABLE ext_dev_jobs ADD COLUMN IF NOT EXISTS batch_id VARCHAR(40);

CREATE TABLE IF NOT EXISTS ext_price_book (
    code VARCHAR(40) PRIMARY KEY,
    unit VARCHAR(20) NOT NULL DEFAULT 'шт',
    price_rub NUMERIC(12,4) NOT NULL DEFAULT 0,
    note VARCHAR(300),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ext_cost_approvals (
    id BIGSERIAL PRIMARY KEY,
    granted_by VARCHAR(60) NOT NULL,
    limit_rub NUMERIC(12,2) NOT NULL DEFAULT 0,
    operations_json TEXT,
    account_id INTEGER,
    expires_at TIMESTAMP,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    note VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_cost_approvals_live ON ext_cost_approvals (revoked, expires_at);

CREATE TABLE IF NOT EXISTS ext_job_costs (
    id BIGSERIAL PRIMARY KEY,
    job_id BIGINT,
    dev_job_id BIGINT,
    kind VARCHAR(20) NOT NULL,
    code VARCHAR(40),
    units INTEGER NOT NULL DEFAULT 0,
    unit_price_rub NUMERIC(12,4) NOT NULL DEFAULT 0,
    amount_rub NUMERIC(12,2) NOT NULL DEFAULT 0,
    actor VARCHAR(40),
    provider VARCHAR(60),
    approval_id BIGINT,
    note VARCHAR(300),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_job_costs_job ON ext_job_costs (job_id);
CREATE INDEX IF NOT EXISTS ix_ext_job_costs_dev ON ext_job_costs (dev_job_id);
CREATE INDEX IF NOT EXISTS ix_ext_job_costs_appr ON ext_job_costs (approval_id);

CREATE TABLE IF NOT EXISTS ext_agent_adapters (
    agent VARCHAR(40) PRIMARY KEY,
    mode VARCHAR(20) NOT NULL DEFAULT 'actions',
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    reason VARCHAR(80),
    note VARCHAR(500),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ext_job_ops (
    id BIGSERIAL PRIMARY KEY,
    dev_job_id BIGINT,
    job_id BIGINT,
    name VARCHAR(30) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'waiting',
    requested INTEGER,
    actual INTEGER,
    evidence TEXT,
    error_code VARCHAR(60),
    note VARCHAR(500),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ext_job_ops ON ext_job_ops (COALESCE(dev_job_id,0), COALESCE(job_id,0), name);

-- Правки кода, предложенные агентами: агент только предлагает,
-- применяет человек, и только через backup -> write -> verify -> rollback.
CREATE TABLE IF NOT EXISTS ext_dev_patches (
    id BIGSERIAL PRIMARY KEY,
    dev_job_id BIGINT,
    author VARCHAR(40) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'proposed',
    note VARCHAR(500),
    edits_json TEXT NOT NULL,
    applied_by VARCHAR(40),
    backup_dir VARCHAR(400),
    verify_output TEXT,
    error_text TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    applied_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_ext_dev_patches_job ON ext_dev_patches (dev_job_id);
CREATE INDEX IF NOT EXISTS ix_ext_dev_patches_status ON ext_dev_patches (status);

-- Контур Claude Lead <-> Orchestrator <-> GPT Executor.
-- Наряд это единый источник истины по одному пакету работ: цель, критерии,
-- запреты, требуемые проверки, чей сейчас ход и на какой итерации цикл.
CREATE TABLE IF NOT EXISTS ext_a2a_orders (
    id BIGSERIAL PRIMARY KEY,
    dev_job_id BIGINT NOT NULL,
    phase VARCHAR(30),
    goal TEXT NOT NULL,
    scope_json TEXT,
    acceptance_json TEXT,
    forbidden_json TEXT,
    required_qa_json TEXT,
    required_evidence_json TEXT,
    created_by VARCHAR(40),
    assigned_to VARCHAR(40) NOT NULL DEFAULT 'gpt-executor',
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    iteration INTEGER NOT NULL DEFAULT 0,
    max_iterations INTEGER NOT NULL DEFAULT 6,
    priority INTEGER NOT NULL DEFAULT 50,
    last_review_json TEXT,
    last_evidence_json TEXT,
    blocked_reason VARCHAR(500),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_a2a_orders_state ON ext_a2a_orders (assigned_to, status, priority DESC);
CREATE INDEX IF NOT EXISTS ix_ext_a2a_orders_job ON ext_a2a_orders (dev_job_id);
-- Exactly one live autonomous order per development job. Application preflight
-- avoids needless conflicts; this partial unique index closes the race between
-- concurrent dispatcher/recovery/master producers.
CREATE UNIQUE INDEX IF NOT EXISTS ux_ext_a2a_orders_one_live_per_job
ON ext_a2a_orders (dev_job_id)
WHERE status IN ('queued','running','review_requested','returned','blocked_human','blocked_infra');

CREATE TABLE IF NOT EXISTS ext_a2a_messages (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL,
    kind VARCHAR(64) NOT NULL,
    author VARCHAR(40),
    body_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_a2a_messages_order ON ext_a2a_messages (order_id, id);

CREATE TABLE IF NOT EXISTS ext_a2a_runs (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL,
    agent VARCHAR(40),
    provider VARCHAR(20),
    model VARCHAR(60),
    iteration INTEGER,
    steps INTEGER,
    tool_calls INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    outcome VARCHAR(24),
    error_text TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_ext_a2a_runs_order ON ext_a2a_runs (order_id, id);

CREATE TABLE IF NOT EXISTS ext_deploy_log (
    id BIGSERIAL PRIMARY KEY,
    sha VARCHAR(64) NOT NULL,
    branch VARCHAR(80),
    ok BOOLEAN NOT NULL DEFAULT FALSE,
    note VARCHAR(1000),
    files_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_deploy_log_sha ON ext_deploy_log (sha);

ALTER TABLE ext_a2a_orders ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(40);
ALTER TABLE ext_a2a_orders ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMP;


-- Состояние AI-провайдеров: почему конкретный сейчас не годится и когда
-- его снова пробовать. Без этого «нет баланса» узнаёт только тот модуль,
-- который на него напоролся.
CREATE TABLE IF NOT EXISTS ext_ai_providers (
    name VARCHAR(40) PRIMARY KEY,
    state VARCHAR(30) NOT NULL DEFAULT 'AVAILABLE',
    note VARCHAR(400),
    last_failure_at TIMESTAMP,
    retry_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Выполненные вызовы: переключение провайдера не должно повторять побочный
-- эффект, который уже случился.
CREATE TABLE IF NOT EXISTS ext_ai_calls (
    idem_key VARCHAR(120) PRIMARY KEY,
    provider VARCHAR(40),
    capability VARCHAR(40),
    result_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_ai_calls_created ON ext_ai_calls (created_at);

-- Использованные изображения: один и тот же сток в кампании десять раз —
-- это заметно клиенту раньше, чем нам.
CREATE TABLE IF NOT EXISTS ext_banner_images (
    id BIGSERIAL PRIMARY KEY,
    image_hash VARCHAR(64),
    perceptual_hash VARCHAR(80),
    source_url TEXT,
    used_by_client VARCHAR(80),
    used_by_campaign VARCHAR(80),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ext_banner_images_hash ON ext_banner_images (perceptual_hash);
CREATE INDEX IF NOT EXISTS ix_ext_banner_images_client ON ext_banner_images (used_by_client, used_by_campaign);
