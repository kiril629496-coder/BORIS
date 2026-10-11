from types import SimpleNamespace

from app.api import viral


def test_public_market_payload_is_client_allowlisted():
    raw = {
        "status": "partial",
        "market_score": {"total": 61, "label": "Есть потенциал", "confidence": "medium", "debug": "secret"},
        "demand": {
            "level": "Высокий",
            "total_count": 552,
            "region_scope": "Уфа",
            "top_requests": [{"phrase": "кухни на заказ", "count": 123, "raw": "do-not-leak"}],
            "provider_payload": {"token": "secret"},
        },
        "competitors": {
            "direct_sample": 4,
            "estimated_competitors": 12,
            "competing_offers_sample": 18,
            "relevant_ads_sample": 9,
            "count_basis": "unique_sellers",
            "sample_ads": [{"seller_phone": "+70000000000"}],
        },
        "recommended_ads": {"low": 21, "high": 26, "grounded": True, "competition_grounded": True},
        "positioning": {"ideas_to_test": ["Быстрый расчёт"], "internal_prompt": "secret"},
        "transport": {"proxy": "secret"},
    }
    out = viral._public_market_payload(raw)
    assert out["demand"]["total_count"] == 552
    assert out["demand"]["top_requests"] == [{"phrase": "кухни на заказ", "count": 123}]
    assert out["recommended_ads"]["low"] == 21
    assert "provider_payload" not in out["demand"]
    assert "sample_ads" not in out["competitors"]
    assert "transport" not in out
    assert "debug" not in out["market_score"]


def test_client_data_status_is_partial_without_blocking(monkeypatch):
    monkeypatch.setattr(
        viral,
        "_storage_get",
        lambda account_id, key: {"status": "ANALYSIS_RUNNING"} if key == "market_job:vm_x" else None,
    )
    out = viral._client_data_status("acc", "vm_x", None)
    assert out["state"] == "PARTIAL_READY"
    assert out["progress_percent"] == 80
    assert {x["code"]: x["state"] for x in out["sections"]}["demand"] == "loading"


def test_client_data_status_fails_partial_not_whole_audit(monkeypatch):
    monkeypatch.setattr(
        viral,
        "_storage_get",
        lambda account_id, key: {"status": "ERROR"} if key == "market_job:vm_x" else None,
    )
    out = viral._client_data_status("acc", "vm_x", None)
    assert out["state"] == "FAILED_PARTIAL"
    assert out["progress_percent"] == 80
    assert {x["code"]: x["state"] for x in out["sections"]}["advertising"] == "unavailable"


def test_client_payload_uses_plain_language_and_no_internal_fields():
    audit = {
        "score": {"total": 61, "label": "Есть потенциал"},
        "loss": {
            "estimated": {"lost_revenue_low": 70000, "lost_revenue_high": 130000, "money_available": True},
            "disclaimer": "Оценка, не подтверждённая выручка.",
            "confirmed": {"dialogs_period": 12, "answered_dialogs": 10},
        },
        "reasons": [
            {"kind": "unanswered", "value": 2, "text": "обращения требуют ответа"},
            {"kind": "reactivation", "value": 3, "text": "клиентов можно вернуть"},
        ],
        "owner_account_id": "never-public",
        "sources": [{"proxy": "never-public"}],
    }
    market = {
        "demand": {
            "total_count": 552,
            "level": "Высокий",
            "region_scope": "Уфа",
            "top_requests": [{"phrase": "кухни на заказ", "count": 123}],
        },
        "recommended_ads": {"low": 21, "high": 26, "grounded": True, "competition_grounded": True},
        "summary": "Спрос есть; стоит увеличить охват.",
    }
    status = {"state": "READY", "progress_percent": 100, "sections": []}
    out = viral._client_payload(
        audit,
        company={"name": "Планета Мебели"},
        market=market,
        data_status=status,
        referral_code="btest",
    )
    assert out["company"]["name"] == "Планета Мебели"
    assert out["growth_score"]["total"] == 61
    assert out["advertising"]["recommended_low"] == 21
    assert out["referral"]["path"] == "/r/btest"
    assert out["loss_points"][0]["title"] == "Есть обращения без ответа"
    assert "kind" not in out["loss_points"][0]
    assert "owner_account_id" not in out
    assert "sources" not in out
    rendered = str(out)
    assert "Market Score" not in rendered
    assert "Money Loss Engine" not in rendered
    assert "proxy" not in rendered


