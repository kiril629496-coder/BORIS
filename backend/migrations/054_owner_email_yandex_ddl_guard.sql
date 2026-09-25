-- OWNER_EMAIL_YANDEX_CANONICAL_V3
-- Supersedes migration 053. Canonical owner outreach transport is Yandex.
-- Restores the owner-approved Yandex canonical transport after migration 053.
-- DDL protection is installed separately by a postgres-owned root bootstrap.

ALTER TABLE prospect_outreach_bootstrap
  DROP CONSTRAINT IF EXISTS prospect_outreach_bootstrap_owner_canonical_v1;
ALTER TABLE client_mailboxes
  DROP CONSTRAINT IF EXISTS client_mailboxes_owner_transport_canonical_v1;
ALTER TABLE prospect_campaigns
  DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_mailbox_canonical_v1;

UPDATE client_mailboxes
SET email_address='noreply@boris-ai.pro',
    smtp_host='smtp.yandex.ru',
    smtp_port=465,
    smtp_ssl=TRUE,
    imap_host='imap.yandex.ru',
    imap_port=993,
    imap_ssl=TRUE,
    username='noreply@boris-ai.pro',
    status='active',
    smtp_last_error=NULL,
    imap_last_error=NULL,
    last_error=NULL,
    updated_at=NOW()
WHERE id=2 AND account_id='__owner_outreach__';

UPDATE prospect_outreach_bootstrap
SET mailbox_id=2,
    email_address='noreply@boris-ai.pro',
    last_error=NULL,
    updated_at=NOW()
WHERE owner_id=2;

UPDATE prospect_campaigns
SET mailbox_id=2,
    status=CASE
      WHEN desired_status='active'
       AND status='paused'
       AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked','mailbox_transport_recovery')
      THEN 'active' ELSE status END,
    paused_at=CASE
      WHEN desired_status='active'
       AND status='paused'
       AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked','mailbox_transport_recovery')
      THEN NULL ELSE paused_at END,
    status_reason=CASE
      WHEN desired_status='active'
       AND status='paused'
       AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked','mailbox_transport_recovery')
      THEN NULL ELSE status_reason END,
    status_source=CASE
      WHEN desired_status='active'
       AND status='paused'
       AND status_reason IN ('mailbox_recovery','mailbox_auth_blocked','mailbox_transport_recovery')
      THEN 'canonical_transport_migration_054' ELSE status_source END,
    updated_at=NOW()
WHERE id IN (9,22);

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_mailboxes_owner_outreach_single_v1
ON client_mailboxes(owner_user_id,account_id)
WHERE account_id='__owner_outreach__';

ALTER TABLE client_mailboxes
  ADD CONSTRAINT client_mailboxes_owner_transport_canonical_v1 CHECK (
    account_id <> '__owner_outreach__'
    OR (
      id=2
      AND lower(email_address)='noreply@boris-ai.pro'
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

ALTER TABLE prospect_outreach_bootstrap
  ADD CONSTRAINT prospect_outreach_bootstrap_owner_canonical_v1 CHECK (
    owner_id <> 2
    OR (mailbox_id=2 AND lower(email_address)='noreply@boris-ai.pro')
  );

ALTER TABLE prospect_campaigns
  ADD CONSTRAINT prospect_campaigns_owner_mailbox_canonical_v1 CHECK (
    id NOT IN (9,22) OR mailbox_id=2
  );

COMMENT ON CONSTRAINT client_mailboxes_owner_transport_canonical_v1 ON client_mailboxes
  IS 'Owner outreach canonical transport: noreply@boris-ai.pro via Yandex SMTP 465 SSL / IMAP 993 SSL; protected by migration 054 DDL guard';
COMMENT ON CONSTRAINT prospect_outreach_bootstrap_owner_canonical_v1 ON prospect_outreach_bootstrap
  IS 'Owner bootstrap canonical mailbox: id=2 / noreply@boris-ai.pro';
COMMENT ON CONSTRAINT prospect_campaigns_owner_mailbox_canonical_v1 ON prospect_campaigns
  IS 'Owner outreach campaigns 9 and 22 must use canonical mailbox id=2';

