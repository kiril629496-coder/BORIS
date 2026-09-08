# DB Session disconnect self-heal — 2026-09-08

Production incident: /api/auth/login completed its business work, then
Session.close() attempted rollback on a PostgreSQL SSL connection that had
already been closed. Cleanup raised OperationalError and converted the request
into an ASGI 500.

This scoped patch:
- makes SessionLocal use ResilientSession;
- suppresses only confirmed physical-disconnect errors during close cleanup;
- disposes the affected process pool so future checkouts reconnect cleanly;
- logs DB_SESSION_CLOSE_DISCONNECT_RECOVERED without SQL or credentials;
- keeps non-disconnect DBAPI errors visible;
- adds app/db/session.py and app/db/base.py to background-worker generation
  tracking so deploy-owner restarts stale workers automatically;
- adds regression tests.

Full focused MOP/DB regression at capture time: 70/70 PASS.

The GitHub repository still does not contain the full production MOP backend
baseline, so this branch stores only the scoped patch rather than copying whole
production files and mixing parallel workstreams.
