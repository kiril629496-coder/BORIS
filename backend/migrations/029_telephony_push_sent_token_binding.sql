-- Bind authenticated device receipts to the exact native push token used
-- for the provider send.  Provider acceptance alone remains insufficient.
ALTER TABLE telephony_push_outbox
    ADD COLUMN IF NOT EXISTS sent_push_token_hash text;
