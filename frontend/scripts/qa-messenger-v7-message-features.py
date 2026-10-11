#!/usr/bin/env python3
from pathlib import Path
import ast,json,textwrap
base=Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text()
exec(base.split("access0,team0=set_viewer()")[0])
wrapper=ast.parse(Path("/root/BORIS/frontend/scripts/qa-messenger-v7-closure.py").read_text())
extra=next(ast.literal_eval(n.value) for n in wrapper.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="extra" for t in n.targets))
extra=textwrap.dedent(extra[:extra.index("      repeat=http")])
extra=extra.replace('bubble.hover();bubble.get_by_role("button",name="Изменить",exact=True).click()', 'bubble.hover()\nprint("FEATURE_DIAG",json.dumps({"buttons":bubble.locator("button").all_text_contents(),"aria":bubble.aria_snapshot(),"api":next(({"id":x["id"],"is_own":x.get("is_own"),"sender_user_id":x.get("sender_user_id")} for x in http(OWNER_ID,"GET",f"/conversations/{group_id}/messages")["messages"] if x["id"]==msgid),{})},ensure_ascii=False),flush=True)\nbubble.get_by_role("button",name="Изменить",exact=True).click()')
for label in ("Изменить","Ответить","Закрепить","Открепить","Удалить"):
 extra=extra.replace('name="'+label+'",exact=True','name=re.compile("^'+label+'")')
extra=extra.replace('bubble.get_by_role("button",name=emoji,exact=True).click()\n    emp.wait_for_timeout(900)', 'with owner.expect_response(lambda r:r.request.method=="POST" and "/reactions" in r.url,timeout=15000) as reaction_response:\n        bubble.get_by_role("button",name=emoji,exact=True).click()\n    check("REACTION_HTTP_"+emoji,reaction_response.value.status==200,reaction_response.value.status)\n    emp.wait_for_timeout(900)')
extra=extra.replace('check("REACTIONS",set(x["reaction"] for x in m["reactions"])=={"👍","✅","❤️","😂"})','check("REACTIONS",set(x["reaction"] for x in m["reactions"])=={"👍","✅","❤️","😂"},m["reactions"])')
compile(extra,"message-features","exec")
def send(page,body):
 c=composer(page);c.locator("textarea").first.fill(body)
 with page.expect_response(lambda r:r.request.method=="POST" and re.search(r"/conversations/[0-9]+/messages$",r.url),timeout=20000) as response:
  c.get_by_role("button",name="Отправить",exact=True).click()
 r=response.value
 print("SEND_HTTP",json.dumps({"http":r.status,"conversation_id":r.json().get("message",{}).get("conversation_id"),"expected":group_id}),flush=True)
 assert r.status==200,str(r.status)
 assert int(r.json()["message"]["conversation_id"])==group_id,"WRONG_CONVERSATION"
 page.locator("#internal-message-"+str(r.json()["message"]["id"])).wait_for(timeout=10000)
tokens={};sessions=[];results={};failures=[];group_id=None
before_dm=dm_ids()
try:
 for uid in (OWNER_ID,EMPLOYEE_ID):
  token,sid,epoch=issue(uid,"V7 isolated message feature QA");tokens[uid]=token;sessions.append((uid,sid))
 response=requests.post(WEB+"/api/internal-messenger/conversations",headers={"Authorization":"Bearer "+tokens[OWNER_ID]},json={"account_id":ACCOUNT,"kind":"GROUP","title":GROUP_TITLE,"member_user_ids":[EMPLOYEE_ID]},timeout=20)
 assert response.status_code==200,response.status_code
 group_id=response.json()["conversation_id"]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  contexts={uid:browser.new_context(viewport={"width":1280,"height":800}) for uid in (OWNER_ID,EMPLOYEE_ID)}
  owner=contexts[OWNER_ID].new_page();emp=contexts[EMPLOYEE_ID].new_page()
  for uid,page in ((OWNER_ID,owner),(EMPLOYEE_ID,emp)):
   page.goto(WEB+"/login",wait_until="domcontentloaded")
   page.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[tokens[uid],ACCOUNT])
  exec(compile(extra,"message-features","exec"))
  browser.close()
except Exception as exc:failures.append(type(exc).__name__+":"+str(exc))
finally:
 for uid,sid in sessions:revoke(uid,sid)
 results["cleanup"]=cleanup(before_dm)
print(json.dumps({"status":"PASS" if not failures else "FAIL","results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(1 if failures else 0)
