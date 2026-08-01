-- =========================
-- BORIS DATABASE INIT
-- =========================

-- CAMPAIGNS TABLE
CREATE TABLE IF NOT EXISTS campaigns (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    accounts INT DEFAULT 1,
    status TEXT DEFAULT 'created',
    created_at TIMESTAMP DEFAULT NOW()
);

-- IMPORT TASKS TABLE (для твоего /api/import)
CREATE TABLE IF NOT EXISTS import_tasks (
    id SERIAL PRIMARY KEY,
    status TEXT DEFAULT 'pending',
    result JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);