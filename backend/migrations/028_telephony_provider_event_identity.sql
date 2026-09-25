-- BORIS Phone: durable provider event idempotency across API replicas/generations.
-- Safe/idempotent: duplicate groups were verified zero before production index creation.
CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_events_provider_identity
ON telephony_events(account_id,provider,provider_event_id)
WHERE provider IS NOT NULL AND provider_event_id IS NOT NULL AND provider_event_id<>'';
