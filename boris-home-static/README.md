# BORIS static homepage source

Canonical static homepage source for the exact `https://boris-ai.pro/` landing.

- Source: `index.source.html`
- Live file: `/var/www/boris-home/index.html`
- Check: `python3 check.py`
- Deploy: `sudo bash deploy.sh`

The deploy script replaces only `index.html`. It does not touch screenshots,
images, the IndexNow key file, nginx, the Next.js application or any other BORIS
workstream.

Required production invariants:
- one self-canonical to https://boris-ai.pro/
- BORIS SEO title/description/schema
- Yandex Metrika counter 111297252
- boris_primary_cta and boris_lead_form_submit goals
- Avito + CRM + AI sales commercial section
