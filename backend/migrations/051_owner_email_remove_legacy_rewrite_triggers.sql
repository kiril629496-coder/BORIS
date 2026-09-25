-- OWNER_EMAIL_SINGLE_WRITER_V1
-- Migration 050 owns canonical state through declarative constraints.
-- Remove legacy rewrite triggers so there is no hidden second writer.

DROP TRIGGER IF EXISTS trg_owner_email_bootstrap_guard_v1 ON prospect_outreach_bootstrap;
DROP TRIGGER IF EXISTS trg_owner_email_campaign_mailbox_guard_v1 ON prospect_campaigns;

-- Recover only pauses created by mailbox transport safety. Manual pauses and
-- unrelated safety reasons are intentionally preserved.
UPDATE prospect_campaigns
SET status='active',
    paused_at=NULL,
    status_reason=NULL,
    status_source='canonical_transport_migration_051',
    updated_at=NOW()
WHERE id IN (9,22)
  AND desired_status='active'
  AND status='paused'
  AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked','mailbox_transport_recovery');

COMMENT ON CONSTRAINT client_mailboxes_owner_transport_canonical_v1 ON client_mailboxes
  IS 'Single-writer owner transport invariant; legacy rewrite triggers removed by migration 051';
