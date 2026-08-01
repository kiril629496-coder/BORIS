-- Миграция v1.2 — бюджет модуля «Поиск клиентов».
-- Добавляет статус awaiting_budget (анализ отложен из-за лимита) и колонку
-- budget_block (код сработавшего предохранителя:
--   monthly_budget | daily_analysis_limit | monthly_analysis_limit).
-- Идемпотентна: повторный запуск не ломает.

-- 1) новая колонка причины блокировки бюджетом
ALTER TABLE monitor_messages
    ADD COLUMN IF NOT EXISTS budget_block VARCHAR(24);

-- 2) расширяем CHECK статусов, добавляя awaiting_budget.
--    DROP + ADD, потому что ALTER CONSTRAINT для CHECK не поддерживается.
ALTER TABLE monitor_messages
    DROP CONSTRAINT IF EXISTS ck_monitor_messages_status;

ALTER TABLE monitor_messages
    ADD CONSTRAINT ck_monitor_messages_status CHECK (status IN
        ('new','sent','seen','lead','rejected','duplicate','in_work','closed',
         'awaiting_budget'));

-- 3) индекс для быстрого поиска отложенных находок (для будущего до-анализа)
CREATE INDEX IF NOT EXISTS ix_monitor_messages_awaiting
    ON monitor_messages (status) WHERE status = 'awaiting_budget';

COMMENT ON COLUMN monitor_messages.budget_block IS
    'Код сработавшего предохранителя: monthly_budget|daily_analysis_limit|monthly_analysis_limit. NULL — анализ выполнен.';
