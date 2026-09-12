#!/usr/bin/env bash
set -euo pipefail
# OWNER_EMAIL_RUNTIME_PREFLIGHT_V4
/usr/sbin/runuser -u postgres -- /usr/bin/psql boris_db -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout='8s';
DO $check$
DECLARE bad integer;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.client_mailboxes'::regclass AND conname='client_mailboxes_owner_transport_canonical_v1' AND convalidated AND pg_get_constraintdef(oid) LIKE '%eliseev-ko@mail.ru%' AND pg_get_constraintdef(oid) LIKE '%smtp.mail.ru%' AND pg_get_constraintdef(oid) LIKE '%imap.mail.ru%') THEN RAISE EXCEPTION 'OWNER_EMAIL_CANONICAL_CONSTRAINT_MISSING'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.prospect_outreach_bootstrap'::regclass AND conname='prospect_outreach_bootstrap_owner_canonical_v1' AND convalidated AND pg_get_constraintdef(oid) LIKE '%eliseev-ko@mail.ru%') THEN RAISE EXCEPTION 'OWNER_EMAIL_BOOTSTRAP_CONSTRAINT_MISSING'; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.prospect_campaigns'::regclass AND conname='prospect_campaigns_owner_mailbox_canonical_v1' AND convalidated) THEN RAISE EXCEPTION 'OWNER_EMAIL_CAMPAIGN_CONSTRAINT_MISSING'; END IF;
  SELECT count(*) INTO bad FROM client_mailboxes WHERE account_id='__owner_outreach__' AND NOT (id=2 AND lower(email_address)='eliseev-ko@mail.ru' AND lower(smtp_host)='smtp.mail.ru' AND smtp_port=587 AND smtp_ssl IS FALSE AND lower(imap_host)='imap.mail.ru' AND imap_port=993 AND imap_ssl IS TRUE AND lower(username)='eliseev-ko@mail.ru' AND status='active');
  IF bad<>0 OR (SELECT count(*) FROM client_mailboxes WHERE account_id='__owner_outreach__')<>1 THEN RAISE EXCEPTION 'OWNER_EMAIL_MAILBOX_DRIFT'; END IF;
  IF NOT EXISTS (SELECT 1 FROM prospect_outreach_bootstrap WHERE owner_id=2 AND mailbox_id=2 AND lower(email_address)='eliseev-ko@mail.ru') THEN RAISE EXCEPTION 'OWNER_EMAIL_BOOTSTRAP_DRIFT'; END IF;
  IF EXISTS (SELECT 1 FROM prospect_campaigns WHERE id IN (9,22) AND mailbox_id IS DISTINCT FROM 2) THEN RAISE EXCEPTION 'OWNER_EMAIL_CAMPAIGN_MAILBOX_DRIFT'; END IF;
  IF EXISTS (SELECT 1 FROM pg_trigger WHERE tgname IN ('trg_owner_mailbox_transport_guard_v1','trg_owner_email_bootstrap_guard_v1','trg_owner_email_campaign_mailbox_guard_v1') AND NOT tgisinternal) THEN RAISE EXCEPTION 'OWNER_EMAIL_LEGACY_REWRITE_TRIGGER_PRESENT'; END IF;
END $check$;
COMMIT;
SQL
