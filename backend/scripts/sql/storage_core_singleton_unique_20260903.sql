-- STORAGE_CORE_SINGLETON_DB_GUARD_V1
-- Last-line protection for core JSON singleton rows. App-level advisory locks
-- remain primary; this partial unique index prevents any forgotten writer from
-- silently creating split-brain copies of these critical account states.
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_storage_core_singletons
ON storage(account_id,key)
WHERE key IN ('drafts','feed_items','notifications','kpi_action_history');
