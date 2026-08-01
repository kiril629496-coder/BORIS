-- ============================================================================
-- BORIS · Система мониторинга источников · схема v1
-- Alembic в проекте нет — миграция ручная. Выполнять ТОЛЬКО после бэкапа БД.
-- Все таблицы создаются с IF NOT EXISTS: повторный запуск безопасен.
-- UUID генерируется на стороне Python (uuid4), DEFAULT не ставим,
-- чтобы не зависеть от наличия pgcrypto / версии PostgreSQL.
-- ============================================================================

BEGIN;

-- --------------------------------------------------------------- ПРОГОНЫ ---
CREATE TABLE IF NOT EXISTS monitor_runs (
    id                  UUID PRIMARY KEY,
    platform            VARCHAR(32),
    mode                VARCHAR(16)   NOT NULL DEFAULT 'scheduled', -- scheduled|manual|dry
    status              VARCHAR(16)   NOT NULL DEFAULT 'running',   -- running|ok|failed
    started_at          TIMESTAMPTZ   NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    scanned_sources     INTEGER       NOT NULL DEFAULT 0,
    scanned_messages    INTEGER       NOT NULL DEFAULT 0,
    matched_messages    INTEGER       NOT NULL DEFAULT 0,
    new_messages        INTEGER       NOT NULL DEFAULT 0,
    duplicate_messages  INTEGER       NOT NULL DEFAULT 0,
    sent_alerts         INTEGER       NOT NULL DEFAULT 0,
    flood_wait_seconds  INTEGER       NOT NULL DEFAULT 0,
    errors              JSONB         NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_monitor_runs_started ON monitor_runs (started_at DESC);

-- -------------------------------------------------------------- КОНТАКТЫ ---
CREATE TABLE IF NOT EXISTS contacts (
    id                  UUID PRIMARY KEY,
    platform            VARCHAR(32)   NOT NULL,
    external_author_id  VARCHAR(64)   NOT NULL,   -- стабильный id, НЕ username
    username            VARCHAR(128),             -- меняется владельцем, идентичностью не является
    display_name        VARCHAR(255),
    contact_type        VARCHAR(16)   NOT NULL DEFAULT 'person', -- person|company
    person_id           UUID,                     -- РЕЗЕРВ: межплатформенное объединение, в v1 всегда NULL
    first_seen_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
    messages_count      INTEGER       NOT NULL DEFAULT 0,
    leads_count         INTEGER       NOT NULL DEFAULT 0,
    is_blocked          BOOLEAN       NOT NULL DEFAULT FALSE,
    payload             JSONB         NOT NULL DEFAULT '{}'::jsonb,
    notes               TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_contacts_platform_author
    ON contacts (platform, external_author_id);
CREATE INDEX IF NOT EXISTS ix_contacts_username ON contacts (lower(username));

-- -------------------------------------------------------------- ИСТОЧНИКИ ---
CREATE TABLE IF NOT EXISTS monitor_sources (
    id                     UUID PRIMARY KEY,
    account_id             VARCHAR(255),          -- РЕЗЕРВ под мультиарендность, в v1 NULL
    platform               VARCHAR(32)  NOT NULL, -- telegram|vk|reddit|forum|avito...
    source_kind            VARCHAR(32)  NOT NULL, -- channel|group|topic|forum|user|page|board
    external_id            VARCHAR(128),          -- id чата у платформы
    username               VARCHAR(128),          -- без @
    title                  VARCHAR(255),
    url                    TEXT,
    topic_id               INTEGER,               -- id темы форума внутри группы
    topic_title            VARCHAR(255),
    parent_id              UUID REFERENCES monitor_sources(id) ON DELETE CASCADE,
    is_active              BOOLEAN      NOT NULL DEFAULT TRUE,
    last_seen_external_id  BIGINT,                -- инкрементальность: выше этого id — новое
    last_scanned_at        TIMESTAMPTZ,
    error_count            INTEGER      NOT NULL DEFAULT 0,
    last_error             TEXT,
    payload                JSONB        NOT NULL DEFAULT '{}'::jsonb,
    notes                  TEXT,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);
-- NULL в UNIQUE не считается дублем, поэтому уникальность через COALESCE
CREATE UNIQUE INDEX IF NOT EXISTS ux_monitor_sources_ext
    ON monitor_sources (platform, external_id, COALESCE(topic_id, -1))
    WHERE external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_monitor_sources_uname
    ON monitor_sources (platform, lower(username), COALESCE(topic_id, -1))
    WHERE username IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_monitor_sources_active
    ON monitor_sources (platform, is_active) WHERE is_active;

-- ---------------------------------------------------------------- ПРАВИЛА ---
CREATE TABLE IF NOT EXISTS monitor_rules (
    id            UUID PRIMARY KEY,
    account_id    VARCHAR(255),                   -- РЕЗЕРВ под мультиарендность
    name          VARCHAR(255) NOT NULL,
    category      VARCHAR(128),                   -- подпись в карточке уведомления
    rule_type     VARCHAR(16)  NOT NULL DEFAULT 'keyword',  -- keyword|regex|llm|semantic
    params        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    platforms     JSONB,                          -- список; NULL = любые
    source_ids    JSONB,                          -- список UUID; NULL = любые
    source_kinds  JSONB,                          -- список видов; NULL = любые
    weights       JSONB        NOT NULL DEFAULT '{}'::jsonb, -- переопределение весов скоринга
    min_score     INTEGER      NOT NULL DEFAULT 40,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_monitor_rules_type
        CHECK (rule_type IN ('keyword', 'regex', 'llm', 'semantic'))
);
CREATE INDEX IF NOT EXISTS ix_monitor_rules_active ON monitor_rules (is_active) WHERE is_active;

-- ------------------------------------------------------------- СООБЩЕНИЯ ---
CREATE TABLE IF NOT EXISTS monitor_messages (
    id                    UUID PRIMARY KEY,
    platform              VARCHAR(32)  NOT NULL,
    source_id             UUID         NOT NULL REFERENCES monitor_sources(id) ON DELETE CASCADE,
    run_id                UUID         REFERENCES monitor_runs(id) ON DELETE SET NULL,
    external_message_id   VARCHAR(64)  NOT NULL,
    external_author_id    VARCHAR(64),
    contact_id            UUID         REFERENCES contacts(id) ON DELETE SET NULL,
    author_name           VARCHAR(255),
    author_username       VARCHAR(128),
    topic_id              INTEGER,
    topic_title           VARCHAR(255),
    text                  TEXT,
    posted_at             TIMESTAMPTZ,
    url                   TEXT,

    matched_rule_id       UUID         REFERENCES monitor_rules(id) ON DELETE SET NULL,
    matched_terms         JSONB        NOT NULL DEFAULT '[]'::jsonb,
    score_total           INTEGER      NOT NULL DEFAULT 0,
    score_breakdown       JSONB        NOT NULL DEFAULT '{}'::jsonb,

    -- колонки = то, по чему фильтруем и сортируем
    language              VARCHAR(8),
    intent                VARCHAR(32),
    priority              VARCHAR(8),             -- high|medium|low
    duplicates_group      VARCHAR(64),            -- РЕЗЕРВ: близкие дубли, в v1 NULL

    summary               TEXT,                   -- одно предложение от модели
    suggested_reply       TEXT,                   -- готовится, но НЕ отправляется автоматически
    ai                    JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- city, country, currency, sentiment, tags
    payload               JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- специфика платформы

    status                VARCHAR(24)  NOT NULL DEFAULT 'new',
    sent_at               TIMESTAMPTZ,
    collected_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT ck_monitor_messages_status CHECK (status IN
        ('new','sent','seen','lead','rejected','duplicate','in_work','closed'))
);
-- защита от повторной отправки: одно сообщение источника — одна строка
CREATE UNIQUE INDEX IF NOT EXISTS ux_monitor_messages_ext
    ON monitor_messages (platform, source_id, external_message_id);
CREATE INDEX IF NOT EXISTS ix_monitor_messages_status  ON monitor_messages (status, score_total DESC);
CREATE INDEX IF NOT EXISTS ix_monitor_messages_posted  ON monitor_messages (posted_at DESC);
CREATE INDEX IF NOT EXISTS ix_monitor_messages_contact ON monitor_messages (contact_id);

-- ------------------------------------------------------------------ ЛИДЫ ---
CREATE TABLE IF NOT EXISTS leads (
    id              UUID PRIMARY KEY,
    contact_id      UUID         REFERENCES contacts(id) ON DELETE SET NULL,
    message_id      UUID         REFERENCES monitor_messages(id) ON DELETE SET NULL,
    account_id      VARCHAR(255),
    deal_id         UUID,                          -- РЕЗЕРВ под таблицу deals, в v1 NULL
    title           VARCHAR(255),
    status          VARCHAR(24)  NOT NULL DEFAULT 'new',  -- new|in_work|won|lost
    manager_email   VARCHAR(255),
    score_total     INTEGER,
    suggested_reply TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    CONSTRAINT ck_leads_status CHECK (status IN ('new','in_work','won','lost'))
);
CREATE INDEX IF NOT EXISTS ix_leads_status  ON leads (status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_leads_contact ON leads (contact_id);
-- один найденный текст — не больше одного лида
CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_message ON leads (message_id) WHERE message_id IS NOT NULL;

COMMIT;

-- ============================================================================
-- ПРОВЕРКА ПОСЛЕ ВЫПОЛНЕНИЯ (должно вернуть 6 строк):
--   SELECT table_name FROM information_schema.tables
--    WHERE table_name IN ('monitor_runs','monitor_sources','monitor_rules',
--                         'monitor_messages','contacts','leads')
--    ORDER BY table_name;
-- ============================================================================