def test_public_report_never_exposes_owner_or_raw_reason_fields(monkeypatch):
    now = viral._now()
    stored = {
        "version": 2,
        "token": "safe-token",
        "created_at": now.isoformat(),
        "expires_at": (now + viral.timedelta(days=1)).isoformat(),
        "revoked_at": None,
        "score": {"total": 61, "label": "Есть потенциал"},
        "loss": {
            "estimated": {"recoverable_leads": 2, "lost_revenue_low": 1000, "lost_revenue_high": 2000},
            "assumptions": {"average_check_rub": 999999},
            "confirmed": {"dialogs_period": 999},
            "disclaimer": "Оценка.",
        },
        "reasons": [{"kind": "unanswered", "value": 2, "text": "Два обращения требуют внимания"}],
        "company": {"name": "Тестовая компания"},
        "referral_code": "safe-ref",
        "_owner_account_id": "acc-secret",
        "_market_audit_id": "",
        "internal_debug": {"proxy": "secret"},
        "cta": {"label": "Проверить свой бизнес", "href": "/r/safe-ref"},
    }

    def fake_storage(account_id, key):
        if account_id == "viral_public" and key == "report:safe-token":
            return stored
        return None

    monkeypatch.setattr(viral, "_storage_get", fake_storage)
    monkeypatch.setattr(viral, "_storage_put", lambda *args, **kwargs: None)
    monkeypatch.setattr(viral, "_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        viral,
        "sales_contact",
        lambda: {
            "status": "ok",
            "contact_name": "Кирилл О.",
            "contact_title": "Разработчик BORIS",
            "whatsapp_url": "https://wa.me/70000000000",
            "max_url": "https://max.ru/test",
            "trial_enabled": True,
            "trial_days": 4,
            "trial_title": "Попробовать BORIS 4 дня бесплатно",
        },
    )
    from fastapi import Response
    req = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    out = viral.public_report("safe-token", req, Response())

    rendered = str(out)
    assert "_owner_account_id" not in out
    assert "_market_audit_id" not in out
    assert "internal_debug" not in out
    assert "acc-secret" not in rendered
    assert "proxy" not in rendered
    assert "loss" not in out
    assert "reasons" not in out
    assert "assumptions" not in rendered
    assert "confirmed" not in rendered
    assert out["money"]["lost_revenue_high"] == 2000
    assert out["loss_points"][0]["title"] == "Есть обращения без ответа"
    assert "kind" not in out["loss_points"][0]
    assert out["contacts"]["contact_name"] == "Кирилл О."
    assert out["contacts"]["contact_title"] == "Разработчик BORIS"
    assert out["contacts"]["trial_title"] == "Попробовать BORIS 4 дня бесплатно"


def test_referral_reward_is_idempotent_for_same_referred_user(monkeypatch):
    import json
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.models.storage import Storage

    fixed_now = datetime(2026, 10, 6, 7, 0, tzinfo=timezone.utc)
    referred = SimpleNamespace(
        id=20,
        referred_by="referrer@example.com",
        email_verified=True,
        status="active",
        account_id="new-client",
    )
    referrer = SimpleNamespace(
        id=10,
        email="referrer@example.com",
        account_id="referrer-account",
        subscription_expires_at=None,
        ref_code="bref",
    )
    state = {"ledger_row": None, "locks": []}

    class FakeQuery:
        def __init__(self, db, model):
            self.db = db
            self.model = model
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def first(self):
            if self.model is viral.User:
                self.db.user_reads += 1
                return referred if self.db.user_reads == 1 else referrer
            if self.model is Storage:
                return state["ledger_row"]
            return None

    class FakeDb:
        def __init__(self):
            self.user_reads = 0
        def execute(self, statement, params=None):
            state["locks"].append(str(statement))
            return SimpleNamespace()
        def query(self, model):
            return FakeQuery(self, model)
        def add(self, row):
            if isinstance(row, Storage):
                state["ledger_row"] = row
        def commit(self):
            return None
        def rollback(self):
            return None
        def expire_all(self):
            return None
        def close(self):
            return None

    extend_calls = []
    monkeypatch.setattr(viral, "SessionLocal", FakeDb)
    monkeypatch.setattr(viral, "_now", lambda: fixed_now)
    monkeypatch.setattr(
        viral,
        "_extend_referral_base_access",
        lambda account_id, owner_user_id, target, reward_id: extend_calls.append(
            (account_id, owner_user_id, target, reward_id)
        ),
    )

    from app.services import subscription_gate
    monkeypatch.setattr(
        subscription_gate,
        "base_subscription_access",
        lambda db, account_id: {
            "allowed": True,
            "state": "unknown_allowed",
            "paid_until": None,
        },
    )

    first = viral._maybe_grant_referral_reward(20, "account_connected")
    second = viral._maybe_grant_referral_reward(20, "account_connected")

    assert first["status"] == "granted"
    assert second["status"] == "already_granted"
    assert first["reward"]["reward_value"] == 7
    assert second["reward"]["reward_value"] == 7
    assert len(extend_calls) == 1
    assert extend_calls[0][0] == "referrer-account"
    assert extend_calls[0][1] == 10
    assert (extend_calls[0][2] - fixed_now).days == 7
    assert state["ledger_row"] is not None
    saved = json.loads(state["ledger_row"].value)
    assert saved["status"] == "GRANTED"
    assert saved["referred_user_id"] == 20
    assert any("pg_advisory_xact_lock" in x for x in state["locks"])


