from app.services import prospect_campaigns as pc


class _Result:
    def __init__(self, rows=None, first=None, rowcount=0):
        self._rows = rows or []
        self._first = first
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._first


class _Session:
    def __init__(self, campaign_rows=None, health=None, writes=None):
        self.campaign_rows = campaign_rows or []
        self.health = health
        self.writes = writes if writes is not None else []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "FROM prospect_campaigns WHERE" in sql and "desired_status='active'" in sql:
            return _Result(rows=self.campaign_rows)
        if "SELECT smtp_last_error,imap_last_error FROM client_mailboxes" in sql:
            return _Result(first=self.health)
        if "UPDATE prospect_campaigns SET status='active'" in sql:
            self.writes.append((sql, params or {}))
            return _Result(rowcount=1)
        return _Result()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_unexplained_pause_is_self_healed_when_mailbox_is_healthy(monkeypatch):
    writes = []
    campaign = {
        "id": 9,
        "owner_id": 2,
        "mailbox_id": 2,
        "account_id": None,
        "subject_template": "subject",
        "body_template": "body",
        "ab_variants": [],
        "attachment_path": None,
    }
    sessions = [
        _Session(campaign_rows=[campaign], writes=writes),
        _Session(health={"smtp_last_error": None, "imap_last_error": None}, writes=writes),
        _Session(writes=writes),
    ]
    monkeypatch.setattr(pc, "ensure_schema", lambda db: None)
    monkeypatch.setattr(pc, "SessionLocal", lambda: sessions.pop(0))

    out = pc.reconcile_desired_campaign_states()

    assert out == {"checked": 1, "resumed": [9], "blocked": {}}
    assert len(writes) == 1


def test_manual_or_safety_pause_is_not_a_self_heal_candidate(monkeypatch):
    writes = []
    session = _Session(campaign_rows=[], writes=writes)
    monkeypatch.setattr(pc, "ensure_schema", lambda db: None)
    monkeypatch.setattr(pc, "SessionLocal", lambda: session)

    out = pc.reconcile_desired_campaign_states()

    assert out == {"checked": 0, "resumed": [], "blocked": {}}
    assert writes == []
