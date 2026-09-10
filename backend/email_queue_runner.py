"""
Воркер очереди писем BORIS AI. Запускается из cron раз в минуту.

Забирает из email_queue письма со статусом queued, у которых подошло время
следующей попытки, и пробует отправить. Захват через FOR UPDATE SKIP LOCKED,
поэтому два одновременных запуска не возьмут одно письмо.

В лог пишет только когда что-то произошло — иначе файл распухнет от пустых строк.
"""

import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, "/root/BORIS/backend")

from dotenv import load_dotenv

load_dotenv("/root/BORIS/backend/.env")

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    from app.services import email_queue as queue
    queue.touch_heartbeat()

    # Independent DB-final safety boundary for owner cold outreach.
    # It is repaired before any prospect feeder or SMTP queue processing.
    try:
        from app.services import owner_outreach_volume_safety
        volume_safety=owner_outreach_volume_safety.ensure()
        if bool((volume_safety or {}).get("changed")):
            print("owner outreach volume safety repaired:", volume_safety)
    except Exception as exc:
        logging.exception("owner outreach volume safety preflight failed: %s", exc)
        # Fail closed for this worker cycle. The timer retries next minute.
        # Sending without the DB-final guard would make the owner an operator.
        return 2

    try:
        from app.services import prospect_bootstrap
        prospect_bootstrap.tick()
    except Exception as exc:
        logging.warning("prospect bootstrap failed (%s)", type(exc).__name__)
    # Repair SMTP auth/connectivity before the feeder makes a send decision.
    # LOGIN/NOOP only: no test message is sent.
    try:
        from app.services import client_mailboxes
        smtp_recheck=client_mailboxes.recheck_unhealthy_smtp(limit=20,cooldown_minutes=15)
        if int((smtp_recheck or {}).get("checked") or 0)>0:
            print("mailbox smtp recheck:", smtp_recheck)
    except Exception as exc:
        logging.warning("mailbox smtp recheck failed (%s)", type(exc).__name__)
    # Reuse this existing minute worker as campaign feeder; active campaigns only.
    # This is deliberately not a second scheduler.
    try:
        from app.services import prospect_campaigns
        repair_result=prospect_campaigns.repair_known_recipient_refusals(limit=50)
        if int((repair_result or {}).get("repaired") or 0)>0:
            print("prospect recipient refusals repaired:", repair_result)
        campaign_result=prospect_campaigns.tick_all(max_campaigns=20, max_enqueue_each=10)
        interesting={cid:res for cid,res in (campaign_result or {}).items()
                     if int((res or {}).get('queued') or 0)>0
                     or str((res or {}).get('status') or '') not in {'ok','paced','outside_window','owner_daily_limit','daily_limit'}}
        if interesting:
            print("prospect campaigns:", interesting)
    except Exception as exc:
        logging.exception("prospect campaigns tick failed: %s", exc)
    try:
        from app.services import client_mailboxes
        client_mailboxes.poll_all(limit=50)
        client_mailboxes.flush_sent_copies(limit=20)
    except Exception as exc:
        logging.warning("client mailboxes poll failed (%s)", type(exc).__name__)
    # Process due outbound mail before any slow discovery/replenishment work.
    # Prospecting may crawl external websites for tens of seconds and must never
    # delay a ready email or IMAP lifecycle step.
    result = queue.process_batch()
    try:
        from app.services import client_mailboxes
        alert_reconcile=client_mailboxes.reconcile_reply_alert_emails(limit=200)
        if int((alert_reconcile or {}).get("changed") or 0)>0:
            print("prospect reply alerts reconciled:", alert_reconcile)
    except Exception as exc:
        logging.warning("prospect reply alert reconcile failed (%s)", type(exc).__name__)
    try:
        from app.services import prospect_reporting
        mailbox_alert=prospect_reporting.report_mailbox_action_required()
        if int((mailbox_alert or {}).get("sent") or 0)>0 or int((mailbox_alert or {}).get("resolved") or 0)>0:
            print("mailbox owner-action alerts:", mailbox_alert)
        prospect_reporting.report_new_sends()
        prospect_reporting.maybe_daily_report()
    except Exception as exc:
        logging.warning("prospect reporting failed (%s)", type(exc).__name__)
    try:
        from app.services import prospect_replenisher
        # Keep a reserve above the visible 100-contact working target so normal
        # daily sending does not immediately drain the campaign below readiness.
        buffer_target=max(100,int(os.getenv('PROSPECT_READY_BUFFER_TARGET','120') or 120))
        prospect_replenisher.tick(min_ready=buffer_target,every_hours=3,repair_every_minutes=15)
    except Exception as exc:
        logging.warning("prospect replenisher failed (%s)", type(exc).__name__)
    if any(result.values()):
        print("%s очередь писем: %s" % (datetime.now().strftime("%d.%m %H:%M"), result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