def test_referral_reward_is_idempotent(monkeypatch):
    import app.services.subscription_gate as subscription_gate

    referred = SimpleNamespace(
        id=202,
        referred_by="owner@example.com",
        email_verified=True,
        status="active",
        account_id="referred-account",
    )
    referrer = SimpleNamespace(
        id=101,
        email="owner@example.com",
        account_id="owner-account",
        ref_code="bowner",
        subscription_expires_at=None,
    )
    shared = {"ledger": None, "extend_calls": []}

    class FakeQuery:
        def __init__(self, model, session):
            self.model = model
            self.session = session
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def first(self):
            if self.model is viral.User:
                self.session.user_reads += 1
                return referred if self.session.user_reads == 1 else referrer
            if self.model is viral.Storage:
                return shared["ledger"]
            return None

    class FakeSession:
        def __init__(self):
            self.user_reads = 0
        def execute(self, *args, **kwargs):
            return SimpleNamespace(rowcount=1)
        def query(self, model):
            return FakeQuery(model, self)
        def add(self, row):
            if isinstance(row, viral.Storage):
                shared["ledger"] = row
        def commit(self):
            return None
        def rollback(self):
            return None
        def expire_all(self):
            return None
        def close(self):
            return None

    monkeypatch.setattr(viral, "SessionLocal", FakeSession)
    monkeypatch.setattr(
        subscription_gate,
        "base_subscription_access",
        lambda db, account_id: {"paid_until": None},
    )
    monkeypatch.setattr(
        viral,
        "_extend_referral_base_access",
        lambda account_id, owner_user_id, target, reward_id: shared["extend_calls"].append(
            (account_id, owner_user_id, reward_id)
        ),
    )

    first = viral._maybe_grant_referral_reward(202, "account_connected")
    second = viral._maybe_grant_referral_reward(202, "account_connected")

    assert first["status"] == "granted"
    assert second["status"] == "already_granted"
    assert first["reward"]["reward_value"] == 7
    assert len(shared["extend_calls"]) == 1
    assert shared["extend_calls"][0][0:2] == ("owner-account", 101)


def test_client_data_status_exposes_last_market_update():
    market = {"created_at": "2026-10-06T05:00:00+00:00"}
    out = viral._client_data_status("acc", "vm_x", market)
    assert out["state"] == "READY"
    assert out["progress_percent"] == 100
    assert out["last_updated_at"] == "2026-10-06T05:00:00+00:00"


def test_market_cache_refresh_starts_once_for_stale_cache(monkeypatch):
    old = viral._now() - viral.timedelta(hours=7)
    cached = {"created_at": old.isoformat()}
    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            self.daemon = daemon
            self.name = name
        def start(self):
            started.append({"args": self.args, "name": self.name})

    monkeypatch.setattr(viral.threading, "Thread", FakeThread)
    monkeypatch.setenv("BORIS_MARKET_CACHE_REFRESH_HOURS", "6")
    with viral._MARKET_JOB_LOCK:
        viral._MARKET_JOBS_RUNNING.clear()

    first = viral._start_market_cache_refresh("кухни на заказ", "Уфа", cached)
    second = viral._start_market_cache_refresh("кухни на заказ", "Уфа", cached)

    assert first is True
    assert second is False
    assert len(started) == 1
    assert started[0]["name"].startswith("viral-market-cache-")
    with viral._MARKET_JOB_LOCK:
        viral._MARKET_JOBS_RUNNING.clear()


