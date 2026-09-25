-- BORIS Phone: durable exactly-once boundary for active push intents.
-- Historical/terminal rows remain unconstrained.
CREATE UNIQUE INDEX IF NOT EXISTS ux_telephony_push_active_intent
ON telephony_push_outbox(account_id,device_id,call_id,event_type)
WHERE status IN ('pending','retry','waiting_configuration','sent');
