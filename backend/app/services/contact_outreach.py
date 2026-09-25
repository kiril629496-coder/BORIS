"""BORIS reusable B2B contact outreach facade.
Combines public-site contact prospecting with the canonical idempotent email queue.
No scheduler and no second mail engine.
"""
from __future__ import annotations
import hashlib
from app.services.contact_prospector import extract_company_contacts
from app.services.email_queue import enqueue_email

def prospect(url: str, max_pages: int = 4) -> dict:
    return extract_company_contacts(url, max_pages=max_pages)

def send_one(*, company: str, email: str, subject: str, body: str,
             attachment_path: str | None = None, campaign: str = "manual_b2b") -> dict:
    key = "outreach:%s:%s" % (campaign, hashlib.sha256(email.strip().lower().encode()).hexdigest()[:24])
    attachments = None
    if attachment_path:
        attachments = [{"path": attachment_path, "filename": attachment_path.rsplit('/',1)[-1], "mime": "application/pdf"}]
    return enqueue_email(
        email, subject, body, source="contact_outreach", idempotency_key=key,
        send_now=True, attachments=attachments,
    )
