-- OWNER_EMAIL_CANONICAL_TRANSPORT_V1
-- One production source of truth for owner outreach transport.
-- Runtime workers must not create/replace schema objects.

-- Remove the legacy transport-rewrite trigger. The canonical invariant below
-- is declarative and cannot silently rewrite a row to another provider.
DROP TRIGGER IF EXISTS trg_owner_mailbox_transport_guard_v1 ON client_mailboxes;
DROP FUNCTION IF EXISTS boris_owner_mailbox_transport_guard_v1();

-- Canonical live transport selected by the owner.
UPDATE client_mailboxes
SET email_address='noreply@boris-ai.pro',
    smtp_host='smtp.yandex.ru',
    smtp_port=465,
    smtp_ssl=TRUE,
    imap_host='imap.yandex.ru',
    imap_port=993,
    imap_ssl=TRUE,
    username='noreply@boris-ai.pro',
    smtp_last_error=NULL,
    imap_last_error=NULL,
    last_error=NULL,
    updated_at=NOW()
WHERE account_id='__owner_outreach__';

-- Only one owner-outreach mailbox row may exist for an owner.
CREATE UNIQUE INDEX IF NOT EXISTS uq_client_mailboxes_owner_outreach_single_v1
ON client_mailboxes(owner_user_id,account_id)
WHERE account_id='__owner_outreach__';

ALTER TABLE client_mailboxes
  DROP CONSTRAINT IF EXISTS client_mailboxes_owner_transport_canonical_v1;
ALTER TABLE client_mailboxes
  ADD CONSTRAINT client_mailboxes_owner_transport_canonical_v1 CHECK (
    account_id <> '__owner_outreach__'
    OR (
      lower(email_address)='noreply@boris-ai.pro'
      AND lower(smtp_host)='smtp.yandex.ru'
      AND smtp_port=465
      AND smtp_ssl IS TRUE
      AND lower(imap_host)='imap.yandex.ru'
      AND imap_port=993
      AND imap_ssl IS TRUE
      AND lower(username)='noreply@boris-ai.pro'
      AND status='active'
    )
  );

-- Bootstrap must describe the same mailbox, never a retired provider.
UPDATE prospect_outreach_bootstrap
SET mailbox_id=2,
    email_address='noreply@boris-ai.pro',
    state=CASE WHEN state IN ('active','self_sent') THEN state ELSE 'self_sent' END,
    last_error=NULL,
    updated_at=NOW()
WHERE owner_id=2;

ALTER TABLE prospect_outreach_bootstrap
  DROP CONSTRAINT IF EXISTS prospect_outreach_bootstrap_owner_canonical_v1;
ALTER TABLE prospect_outreach_bootstrap
  ADD CONSTRAINT prospect_outreach_bootstrap_owner_canonical_v1 CHECK (
    owner_id <> 2
    OR (mailbox_id=2 AND lower(email_address)='noreply@boris-ai.pro')
  );

-- The two production owner campaigns stay attached to the canonical mailbox.
UPDATE prospect_campaigns
SET mailbox_id=2,
    status=CASE
      WHEN desired_status='active' AND status='paused' AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked') THEN 'active'
      ELSE status
    END,
    paused_at=CASE
      WHEN desired_status='active' AND status='paused' AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked') THEN NULL
      ELSE paused_at
    END,
    status_reason=CASE
      WHEN desired_status='active' AND status='paused' AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked') THEN NULL
      ELSE status_reason
    END,
    status_source=CASE
      WHEN desired_status='active' AND status='paused' AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked') THEN 'canonical_transport_migration_050'
      ELSE status_source
    END,
    updated_at=NOW()
WHERE id IN (9,22);

ALTER TABLE prospect_campaigns
  DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_mailbox_canonical_v1;
ALTER TABLE prospect_campaigns
  ADD CONSTRAINT prospect_campaigns_owner_mailbox_canonical_v1 CHECK (
    id NOT IN (9,22) OR mailbox_id=2
  );

COMMENT ON CONSTRAINT client_mailboxes_owner_transport_canonical_v1 ON client_mailboxes
  IS 'Owner outreach canonical transport: noreply@boris-ai.pro via Yandex SMTP/IMAP';
COMMENT ON CONSTRAINT prospect_outreach_bootstrap_owner_canonical_v1 ON prospect_outreach_bootstrap
  IS 'Owner bootstrap must bind to canonical mailbox id=2 / noreply@boris-ai.pro';
COMMENT ON CONSTRAINT prospect_campaigns_owner_mailbox_canonical_v1 ON prospect_campaigns
  IS 'Production owner outreach campaigns 9 and 22 must use mailbox id=2';
