from types import SimpleNamespace

from app.api import mop_control


def test_smart_review_catches_risky_and_samples_normal():
    assert mop_control._smart_review("Сколько стоит?", "Уточню цену.", "", 11) is True
    assert mop_control._smart_review("Есть доставка?", "Да, уточним.", "", 12) is True
    assert mop_control._smart_review("Привет", "Добрый день", "", 20) is True  # 10% sample
    assert mop_control._smart_review("Привет", "Добрый день", "", 21) is False


def test_module_visible_when_unpaid(monkeypatch):
    class FakeDb:
        def close(self):
            pass
    monkeypatch.setattr(mop_control, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(mop_control, "_access", lambda db, user, account_id, permission="can_view": True)
    monkeypatch.setattr(mop_control, "_entitlement", lambda account_id: {
        "active": False, "purchased": 0, "used": 0, "left": 0,
        "expired": True, "days_left": 0, "unlimited": False,
    })
    monkeypatch.setattr(mop_control, "_settings", lambda db, account_id: {
        "review_mode": "all", "manual_message_alerts": True, "updated_at": None,
    })
    result = mop_control.status("acc", user=SimpleNamespace(id=1))
    assert result["visible"] is True
    assert result["active"] is False
    assert result["state"] == "not_activated"


def test_module_active_when_mop_paid(monkeypatch):
    class ScalarResult:
        def scalar(self):
            return 3

    class FakeDb:
        def execute(self, *args, **kwargs):
            return ScalarResult()
        def close(self):
            pass

    monkeypatch.setattr(mop_control, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(mop_control, "_access", lambda db, user, account_id, permission="can_view": True)
    monkeypatch.setattr(mop_control, "_entitlement", lambda account_id: {
        "active": True, "purchased": 2000, "used": 100, "left": 1900,
        "expired": False, "days_left": 20, "unlimited": False,
    })
    monkeypatch.setattr(mop_control, "_settings", lambda db, account_id: {
        "review_mode": "all", "manual_message_alerts": False, "updated_at": None,
    })
    result = mop_control.status("acc", user=SimpleNamespace(id=1))
    assert result["active"] is True
    assert result["state"] == "active"
    assert result["pending_reviews"] == 3


def test_feedback_actions_are_stable_contract():
    assert mop_control.FeedbackBody(account_id="a", draft_id=1, action="approve").action == "approve"
    assert mop_control.SettingsBody(account_id="a", review_mode="smart").review_mode == "smart"


def test_owner_feedback_is_injected_into_dialogue_context(monkeypatch):
    import json
    from app.services import mop_dialogue_brain as brain

    account_payload = {
        "summary": {"messages": 20, "chats": 5, "human_replies": 10, "mop_replies_excluded": 2},
        "style_profile": {"preferred_length": "short", "median_chars": 30, "typical_sentences": 1},
        "intent_profiles": [],
        "recommended_sequence": [],
        "question_templates": [],
        "response_patterns": [],
        "handoff_patterns": [],
    }

    class Result:
        def __init__(self, first_value=None, rows=None):
            self._first = first_value
            self._rows = rows or []
        def first(self):
            return self._first
        def fetchall(self):
            return self._rows

    class FakeDb:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "FROM accounts" in sql:
                return Result(("Компания", "Окна", ""))
            if "mop_control_feedback:%" in sql:
                payload = {
                    "action": "correct",
                    "incoming": "Сколько стоит окно?",
                    "reply": "Длинный старый ответ",
                    "correction": "Вам с монтажом?",
                    "comment": "Коротко, как менеджер.",
                }
                return Result(rows=[(json.dumps(payload, ensure_ascii=False),)])
            return Result()
        def close(self):
            pass
        def rollback(self):
            pass

    monkeypatch.setattr(brain, "SessionLocal", lambda: FakeDb())
    monkeypatch.setattr(brain, "ensure_tables", lambda db: None)
    monkeypatch.setattr(
        brain,
        "_payload",
        lambda db, table, key_col, key: account_payload if table == brain.ACCOUNT_TABLE else {},
    )
    monkeypatch.setattr(brain, "niche_key", lambda *args: "steklo")

    ctx = brain.dialogue_context("acc", current_client_text="Сколько стоит окно?", max_chars=6000)
    assert "ОБРАТНАЯ СВЯЗЬ ВЛАДЕЛЬЦА" in ctx
    assert "Вам с монтажом?" in ctx
    assert "Коротко, как менеджер." in ctx