def test_market_cache_refresh_skips_fresh_cache(monkeypatch):
    fresh = {"created_at": (viral._now() - viral.timedelta(hours=1)).isoformat()}
    monkeypatch.setenv("BORIS_MARKET_CACHE_REFRESH_HOURS", "6")
    assert viral._start_market_cache_refresh("кухни", "Уфа", fresh) is False


def test_market_cache_refresh_failure_keeps_old_cache(monkeypatch):
    cache_id = "market:test-refresh-failure"
    old_cache = {"created_at": "2026-10-06T06:00:00+00:00", "status": "ok", "demand": {"total_count": 111}}
    writes = []

    monkeypatch.setattr(viral, "_storage_put", lambda account_id, key, value: writes.append((account_id, key, value)))

    import app.services.audit_market as audit_market
    monkeypatch.setattr(audit_market, "run_market_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("collector down")))

    with viral._MARKET_JOB_LOCK:
        viral._MARKET_JOBS_RUNNING.add("cache-refresh:" + cache_id)

    viral._run_market_cache_refresh(cache_id, "кухни", "Уфа", "cache-refresh:" + cache_id)

    assert all(not (account_id == "viral_market_cache" and key == cache_id) for account_id, key, value in writes)
    meta = [value for account_id, key, value in writes if account_id == "viral_market_cache_meta" and key == "refresh:" + cache_id]
    assert len(meta) == 1
    assert meta[0]["status"] == "error"
    assert meta[0]["error_type"] == "RuntimeError"
    with viral._MARKET_JOB_LOCK:
        assert "cache-refresh:" + cache_id not in viral._MARKET_JOBS_RUNNING


def test_connected_audit_owner_filter_does_not_drop_accounts_without_slots():
    import inspect
    source = inspect.getsource(viral.connected_audit)
    expected = ":owner_id IS NULL OR item_owner_id IS NULL OR item_owner_id = :owner_id"
    assert source.count(expected) == 2
    assert "SELECT avito_user_id FROM account_slots WHERE account_id=:a LIMIT 1" not in source


def test_referral_reward_rejects_self_referral(monkeypatch):
    same = SimpleNamespace(
        id=303,
        email="self@example.com",
        referred_by="self@example.com",
        email_verified=True,
        status="active",
        account_id="self-account",
        subscription_expires_at=None,
        ref_code="bself",
    )

    class FakeQuery:
        def __init__(self, model):
            self.model = model
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            if self.model is viral.User:
                return same
            return None

    class FakeSession:
        def execute(self, *args, **kwargs):
            return SimpleNamespace(rowcount=1)
        def query(self, model):
            return FakeQuery(model)
        def rollback(self):
            return None
        def close(self):
            return None

    monkeypatch.setattr(viral, "SessionLocal", FakeSession)
    out = viral._maybe_grant_referral_reward(303, "account_connected")
    assert out["status"] == "invalid_referrer"


def test_referral_reward_requires_real_qualification(monkeypatch):
    called = {"session": 0}

    def fake_session():
        called["session"] += 1
        raise AssertionError("SessionLocal must not be called before qualification")

    monkeypatch.setattr(viral, "SessionLocal", fake_session)
    out = viral._maybe_grant_referral_reward(404, "registered")
    assert out["status"] == "not_qualified"
    assert called["session"] == 0


def test_registration_referral_attribution_contract():
    import inspect
    from app.api import auth as auth_api
    source = inspect.getsource(auth_api.register)
    assert 'ref_code = (req.ref or "").strip()' in source
    assert "User.ref_code == ref_code" in source
    assert "referred_by=(manager.email if manager else None)" in source
    assert "referred_at=(datetime.datetime.utcnow() if manager else None)" in source


def test_duplicate_registration_returns_before_new_user_creation():
    import inspect
    from app.api import auth as auth_api
    source = inspect.getsource(auth_api.register)
    existing_pos = source.index("if existing:")
    user_create_pos = source.index("user = User(")
    assert existing_pos < user_create_pos
    duplicate_block = source[existing_pos:user_create_pos]
    assert '"status": "pending"' in duplicate_block


def test_owner_audits_rejects_non_owner():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        viral.owner_audits(period="30d", user=SimpleNamespace(role="client"))
    assert exc.value.status_code == 403


def test_owner_audits_filters_and_returns_register(monkeypatch):
    import json
    from datetime import datetime, timezone

    run = {
        "run_id": "ar_owner_test",
        "audit_id": "vm_owner_test",
        "account_id": "acc-owner-test",
        "kind": "market",
        "status": "ok",
        "result_status": "partial",
        "started_at": "2026-10-06T20:00:00+00:00",
        "finished_at": "2026-10-06T20:00:01+00:00",
        "duration_ms": 1234,
        "query": "Кухни на заказ",
        "region": "Уфа",
        "total_variable_cost_rub": 1.25,
    }
    gateway = {
        "stage": "TRIAL_INTEREST",
        "first_touch": {
            "referral_code": "bowner",
            "utm_campaign": "ocean",
            "utm_content": "post-owner-test",
        },
        "history": [{"mode": "market"}],
        "updated_at": "2026-10-06T20:00:02+00:00",
    }

    class FakeQuery:
        def __init__(self, model):
            self.model = model
        def filter(self, *args, **kwargs):
            return self
        def all(self):
            if self.model is viral.Storage:
                return [SimpleNamespace(account_id="acc-owner-test", value=json.dumps(gateway))]
            if self.model is viral.User:
                return [SimpleNamespace(account_id="acc-owner-test", email="client@example.com")]
            if self.model is viral.Account:
                return [SimpleNamespace(account_id="acc-owner-test", name="Тестовая компания")]
            return []

    class FakeSession:
        def query(self, model):
            return FakeQuery(model)
        def close(self):
            return None

    monkeypatch.setattr(viral, "_audit_run_rows", lambda limit=500: [run])
    monkeypatch.setattr(viral, "SessionLocal", FakeSession)
    monkeypatch.setattr(viral, "_now", lambda: datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc))

    out = viral.owner_audits(
        period="all",
        niche="кухни",
        city="уфа",
        referral="referral",
        source="Referral",
        mode="market",
        readiness="PARTIAL_READY",
        user=SimpleNamespace(role="owner"),
    )

    assert out["status"] == "ok"
    assert out["total"] == 1
    row = out["audits"][0]
    assert row["company"] == "Тестовая компания"
    assert row["user"] == "client@example.com"
    assert row["niche"] == "Кухни на заказ"
    assert row["city"] == "Уфа"
    assert row["readiness"] == "PARTIAL_READY"
    assert row["referral_code"] == "bowner"
    assert row["source"] == "Referral"
    assert row["campaign"] == "ocean"
    assert row["post"] == "post-owner-test"
    assert row["trial_interest"] is True
    assert row["duration_ms"] == 1234


