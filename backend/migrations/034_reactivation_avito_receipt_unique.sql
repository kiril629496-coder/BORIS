-- Reactivation Avito delivery receipt identity.
-- One provider message ID is evidence for exactly one reactivation send.
-- Safe/idempotent after existing duplicate audit returns zero rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_react_msg_avito_receipt
    ON reactivation_messages (avito_message_id)
    WHERE avito_message_id IS NOT NULL;
