# BORIS SEO Checkpoint Center standalone UI

Authoritative source for the isolated exact-route /seo-checkpoints.

Why isolated:
- shared Next frontend releases are gated by Campaign/Messenger/UASAAAP runtime QA;
- this owner-only SEO operator page must not be rolled back by unrelated frontend failures;
- business logic remains on owner-protected /seo-checkpoints-data, /seo-checkpoints-extension-config, and /seo-checkpoints-complete.

Deploy:
1. edit source here;
2. DRY_RUN=1 ./deploy.sh;
3. sudo ./deploy.sh;
4. run live browser acceptance.

CAPTCHA/Terms/identity protections must never be automated.