def test_sales_contact_defaults_use_final_trial_copy(monkeypatch):
    monkeypatch.setattr(viral, "_storage_get", lambda *args, **kwargs: {})
    for key in ("BORIS_SALES_WHATSAPP_URL", "BORIS_SALES_MAX_URL", "BORIS_SALES_PHONE"):
        monkeypatch.delenv(key, raising=False)
    out = viral.sales_contact()
    assert out["contact_name"] == "Кирилл О."
    assert out["contact_title"] == "Разработчик BORIS"
    assert out["trial_days"] == 4
    assert out["trial_title"] == "Попробовать BORIS 4 дня бесплатно"
    assert "Поможем подключить BORIS к вашему бизнесу" in out["trial_description"]


def test_owner_ocean_funnel_dedupes_and_excludes_qa(monkeypatch):
    import json

    state = {
        "stage": "TRIAL_INTEREST",
        "audit_completed": True,
        "first_touch": {
            "session_id": "sess-ocean-tg",
            "utm_source": "tg",
            "utm_campaign": "ocean",
            "utm_content": "post-0017",
            "captured_at": "2026-10-08T08:00:00+00:00",
            "qa": False,
        },
        "history": [{"mode": "connected"}],
    }
    real_events = [
        {"event": "audit_view", "session_id": "sess-ocean-tg", "utm_source": "tg", "utm_campaign": "ocean", "utm_content": "post-0017", "created_at": "2026-10-08T08:00:00+00:00"},
        {"event": "audit_start", "session_id": "sess-ocean-tg", "utm_source": "tg", "utm_campaign": "ocean", "utm_content": "post-0017", "created_at": "2026-10-08T08:01:00+00:00"},
        {"event": "registration_completed", "session_id": "sess-ocean-tg", "account_id": "acc-ocean", "utm_source": "tg", "utm_campaign": "ocean", "utm_content": "post-0017", "created_at": "2026-10-08T08:02:00+00:00"},
        {"event": "audit_result_view", "session_id": "sess-ocean-tg", "account_id": "acc-ocean", "utm_source": "tg", "utm_campaign": "ocean", "utm_content": "post-0017", "created_at": "2026-10-08T08:03:00+00:00"},
        {"event": "trial_cta_click", "session_id": "sess-ocean-tg", "account_id": "acc-ocean", "utm_source": "tg", "utm_campaign": "ocean", "utm_content": "post-0017", "created_at": "2026-10-08T08:04:00+00:00"},
        {"event": "connected_audit", "session_id": "sess-ocean-tg", "account_id": "acc-ocean", "utm_source": "tg", "utm_campaign": "ocean", "utm_content": "post-0017", "created_at": "2026-10-08T08:05:00+00:00"},
        {"event": "audit_view", "session_id": "sess-qa", "utm_source": "vk", "utm_campaign": "ocean", "utm_content": "qa-check", "qa": True, "created_at": "2026-10-08T08:06:00+00:00"},
    ]

    class FakeQuery:
        storage_calls = 0

        def __init__(self, model):
            self.model = model

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            if self.model is viral.Storage:
                FakeQuery.storage_calls += 1
                if FakeQuery.storage_calls == 1:
                    return [SimpleNamespace(account_id="acc-ocean", value=json.dumps(state))]
                return [SimpleNamespace(account_id="viral_event", value=json.dumps(x)) for x in real_events]
            if self.model is viral.User:
                return [SimpleNamespace(account_id="acc-ocean", email="ocean@example.com")]
            if self.model is viral.Account:
                return [SimpleNamespace(account_id="acc-ocean", name="Ocean Client")]
            return []

    class FakeSession:
        def query(self, model):
            return FakeQuery(model)

        def close(self):
            return None

    monkeypatch.setattr(viral, "SessionLocal", FakeSession)
    out = viral.owner_ocean_funnel(user=SimpleNamespace(role="owner"))

    assert out["status"] == "ok"
    assert out["summary"]["visits"] == 1
    assert out["summary"]["audits"] == 1
    assert out["summary"]["registrations"] == 1
    assert out["summary"]["results"] == 1
    assert out["summary"]["trial_cta"] == 1
    assert out["summary"]["connected"] == 1
    assert out["summary"]["rates"]["visit_to_audit"] is None
    assert out["by_source"]["tg"]["visits"] == 1
    assert out["by_source"]["vk"]["visits"] == 0
    assert out["posts"][0]["post"] == "post-0017"
    assert out["rows"][0]["source"] == "Ocean TG"
    assert out["rows"][0]["user"] == "ocean@example.com"


