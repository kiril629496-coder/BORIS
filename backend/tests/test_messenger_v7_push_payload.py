"""Transport-boundary payload checks: no network or real Push delivery."""
import ast,json,sys
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize("root", [Path(__file__).resolve().parents[1],
                                 Path("/root/BORIS/releases/backend/current/backend")])
@pytest.mark.parametrize("event_type,url", [
    ("internal_message","/messenger"),
    ("client_assignment","/messenger?client=qa"),
    ("crm_task_reminder","/messenger?task=qa"),
    ("other","/messenger?qa=1"),
])
def test_messenger_payload_is_private_at_webpush_boundary(monkeypatch,root,event_type,url):
    sent=[];deliveries=[]
    monkeypatch.setitem(sys.modules,"pywebpush",SimpleNamespace(
        webpush=lambda **kwargs:sent.append(kwargs),
        WebPushException=RuntimeError))
    rows=[{"id":1,"user_id":7,"endpoint_hash":"qa-one","subscription_enc":"{}"},
          {"id":2,"user_id":8,"endpoint_hash":"qa-two","subscription_enc":"{}"}]
    class DB:
        def execute(self,*args):
            return SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:rows))
        def close(self):pass
        def commit(self):pass
    ns={"Any":object,"json":json,
        "os":SimpleNamespace(getenv=lambda *a:None,path=SimpleNamespace(exists=lambda *a:True)),
        "WEBPUSH_PRIVATE_KEY":"qa-key-placeholder","WEBPUSH_SUBJECT":"mailto:qa@example.com",
        "SessionLocal":DB,"text":lambda x:x,"decrypt_secret":lambda x:x,
        "_record_delivery":lambda *a:deliveries.append(a)}
    tree=ast.parse((root/"app/services/lead_notifications.py").read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {"_out_payload","_send_webpush"}]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"push-boundary","exec"),ns)
    out={"id":1,"account_id":"qa","title":"BORIS Messenger","body":"PRIVATE client text CRM 12345 rub",
         "event_type":event_type,"severity":"info","payload_json":{"messenger_url":url,"target_user_ids":[7]}}
    assert ns["_send_webpush"](out,{"web_push_enabled":True})==(1,0)
    assert len(sent)==1
    payload=json.loads(sent[0]["data"])
    assert payload["body"]=="Откройте BORIS Messenger, чтобы посмотреть обновление."
    assert payload["privacy_mode"]=="private" and payload["url"]==url
    assert "PRIVATE" not in sent[0]["data"] and "12345" not in sent[0]["data"]
    assert deliveries[0][2]=="qa-one"
    sent.clear();out["payload_json"]["target_user_ids"]=[]
    assert ns["_send_webpush"](out,{"web_push_enabled":True})==(0,0)
    assert sent==[]
