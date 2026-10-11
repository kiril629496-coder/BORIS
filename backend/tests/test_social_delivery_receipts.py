"""Failure-injection tests: no real DB, HTTP, messages or publications."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path
import threading

from unittest import TestCase

from app.services.social_delivery_receipts import (
    DeliveryIntent, PROTOCOL, ReceiptBusy, guarded_delivery,
    delivery_reconciliation_wait_state, waiting,
)
from app.services.social_delivery_reconcile import (
    TelegramPublicFeed, match_telegram_items, match_vk_items, reconcile_vk,
)
from app.services.social_delivery_transport import send_telegram, send_vk


def publish_wall(session, params):
    # Compile only the canonical owner's pure HTTP callback; no app/DB bootstrap.
    path = Path(__file__).resolve().parents[1] / "posting_runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "_social_vk_wall_post_once")
    namespace = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[fn.name](session, params)


class MemoryJournal:
    def __init__(self):
        self.rows = {}
        self.locks = {}
        self.fail_success_once = False

    def __call__(self, intent):
        owner = self
        lock = self.locks.setdefault(intent.key, threading.Lock())
        class Session:
            def __enter__(self):
                if not lock.acquire(False):
                    raise ReceiptBusy()
                return self
            def destination_admission(self):
                # These existing tests isolate receipt/HTTP failure behavior.
                # The separate capacity suite exercises real admission policy.
                class Admitted:
                    def __enter__(self): return self
                    def check(self, now=None):
                        local = (now or datetime.now(timezone.utc))
                        return {"ok": True, "local_day": local.date().isoformat(), "timezone_offset_hours": 0}
                    def __exit__(self, *args): return None
                return Admitted()
            def load(self):
                return deepcopy(owner.rows.get(intent.key))
            def save(self, row):
                if owner.fail_success_once and row["status"] == "success":
                    owner.fail_success_once = False
                    raise OSError("simulated DB commit failure")
                owner.rows[intent.key] = deepcopy(row)
            def __exit__(self, *args):
                lock.release()
        return Session()


def intent(*, tenant="u2", target="-121265252", text="Autumn post", protocol=PROTOCOL, asset=""):
    return DeliveryIntent(tenant, "vk", target, "wall_post", text, asset, {
        "post_id": "post_123", "generation_key": "slot_09",
        "created_at": "2026-10-08T12:00:00+00:00", "protocol": protocol,
        "legacy_error": "photos.getWallUploadServer:",
    })


def confirmed_vk(_intent, _receipt):
    return {"status": "confirmed", "result": {"ok": True, "post_id": 902}}


def test_success_is_committed_before_return_and_replayed_without_send():
    memory, calls = MemoryJournal(), []
    def send(_):
        calls.append("wall.post")
        return "success", {"ok": True, "post_id": 901}
    first = guarded_delivery(intent(), send, journal_factory=memory)
    again = guarded_delivery(intent(), send, journal_factory=memory)
    assert first["post_id"] == again["post_id"] == 901
    assert again["receipt_cached"] is True
    assert calls == ["wall.post"]
    assert memory.rows[intent().key]["status"] == "success"


def test_kill_after_provider_acceptance_leaves_inflight_and_never_blind_retries():
    memory, calls = MemoryJournal(), []
    def crash(_):
        calls.append("accepted")
        raise SystemExit("worker killed before receipt")
    with TestCase().assertRaises(SystemExit):
        guarded_delivery(intent(), crash, journal_factory=memory)
    assert memory.rows[intent().key]["status"] == "inflight"
    result = guarded_delivery(intent(), crash, journal_factory=memory)
    assert result["delivery_outcome_uncertain"] is True
    assert memory.rows[intent().key]["status"] == "uncertain"
    assert calls == ["accepted"]


def test_commit_failure_after_send_can_only_be_recovered_by_readback():
    memory, calls = MemoryJournal(), []
    memory.fail_success_once = True
    def send(_):
        calls.append("accepted")
        return "success", {"ok": True, "post_id": 902}
    failed = guarded_delivery(intent(), send, journal_factory=memory)
    assert failed["delivery_outcome_uncertain"]
    assert memory.rows[intent().key]["status"] == "inflight"
    recovered = guarded_delivery(intent(), send, reconcile=confirmed_vk, journal_factory=memory)
    assert recovered["ok"] and recovered["receipt_cached"]
    assert calls == ["accepted"]


def test_changed_payload_cannot_escape_an_uncertain_lifecycle():
    memory = MemoryJournal()
    def unknown(_):
        raise TimeoutError()
    guarded_delivery(intent(), unknown, journal_factory=memory)
    called = []
    result = guarded_delivery(intent(text="edited"), lambda _: called.append(True), journal_factory=memory)
    assert result["reason"] == "SOCIAL_DELIVERY_INTENT_CHANGED"
    assert called == []


def test_rejected_draft_can_be_corrected_but_uncertain_cannot():
    memory = MemoryJournal()
    guarded_delivery(intent(), lambda _: ("rejected", {"ok": False, "code": 100}), journal_factory=memory)
    result = guarded_delivery(intent(text="corrected"), lambda _: ("success", {"ok": True, "post_id": 1}),
                              journal_factory=memory)
    assert result["ok"]


def test_explicit_rate_limit_is_persisted_and_next_pass_does_not_retry_early():
    memory, calls = MemoryJournal(), []
    def reject(_):
        calls.append("attempt")
        return "rejected", {"ok": False, "code": 429, "retry_after_seconds": 3600,
                            "dependency_state": "waiting_external"}
    guarded_delivery(intent(), reject, journal_factory=memory)
    result = guarded_delivery(intent(), reject, journal_factory=memory)
    assert not result["ok"] and calls == ["attempt"]


def test_tenant_target_and_lifecycle_are_independent():
    values = [intent(), intent(tenant="u3"), intent(target="-42")]
    assert len({x.key for x in values}) == 3
    assert intent(text="changed").key == intent().key
    assert intent(text="changed").payload_hash != intent().payload_hash


def test_second_writer_cannot_send_during_first_writer():
    memory, started, release, calls = MemoryJournal(), threading.Event(), threading.Event(), []
    def send(_):
        calls.append("one")
        started.set()
        assert release.wait(3)
        return "success", {"ok": True, "post_id": 1}
    thread = threading.Thread(target=lambda: guarded_delivery(intent(), send, journal_factory=memory))
    thread.start()
    assert started.wait(3)
    second = guarded_delivery(intent(), send, journal_factory=memory)
    release.set()
    thread.join(3)
    assert second["reason"] == "social_delivery_busy"
    assert calls == ["one"]


def test_legacy_without_receipt_is_not_permission_to_send():
    memory, calls = MemoryJournal(), []
    result = guarded_delivery(intent(protocol=""), lambda _: calls.append(True),
                              reconcile=lambda *_: {"status": "absent", "scan_complete": False},
                              journal_factory=memory)
    assert result["delivery_outcome_uncertain"] and not calls


def test_legacy_release_requires_complete_readback_plus_proven_pre_send_rejection():
    memory, calls = MemoryJournal(), []
    def send(_):
        calls.append("one")
        return "success", {"ok": True, "post_id": 902}
    result = guarded_delivery(
        intent(protocol=""), send,
        reconcile=lambda *_: {"status": "absent", "scan_complete": True,
                               "pre_send_rejection_proven": True},
        journal_factory=memory,
    )
    assert result["ok"] and calls == ["one"]


def test_receipt_db_unavailable_is_internal_and_does_not_hide_as_external_wait():
    calls = []
    def unavailable(_):
        raise OSError("db unavailable")
    result = guarded_delivery(intent(), lambda _: calls.append(True), journal_factory=unavailable)
    assert result["dependency_state"] == "internal_error"
    assert result["delivery_outcome_uncertain"] is False and not calls


def test_observer_recognizes_only_structured_uncertainty_without_marking_published():
    record = {"status": "partial", "ids": {"tg": {"message_id": 315}},
              "delivery_failures": {"vk": waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN")}}
    snapshot = deepcopy(record)
    result = delivery_reconciliation_wait_state(record, ["tg", "vk"])
    assert result["reconciliation_required"] and result["owner_action_required"] is False
    assert result["next_action"] == "read_only_provider_reconciliation"
    assert record == snapshot and not result.get("ok")


def test_observer_does_not_hide_internal_failure_on_another_pending_channel():
    record = {"ids": {}, "delivery_failures": {
        "tg": {"error": "DB_UNAVAILABLE", "dependency_state": "internal_error"},
        "vk": waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN"),
    }}
    assert delivery_reconciliation_wait_state(record, ["tg", "vk"]) is None


def test_observer_can_wait_for_uncertain_tg_and_explicit_vk_cooldown_together():
    record = {"ids": {}, "delivery_failures": {
        "tg": waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN"),
        "vk": {"dependency_state": "waiting_external", "retryable_external": True},
    }}
    assert delivery_reconciliation_wait_state(record, ["tg", "vk"])["reconciliation_required"]


def test_observer_rejects_unstructured_errors_and_preserves_concrete_owner_action():
    failure = waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN")
    record = {"ids": {}, "delivery_failures": {"vk": {**failure, "receipt_key": ""}}}
    assert delivery_reconciliation_wait_state(record, ["vk"]) is None
    record["delivery_failures"]["vk"] = {**failure, "owner_action_required": True,
        "owner_action": "inspect_provider_candidates_text_and_banner"}
    result = delivery_reconciliation_wait_state(record, ["vk"])
    assert result["owner_action_required"] and result["next_action"] == "owner_provider_history_inspection"


def test_uncertain_candidate_escalates_to_concrete_review_after_six_hours():
    receipt = {"attempted_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()}
    result = waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN", receipt=receipt,
                     evidence={"status": "ambiguous", "candidates": [{"message_id": 315, "asset_verified": False}]})
    assert result["review_overdue"] and result["owner_action_required"]
    assert result["owner_action"] == "inspect_provider_candidates_text_and_banner"
    assert receipt["review_after"] == result["review_after"]


def test_first_automatic_reconciliation_window_does_not_prematurely_ask_owner():
    receipt = {"attempted_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()}
    result = waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN", receipt=receipt,
                     evidence={"status": "ambiguous", "candidates": [{"post_id": 902}]})
    assert not result["review_overdue"] and not result["owner_action_required"]


def test_provider_cooldown_never_fabricates_a_manual_owner_remedy():
    receipt = {"attempted_at": (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()}
    result = waiting(intent(), "SOCIAL_DELIVERY_OUTCOME_UNCERTAIN", receipt=receipt,
                     evidence={"status": "unavailable", "reason": "vk_cooldown"})
    assert result["review_overdue"] and not result["owner_action_required"]


class Response:
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return deepcopy(self.payload)


class TelegramSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def post(self, url, **kwargs):
        self.calls.append(url.rsplit("/", 1)[-1])
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return Response(response)


def test_telegram_timeout_has_one_mutation_and_no_internal_retry():
    session, memory = TelegramSession([TimeoutError()]), MemoryJournal()
    kwargs = dict(tenant_scope="u2", delivery_context=intent().context,
                  api_base="https://telegram.invalid/botTEST", session=session,
                  journal_factory=memory, reconcile=lambda *_: {"status": "absent"})
    first = send_telegram("@channel_test", "hello", None, **kwargs)
    second = send_telegram("@channel_test", "hello", None, **kwargs)
    assert first["delivery_outcome_uncertain"] and not second["ok"]
    assert session.calls == ["sendMessage"]


def test_telegram_server_error_is_uncertain_and_is_not_repeated():
    session, memory = TelegramSession([{"ok": False, "error_code": 500}]), MemoryJournal()
    kwargs = dict(tenant_scope="u2", delivery_context=intent().context,
                  api_base="https://telegram.invalid/botTEST", session=session,
                  journal_factory=memory, reconcile=lambda *_: {"status": "absent"})
    assert send_telegram("@channel_test", "hello", None, **kwargs)["delivery_outcome_uncertain"]
    assert not send_telegram("@channel_test", "hello", None, **kwargs)["ok"]
    assert session.calls == ["sendMessage"]


def test_telegram_long_post_failure_does_not_resend_the_successful_photo(tmp_path):
    from PIL import Image
    image = tmp_path / "banner.png"
    Image.new("RGB", (16, 16), (120, 45, 78)).save(image)
    session = TelegramSession([
        {"ok": True, "result": {"message_id": 315, "chat": {"id": -1001}}},
        TimeoutError(),
    ])
    memory = MemoryJournal()
    kwargs = dict(tenant_scope="u2", delivery_context=intent().context,
                  api_base="https://telegram.invalid/botTEST", session=session,
                  journal_factory=memory, reconcile=lambda *_: {"status": "absent"})
    first = send_telegram("@channel_test", "a" * 1200, str(image), **kwargs)
    second = send_telegram("@channel_test", "a" * 1200, str(image), **kwargs)
    assert not first["ok"] and first["photo_message_id"] == 315
    assert not second["ok"]
    assert session.calls == ["sendPhoto", "sendMessage"]


class VkSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def get(self, url, **kwargs):
        self.calls.append(url.rsplit("/", 1)[-1])
        value = self.responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return Response(value)
    def post(self, url, **kwargs):
        self.calls.append("photo_upload")
        return Response({"photo": "prepared", "server": 1, "hash": "hash"})


def vk_failure(payload, stage, prefix=""):
    return {"ok": False, "error": "Flood control", "stage": stage, "reason": "vk_rate_limited",
            "dependency_state": "waiting_external", "retryable_external": True,
            "retry_after_seconds": 3600}


def test_vk_flood_at_upload_never_reaches_wall_post(tmp_path):
    from PIL import Image
    image = tmp_path / "banner.png"
    Image.new("RGB", (16, 16)).save(image)
    session = VkSession([{"error": {"error_code": 9, "error_msg": "Flood control"}}])
    result = send_vk(-121, "text", str(image), tenant_scope="u2", token="TEST",
                     delivery_context=intent().context, session=session, journal_factory=MemoryJournal(),
                     api_failure=vk_failure, publish_wall=publish_wall, cooldown_remaining=lambda: 0, clear_cooldown=lambda: None)
    assert result["stage"] == "photos.getWallUploadServer"
    assert session.calls == ["photos.getWallUploadServer"]


def test_vk_attachment_is_durable_before_wall_post_timeout_and_reused_by_readback(tmp_path):
    from PIL import Image
    image = tmp_path / "banner.png"
    Image.new("RGB", (16, 16)).save(image)
    session = VkSession([
        {"response": {"upload_url": "https://upload.vk.invalid/"}},
        {"response": [{"owner_id": -121, "id": 77}]},
        TimeoutError(),
    ])
    memory = MemoryJournal()
    kwargs = dict(tenant_scope="u2", token="TEST", delivery_context=intent().context,
                  session=session, journal_factory=memory, api_failure=vk_failure, publish_wall=publish_wall,
                  cooldown_remaining=lambda: 0, clear_cooldown=lambda: None,
                  reconcile=lambda i, r: {
                      "status": "confirmed", "result": {"ok": True, "post_id": 15},
                      "attachment_checked": r["prepared"]["attachment"],
                  })
    first = send_vk(-121, "text", str(image), **kwargs)
    assert first["delivery_outcome_uncertain"]
    receipt = next(iter(memory.rows.values()))
    assert receipt["prepared"]["attachment"] == "photo-121_77"
    second = send_vk(-121, "text", str(image), **kwargs)
    assert second["ok"] and second["post_id"] == 15
    assert session.calls == ["photos.getWallUploadServer", "photo_upload", "photos.saveWallPhoto", "wall.post"]


def test_vk_success_without_id_is_uncertain_not_retryable_failure():
    session, memory = VkSession([{"response": {}}]), MemoryJournal()
    kwargs = dict(tenant_scope="u2", token="TEST", delivery_context=intent().context,
                  session=session, journal_factory=memory, api_failure=vk_failure, publish_wall=publish_wall,
                  cooldown_remaining=lambda: 0, clear_cooldown=lambda: None,
                  reconcile=lambda *_: {"status": "absent"})
    first = send_vk(-121, "text", None, **kwargs)
    second = send_vk(-121, "text", None, **kwargs)
    assert first["delivery_outcome_uncertain"] and not second["ok"]
    assert session.calls == ["wall.post"]


def test_vk_internal_error_is_uncertain_and_not_blindly_repeated():
    session, memory = VkSession([{"error": {"error_code": 10}}]), MemoryJournal()
    kwargs = dict(tenant_scope="u2", token="TEST", delivery_context=intent().context,
                  session=session, journal_factory=memory, api_failure=vk_failure, publish_wall=publish_wall,
                  cooldown_remaining=lambda: 0, clear_cooldown=lambda: None,
                  reconcile=lambda *_: {"status": "absent"})
    assert send_vk(-121, "text", None, **kwargs)["delivery_outcome_uncertain"]
    assert not send_vk(-121, "text", None, **kwargs)["ok"]
    assert session.calls == ["wall.post"]


def test_vk_cached_success_does_not_clear_another_posts_global_cooldown():
    session, memory, cleared = VkSession([{"response": {"post_id": 4}}]), MemoryJournal(), []
    kwargs = dict(tenant_scope="u2", token="TEST", delivery_context=intent().context,
                  session=session, journal_factory=memory, api_failure=vk_failure, publish_wall=publish_wall,
                  cooldown_remaining=lambda: 0, clear_cooldown=lambda: cleared.append(True))
    send_vk(-121, "text", None, **kwargs)
    kwargs["cooldown_remaining"] = lambda: 3600
    result = send_vk(-121, "text", None, **kwargs)
    assert result["ok"] and result["receipt_cached"]
    assert cleared == [True] and session.calls == ["wall.post"]


def fixture_receipt():
    return {"attempted_at": "2026-10-08T12:30:00+00:00",
            "context": intent().context, "prepared": {"attachment": "photo-121265252_77"}}


def test_vk_reconciliation_requires_exact_text_attachment_and_attempt_window():
    i = intent(asset="hash")
    post = {"id": 902, "owner_id": -121265252,
            "date": datetime(2026, 10, 8, 12, 30, tzinfo=timezone.utc).timestamp(),
            "text": "Autumn post", "attachments": [{"type": "photo", "photo": {"owner_id": -121265252, "id": 77}}]}
    assert match_vk_items(i, fixture_receipt(), [post])["status"] == "confirmed"
    assert match_vk_items(i, fixture_receipt(), [{**post, "text": "Different"}])["status"] == "absent"
    wrong_photo = {**post, "attachments": [{"type": "photo", "photo": {"owner_id": -121265252, "id": 78}}]}
    assert match_vk_items(i, fixture_receipt(), [wrong_photo])["status"] == "ambiguous"
    assert match_vk_items(i, fixture_receipt(), [post, {**post, "id": 903}])["status"] == "ambiguous"


def test_telegram_parser_and_photo_presence_alone_are_not_a_false_receipt():
    feed = TelegramPublicFeed()
    feed.feed("""<div class="tgme_widget_message" data-post="channel_test/315">
      <div class="tgme_widget_message_text"><b>Hello</b><br>world &amp; autumn</div>
      <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn4.cdn-telegram.org/file/p.jpg')"></a>
      <a><time datetime="2026-10-08T12:30:00+00:00"></time></a>
    </div>""")
    i = DeliveryIntent("u2", "tg", "@channel_test", "photo", "Hello\nworld & autumn", "expected", intent().context)
    assert feed.items[0]["message_id"] == 315
    mismatch = match_telegram_items(i, fixture_receipt(), feed.items, image_sha=lambda _: "different")
    assert mismatch["status"] == "ambiguous"
    match = match_telegram_items(i, fixture_receipt(), feed.items, image_sha=lambda _: "expected")
    assert match["result"]["result"]["message_id"] == 315


def test_pinned_old_vk_post_does_not_prove_full_history_scan():
    i = intent(protocol="")
    old = {"date": datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp(), "is_pinned": 1}
    recent = {"date": datetime(2026, 10, 8, 13, 0, tzinfo=timezone.utc).timestamp()}
    session = VkSession([
        {"response": {"count": 1000, "items": [old] + [recent] * 99}},
        {"response": {"count": 1000, "items": [recent] * 100}},
    ])
    result = reconcile_vk(i, {"context": i.context, "prepared": {}}, token="TEST", session=session)
    assert result["status"] == "absent" and result["scan_complete"] is False
    assert session.calls == ["wall.get", "wall.get"]


def test_canonical_autopost_stages_draft_without_any_provider_send():
    path = Path(__file__).resolve().parents[1] / "posting_runner.py"
    if not path.exists():
        path = Path(__file__).resolve().parents[2] / "proposal/backend/posting_runner.py"
    source = path.read_text()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_autopost_projects_impl")
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert not any(n.func.id in {"_send_telegram", "_send_vk"} for n in calls)
    staged = [n for n in calls if n.func.id == "_save_post_record"
              and len(n.args) > 5 and isinstance(n.args[5], ast.Constant) and n.args[5].value == "draft"]
    assert staged
    assert '"delivery_protocol": "SOCIAL_RECEIPT_V1"' in source



def test_vk_owner_callback_preserves_session_request_and_timeout():
    params = {"access_token": "TEST", "v": "5.199", "owner_id": -121,
              "from_group": 1, "message": "text", "attachments": "photo-121_77"}
    calls = []
    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response({"response": {"post_id": 991}})
    payload = publish_wall(Session(), params)
    assert payload == {"response": {"post_id": 991}}
    assert len(calls) == 1
    assert calls[0][0] == "https://api.vk.com/method/wall.post"
    assert calls[0][1]["params"] is params
    assert calls[0][1]["timeout"] == (5, 30)


def test_vk_injected_owner_runs_only_after_inflight_and_once_on_success():
    memory, session, calls = MemoryJournal(), VkSession([]), []
    def owner(actual_session, params):
        assert actual_session is session
        assert next(iter(memory.rows.values()))["status"] == "inflight"
        calls.append(deepcopy(params))
        return {"response": {"post_id": 992}}
    kwargs = dict(tenant_scope="u2", token="TEST", delivery_context=intent().context,
                  session=session, journal_factory=memory, api_failure=vk_failure,
                  publish_wall=owner, cooldown_remaining=lambda: 0,
                  clear_cooldown=lambda: None)
    assert send_vk(-121, "text", None, **kwargs)["post_id"] == 992
    assert send_vk(-121, "text", None, **kwargs)["receipt_cached"]
    assert len(calls) == 1 and session.calls == []


def test_vk_injected_owner_failure_is_uncertain_without_republication():
    memory, session, calls = MemoryJournal(), VkSession([]), []
    def owner(actual_session, params):
        assert actual_session is session
        assert next(iter(memory.rows.values()))["status"] == "inflight"
        calls.append(deepcopy(params))
        raise TimeoutError("ambiguous provider result")
    kwargs = dict(tenant_scope="u2", token="TEST", delivery_context=intent().context,
                  session=session, journal_factory=memory, api_failure=vk_failure,
                  publish_wall=owner, cooldown_remaining=lambda: 0,
                  clear_cooldown=lambda: None, reconcile=lambda *_: {"status": "absent"})
    assert send_vk(-121, "text", None, **kwargs)["delivery_outcome_uncertain"]
    assert not send_vk(-121, "text", None, **kwargs)["ok"]
    assert len(calls) == 1 and session.calls == []


def test_vk_owner_callback_is_required_before_any_transport_activity():
    session = VkSession([])
    with TestCase().assertRaises(TypeError):
        send_vk(-121, "text", None, tenant_scope="u2", token="TEST", session=session,
                api_failure=vk_failure, cooldown_remaining=lambda: 0,
                clear_cooldown=lambda: None)
    assert session.calls == []