def test_event_input_supports_full_audit_attribution():
    payload = viral.EventInput(
        event="audit_view",
        session_id="sess-1",
        utm_source="tg",
        utm_medium="social",
        utm_campaign="ocean",
        utm_content="post-0017",
        landing_path="/audit",
        first_seen_at="2026-10-08T08:00:00+00:00",
        qa=True,
    )
    assert payload.landing_path == "/audit"
    assert payload.first_seen_at == "2026-10-08T08:00:00+00:00"
    assert payload.qa is True


def test_register_attribution_keeps_audit_landing_and_qa():
    from app.api import auth as auth_api

    req = auth_api.RegisterRequest(
        email="qa-attribution@example.com",
        password="strong-password",
        attribution_session_id="session-ocean-1",
        utm_source="tg",
        utm_medium="social",
        utm_campaign="ocean",
        utm_content="post-42",
        landing_path="/audit",
        first_seen_at="2026-10-08T12:00:00+00:00",
        qa=True,
    )
    out = auth_api._register_attribution(req)
    assert out["session_id"] == "session-ocean-1"
    assert out["utm_campaign"] == "ocean"
    assert out["utm_content"] == "post-42"
    assert out["landing_path"] == "/audit"
    assert out["first_seen_at"] == "2026-10-08T12:00:00+00:00"
    assert out["qa"] is True
    assert out.get("captured_at")


def test_ocean_source_labels_and_rate_threshold():
    assert viral._attribution_source_label({"utm_campaign": "ocean", "utm_source": "tg"}) == "Ocean TG"
    assert viral._attribution_source_label({"utm_campaign": "ocean", "utm_source": "vk"}) == "Ocean VK"
    assert viral._attribution_source_label({"referral_code": "abc"}) == "Referral"
    assert viral._attribution_source_label({"utm_source": "email"}) == "Email"
    assert viral._attribution_source_label({}) == "Direct"
    assert viral._safe_rate(10, 29) is None
    assert viral._safe_rate(15, 30) == 50.0


def test_owner_ocean_funnel_rejects_non_owner():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        viral.owner_ocean_funnel(user=SimpleNamespace(role="client"))
    assert exc.value.status_code == 403
