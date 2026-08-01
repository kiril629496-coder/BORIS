-- 001_email_verification.sql
-- Подтверждение email при регистрации BORIS.
-- Только структура и данные. Без BEGIN/COMMIT: транзакцией управляет обёртка run_001.sh.
-- Идемпотентно: повторный запуск не ломает и не дублирует.

-- ---------- 1. Новые колонки users ----------
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified    boolean NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at timestamp;
ALTER TABLE users ADD COLUMN IF NOT EXISTS status            varchar(32) NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_normalized  varchar;
ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_started_at  timestamp;

-- ---------- 2. Данные существующих пользователей ----------
-- Все 6 текущих пользователей имеют is_active = true, поэтому все получают status='active'.
-- CASE оставлен на случай появления строк с is_active NULL/false между написанием и запуском.
UPDATE users
   SET email_verified    = true,
       email_verified_at = COALESCE(email_verified_at, created_at, now()),
       status            = CASE WHEN is_active IS TRUE THEN 'active' ELSE 'blocked' END
 WHERE email_verified = false;

-- Нормализация: только регистр и обрезка пробелов. Плюс-теги НЕ срезаем —
-- QA-блок регистрирует qa+<ts>-<rand>@borisqa.ru, срезание схлопнуло бы их в один адрес.
UPDATE users
   SET email_normalized = lower(btrim(email))
 WHERE email_normalized IS NULL;

-- Признак «общий регистрационный триал уже выдавался».
-- Проставляем только тем, у кого subscription_expires_at не пуст (сегодня это id 23 и 24).
UPDATE users
   SET trial_started_at = COALESCE(created_at, now())
 WHERE subscription_expires_at IS NOT NULL
   AND trial_started_at IS NULL;

-- ---------- 3. Ограничения users ----------
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_normalized ON users (email_normalized);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_status') THEN
        ALTER TABLE users ADD CONSTRAINT ck_users_status
            CHECK (status IN ('pending_verification', 'active', 'blocked'));
    END IF;
END $$;

-- ---------- 4. email_verifications ----------
-- verification_id намеренно varchar, а не uuid: генерируется приложением (secrets),
-- не зависит от версии Postgres и от расширения pgcrypto.
CREATE TABLE IF NOT EXISTS email_verifications (
    id              serial PRIMARY KEY,
    verification_id varchar(64)  NOT NULL UNIQUE,
    user_id         integer      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email           varchar      NOT NULL,
    code_hash       varchar(255) NOT NULL,
    expires_at      timestamp    NOT NULL,
    attempts_count  integer      NOT NULL DEFAULT 0,
    max_attempts    integer      NOT NULL DEFAULT 5,
    sent_at         timestamp,
    used_at         timestamp,
    created_at      timestamp    NOT NULL DEFAULT now(),
    request_ip      varchar(64)
);

CREATE INDEX IF NOT EXISTS ix_evf_user_id      ON email_verifications (user_id);
CREATE INDEX IF NOT EXISTS ix_evf_email_norm   ON email_verifications (lower(btrim(email)), created_at);
CREATE INDEX IF NOT EXISTS ix_evf_created_at   ON email_verifications (created_at);
CREATE INDEX IF NOT EXISTS ix_evf_active       ON email_verifications (user_id, used_at, expires_at);

-- ---------- 5. Лимиты (Redis в проекте нет) ----------
CREATE TABLE IF NOT EXISTS auth_rate_events (
    id         serial PRIMARY KEY,
    kind       varchar(32)  NOT NULL,
    rate_key   varchar(255) NOT NULL,
    ip         varchar(64),
    created_at timestamp    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_are_kind_key_created ON auth_rate_events (kind, rate_key, created_at);
CREATE INDEX IF NOT EXISTS ix_are_kind_ip_created  ON auth_rate_events (kind, ip, created_at);

CREATE TABLE IF NOT EXISTS auth_blocks (
    id            serial PRIMARY KEY,
    block_key     varchar(255) NOT NULL,
    reason        varchar(64),
    blocked_until timestamp    NOT NULL,
    created_at    timestamp    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ab_key_until ON auth_blocks (block_key, blocked_until);
