from app.services import prospect_campaigns as pc


class _Rows:
    def __init__(self, row=None):
        self.row=row
    def mappings(self):
        return self
    def first(self):
        return self.row


class _DB:
    def __init__(self):
        self.update_sql=""
        self.update_params={}
    def execute(self, statement, params=None):
        sql=str(statement)
        if "FROM email_queue WHERE idempotency_key" in sql:
            return _Rows({
                "id": 3985,
                "status": "sent",
                "created_at": "2026-10-07T06:24:40Z",
                "sent_at": "2026-10-07T06:24:47Z",
                "last_error": None,
            })
        if "UPDATE prospect_campaign_members SET status" in sql:
            self.update_sql=sql
            self.update_params=dict(params or {})
            return _Rows()
        raise AssertionError(sql)


def test_reconcile_sent_queue_accepts_queued_member_state():
    db=_DB()
    ok=pc._reconcile_existing_queue(
        db,9,{"id":42188},
        key="prospect_campaign:9:qa:v1",
        copy_version="v1",
        ab_label="A",
    )
    assert ok is True
    assert "status IN ('ready','queued','failed')" in db.update_sql
    assert db.update_params["st"] == "sent"
    assert db.update_params["sa"] == "2026-10-07T06:24:47Z"
    assert db.update_params["q"] == 3985
