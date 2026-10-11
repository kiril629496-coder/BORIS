"""Behavioral regressions for private streams and monotonic read receipts."""
import ast
from pathlib import Path
from types import SimpleNamespace
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "app/api/internal_messenger.py"
def function(name, **bindings):
    node = next(n for n in ast.parse(SOURCE.read_text()).body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node.decorator_list = []
    ns = {"Depends": lambda x: None, "get_current_user": None, **bindings}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns[name]

@pytest.mark.parametrize("members,entity,payload,allowed", [
    ({7}, "message", {"conversation_id":7,"body":"private"}, True),
    ({8}, "message", {"conversation_id":7,"body":"private"}, False),
    (set(), "conversation", {"body":"private"}, False),
    ({7}, "conversation", {"body":"private"}, True),
    ({7}, "message", {"conversation_id":"bad","body":"private"}, False),
])
def test_private_events_only_reach_current_members(members,entity,payload,allowed):
    event={"id":22,"type":"internal.message.created","entity_type":entity,
           "entity_id":7,"payload":payload,"created_at":"now"}
    result=function("_event_for_memberships")(event,members)
    if allowed:
        assert result == event
    else:
        assert result is None

def test_public_account_event_is_preserved():
    event={"id":23,"type":"team.changed","payload":{"action":"access_revoked"}}
    result = function("_event_for_memberships")(event,set())
    assert result["type"] == "team.changed"
    assert result["payload"] == {} and result["entity_id"] is None

@pytest.mark.parametrize("kind", ["unknown.event", "internal.unknown"])
def test_unknown_visibility_is_absent(kind):
    event={"id":99,"type":kind,"payload":{"body":"secret","attachment":"private.pdf"}}
    assert function("_event_for_memberships")(event,set()) is None

def test_read_receipt_only_emits_on_monotonic_advance():
    events=[]
    class DB:
        last=0
        committed=0
        def execute(self,sql,params):
            if str(sql).startswith("SELECT"):
                return SimpleNamespace(first=lambda:(1,))
            assert "COALESCE(last_read_message_id,0)<:m" in str(sql)
            advanced=params["m"] > self.last
            if advanced: self.last=params["m"]
            return SimpleNamespace(first=lambda:(self.last,) if advanced else None)
        def commit(self): self.committed+=1
        def close(self): pass
    db=DB()
    call=function("mark_read", SessionLocal=lambda:db,text=lambda s:s,
                  _require_member=lambda *a:{"account_id":"qa"},
                  emit_messenger_event=lambda *a:events.append(a))
    for mid in (10,10,9,11,11):
        call(7,mid,SimpleNamespace(id=8))
    assert db.last==11 and db.committed==5
    assert [e[-1]["last_read_message_id"] for e in events]==[10,11]

def test_revoked_or_blocked_stream_user_denied_without_validating_session():
    check=function("_stream_session_active",text=lambda s:s)
    for row in (None, {"is_active":False,"status":"active","security_epoch":0},
                {"is_active":True,"status":"blocked","security_epoch":0}):
        db=SimpleNamespace(execute=lambda *a:SimpleNamespace(
            mappings=lambda:SimpleNamespace(first=lambda:row)))
        assert check(db,SimpleNamespace(id=8)) is False

def test_stream_revalidates_session_and_feed_requires_user_identity():
    tree=ast.parse(SOURCE.read_text())
    feed=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=="_messenger_event_feed")
    assert "user_id" in [a.arg for a in feed.args.kwonlyargs]
    source=SOURCE.read_text()
    assert source.count("if not _stream_session_active(session, user) or not _access(session, user, account_id):")==2

@pytest.mark.parametrize("session_valid", [True, False])
def test_stream_checks_current_session_and_epoch(monkeypatch, session_valid):
    from app.services import auth_sessions
    received=[]
    def validate(db, **kwargs):
        received.append(kwargs)
        return session_valid
    monkeypatch.setattr(auth_sessions, "validate_session", validate)
    db=SimpleNamespace(execute=lambda *a:SimpleNamespace(
        mappings=lambda:SimpleNamespace(first=lambda:{
            "is_active":True,"status":"active","security_epoch":9})))
    user=SimpleNamespace(id=8,_auth_session_id="qa-session",_auth_security_epoch=8)
    assert function("_stream_session_active",text=lambda s:s)(db,user) is session_valid
    assert received==[{"user_id":8,"session_id":"qa-session","token_epoch":8,"current_epoch":9}]

@pytest.mark.parametrize("filename", ["../qa.png", "folder/qa.png", "folder\\qa.png", "..", "."])
def test_upload_rejects_path_filenames_before_storage(filename):
    import asyncio
    from fastapi import HTTPException
    db=SimpleNamespace(close=lambda:None)
    call=function("upload_attachment", UploadFile=object, File=lambda *a,**k:None,
                  enforce_messenger_rate=lambda *a,**k:None,
                  SessionLocal=lambda:db, _require_member=lambda *a:{"account_id":"qa"},
                  _require_reply=lambda *a:None, HTTPException=HTTPException)
    with pytest.raises(HTTPException) as error:
        asyncio.run(call(7,SimpleNamespace(filename=filename),SimpleNamespace(id=8)))
    assert error.value.status_code==400
