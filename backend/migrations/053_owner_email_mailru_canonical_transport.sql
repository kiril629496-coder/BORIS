-- OWNER_EMAIL_MAILRU_CANONICAL_V1
-- Supersedes the historical Yandex canonical transport from migration 050.
-- Migration 050 remains immutable; this migration is the legal forward change.

DROP TRIGGER IF EXISTS trg_owner_mailbox_transport_guard_v1 ON client_mailboxes;
DROP TRIGGER IF EXISTS trg_owner_email_bootstrap_guard_v1 ON prospect_outreach_bootstrap;
DROP TRIGGER IF EXISTS trg_owner_email_campaign_mailbox_guard_v1 ON prospect_campaigns;

ALTER TABLE prospect_outreach_bootstrap
  DROP CONSTRAINT IF EXISTS prospect_outreach_bootstrap_owner_canonical_v1;
ALTER TABLE client_mailboxes
  DROP CONSTRAINT IF EXISTS client_mailboxes_owner_transport_canonical_v1;
ALTER TABLE prospect_campaigns
  DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_mailbox_canonical_v1;

UPDATE client_mailboxes
SET email_address='eliseev-ko@mail.ru',
    smtp_host='smtp.mail.ru',
    smtp_port=587,
    smtp_ssl=FALSE,
    imap_host='imap.mail.ru',
    imap_port=993,
    imap_ssl=TRUE,
    username='eliseev-ko@mail.ru',
    status='active',
    smtp_last_error=NULL,
    imap_last_error=NULL,
    last_error=NULL,
    updated_at=NOW()
WHERE id=2 AND account_id='__owner_outreach__';

UPDATE prospect_outreach_bootstrap
SET mailbox_id=2,
    email_address='eliseev-ko@mail.ru',
    last_error=NULL,
    updated_at=NOW()
WHERE owner_id=2;

UPDATE prospect_campaigns
SET mailbox_id=2,updated_at=NOW()
WHERE id IN (9,22);

CREATE UNIQUE INDEX IF NOT EXISTS uq_client_mailboxes_owner_outreach_single_v1
ON client_mailboxes(owner_user_id,account_id)
WHERE account_id='__owner_outreach__';

ALTER TABLE client_mailboxes
  ADD CONSTRAINT client_mailboxes_owner_transport_canonical_v1 CHECK (
    account_id <> '__owner_outreach__'
    OR (
      id=2
      AND lower(email_address)='eliseev-ko@mail.ru'
      AND lower(smtp_host)='smtp.mail.ru'
      AND smtp_port=587
      AND smtp_ssl IS FALSE
      AND lower(imap_host)='imap.mail.ru'
      AND imap_port=993
      AND imap_ssl IS TRUE
      AND lower(username)='eliseev-ko@mail.ru'
      AND status='active'
    )
  );

ALTER TABLE prospect_outreach_bootstrap
  ADD CONSTRAINT prospect_outreach_bootstrap_owner_canonical_v1 CHECK (
    owner_id <> 2
    OR (mailbox_id=2 AND lower(email_address)='eliseev-ko@mail.ru')
  );

ALTER TABLE prospect_campaigns
  ADD CONSTRAINT prospect_campaigns_owner_mailbox_canonical_v1 CHECK (
    id NOT IN (9,22) OR mailbox_id=2
  );

COMMENT ON CONSTRAINT client_mailboxes_owner_transport_canonical_v1 ON client_mailboxes
  IS 'Owner outreach canonical transport superseded by migration 053: eliseev-ko@mail.ru via Mail.ru SMTP 587 STARTTLS / IMAP 993 SSL';
COMMENT ON CONSTRAINT prospect_outreach_bootstrap_owner_canonical_v1 ON prospect_outreach_bootstrap
  IS 'Owner bootstrap canonical mailbox after migration 053: mailbox id=2 / eliseev-ko@mail.ru';
COMMENT ON CONSTRAINT prospect_campaigns_owner_mailbox_canonical_v1 ON prospect_campaigns
  IS 'Production owner outreach campaigns 9 and 22 use canonical mailbox id=2';
