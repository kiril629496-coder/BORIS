-- ============================================================================
-- BORIS · Мониторинг · схема v1.1 (добавления к v1)
-- Идемпотентно. Выполнять после бэкапа. Порядок: ALTER -> проверка -> рестарт.
-- Если v1 ещё не применялась — сначала monitoring_v1.sql, потом этот файл.
-- ============================================================================

BEGIN;

-- 1. Объяснимость: какие ПРАВИЛА сработали и по какой ВЕРСИИ набора
ALTER TABLE monitor_messages
    ADD COLUMN IF NOT EXISTS matched_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS rules_hash    VARCHAR(32),
    ADD COLUMN IF NOT EXISTS normalizer    VARCHAR(32);

COMMENT ON COLUMN monitor_messages.matched_rules IS
    'Все сработавшие правила со своими баллами, включая не добравшие min_score (passed=false). Ответ на вопрос «почему это стало лидом».';
COMMENT ON COLUMN monitor_messages.rules_hash IS
    'Отпечаток набора правил на момент находки — по какой версии правил найден лид.';
COMMENT ON COLUMN monitor_messages.normalizer IS
    'Каким слоем нормализации разобран текст: simple | morphology | ...';

CREATE INDEX IF NOT EXISTS ix_monitor_messages_ruleshash ON monitor_messages (rules_hash);

-- 2. Доверие источнику — компонента score_breakdown.source
ALTER TABLE monitor_sources
    ADD COLUMN IF NOT EXISTS weight INTEGER NOT NULL DEFAULT 10;

COMMENT ON COLUMN monitor_sources.weight IS
    'Доверие источнику 0..15. Идёт в скоринг как компонента source. 10 — обычный источник.';

-- 3. Авторство и номер версии правила
ALTER TABLE monitor_rules
    ADD COLUMN IF NOT EXISTS version    INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_by VARCHAR(255);

-- 4. История изменений правил
CREATE TABLE IF NOT EXISTS monitor_rule_versions (
    id          UUID PRIMARY KEY,
    rule_id     UUID         NOT NULL REFERENCES monitor_rules(id) ON DELETE CASCADE,
    version     INTEGER      NOT NULL,
    snapshot    JSONB        NOT NULL,          -- полный слепок правила на момент сохранения
    changed_by  VARCHAR(255),
    comment     TEXT,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rule_versions ON monitor_rule_versions (rule_id, version);
CREATE INDEX IF NOT EXISTS ix_rule_versions_created ON monitor_rule_versions (created_at DESC);

COMMIT;

-- ============================================================================
-- ПРОВЕРКА:
--   SELECT column_name FROM information_schema.columns
--    WHERE table_name='monitor_messages'
--      AND column_name IN ('matched_rules','rules_hash','normalizer');
--   SELECT count(*) FROM monitor_rule_versions;
-- ============================================================================
