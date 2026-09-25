-- Canonical CampaignItem <-> Avito identity contract v1
ALTER TABLE campaign_items ADD COLUMN IF NOT EXISTS feed_identity VARCHAR;
ALTER TABLE campaign_items ADD COLUMN IF NOT EXISTS avito_item_id VARCHAR;
ALTER TABLE campaign_items ADD COLUMN IF NOT EXISTS identity_status VARCHAR;

CREATE INDEX IF NOT EXISTS ix_campaign_items_feed_identity ON campaign_items (feed_identity);
CREATE INDEX IF NOT EXISTS ix_campaign_items_avito_item_id ON campaign_items (avito_item_id);
CREATE INDEX IF NOT EXISTS ix_campaign_items_identity_status ON campaign_items (identity_status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_items_account_avito_item
  ON campaign_items (account_id, avito_item_id) WHERE avito_item_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_items_account_feed_identity
  ON campaign_items (account_id, feed_identity) WHERE feed_identity IS NOT NULL;
