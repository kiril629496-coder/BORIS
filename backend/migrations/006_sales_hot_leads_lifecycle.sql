-- Canonical BORIS Telegram sales lifecycle contract v1.
-- Align runtime states with the statuses used by hot lead control/queue/replies
-- and persist bounded Telegram follow-up / reply-autocontinue metadata.

ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS contact_peer_id BIGINT;
ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_sent_at TIMESTAMPTZ;
ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_message_id BIGINT;
ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS followup_error TEXT;

ALTER TABLE boris_sales_hot_leads
  DROP CONSTRAINT IF EXISTS boris_sales_hot_leads_status_check;
ALTER TABLE boris_sales_hot_leads
  ADD CONSTRAINT boris_sales_hot_leads_status_check CHECK (
    status IN ('new','approved','contacted','replied','qualified','lost','ignored','skipped','later','declined')
  );

ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS manager_lead_id BIGINT;
ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_text TEXT;
ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_message_id BIGINT;
ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_replied_at TIMESTAMPTZ;
ALTER TABLE boris_sales_tg_replies ADD COLUMN IF NOT EXISTS auto_reply_policy TEXT;

CREATE INDEX IF NOT EXISTS ix_boris_sales_hot_leads_contacted_followup
  ON boris_sales_hot_leads (contacted_at, followup_sent_at)
  WHERE status='contacted';
CREATE INDEX IF NOT EXISTS ix_boris_sales_tg_replies_hot_lead_received
  ON boris_sales_tg_replies (hot_lead_id, received_at DESC);
