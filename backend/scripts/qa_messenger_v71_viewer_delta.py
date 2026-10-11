"""Owner/employee UI lifecycle and Viewer read-only, QA tenant only."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
tokens={};sessions=[];checks={};before_dm=dm_ids();access0=team0=None
def check(k,v,detail=None):checks[k]={"ok":bool(v),"detail":detail}
def expect_action(page,method,suffix,action):
 with page.expect_response(lambda r:r.request.method==method and r.url.split("?")[0].endswith(suffix),timeout=20000) as event:action()
 r=event.value;assert r.status==200,(suffix,r.status)
 return r.json()
def send_confirmed(page,cid,body):
 composer(page).locator("textarea").fill(body)
 data=expect_action(page,"POST",f"/conversations/{cid}/messages",lambda:composer(page).get_by_role("button",name="Отправить",exact=True).click())
 mid=data["message"]["id"];page.locator("#internal-message-"+str(mid)).wait_for(timeout=15000)
 return mid
def row(page,name):return page.locator('[class*="internalInfo"] [class*="personRow"]').filter(has_text=name)
def go(page,token,section="chats"):
 page.goto(WEB+"/login",wait_until="domcontentloaded")
 page.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[token,ACCOUNT])
 page.goto(WEB+f"/messenger?account_id={ACCOUNT}&section={section}",wait_until="domcontentloaded")
def open_more(page,label):
 page.get_by_role("button",name="Ещё",exact=True).click()
 page.get_by_role("dialog",name="Ещё").get_by_role("button",name=label,exact=False).click()
try:
 token,sid,_=issue(VIEWER_ID,"V71 Viewer delta");tokens[VIEWER_ID]=token;sessions.append((VIEWER_ID,sid))
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  access0,team0=set_viewer()
  viewer=browser.new_page(viewport={"width":1440,"height":900});go(viewer,tokens[VIEWER_ID])
  viewer.get_by_role("button",name="Новый чат",exact=True).wait_for(timeout=20000);open_general(viewer)
  check("VIEWER_SEND_UI",composer(viewer).locator("textarea").is_disabled() and composer(viewer).get_by_role("button",name="Отправить",exact=True).is_disabled())
  check("VIEWER_CREATE_UI",viewer.get_by_role("button",name="Новый чат",exact=True).is_disabled())
  viewer.get_by_role("button",name="Ещё",exact=True).click()
  team_button=viewer.get_by_role("dialog",name="Ещё").get_by_role("button",name="Команда и доступы",exact=False)
  if team_button.count()==0 or team_button.is_disabled():
   check("VIEWER_TEAM_UI",True,"Team route disabled by role")
   viewer.keyboard.press("Escape")
  else:
   team_button.click()
   viewer.wait_for_url("**section=team",timeout=15000)
   active=viewer.get_by_role("button").filter(has_text=re.compile("Пригласить|Отозвать|Изменить роль"))
   check("VIEWER_TEAM_UI",all(not active.nth(i).is_visible() or active.nth(i).is_disabled() for i in range(active.count())))
  open_more(viewer,"Задачи и напоминания")
  viewer.get_by_role("heading",name="Задачи и напоминания",exact=True).wait_for(timeout=15000)
  active=viewer.get_by_role("button").filter(has_text=re.compile("Создать задачу|Добавить задачу|Взять клиента"))
  check("VIEWER_CRM_UI",all(not active.nth(i).is_visible() or active.nth(i).is_disabled() for i in range(active.count())))
  browser.close()
except Exception as exc:check("UNEXPECTED",False,type(exc).__name__+":"+str(exc))
finally:
 if access0 is not None:restore_viewer(access0,team0)
 for uid,sid in sessions:revoke(uid,sid)
 cleanup_result=cleanup(before_dm)
print(json.dumps({"status":"PASS" if checks and all(v["ok"] for v in checks.values()) else "FAIL","checks":checks,"cleanup":cleanup_result},ensure_ascii=False,indent=2))
raise SystemExit(0 if checks and all(v["ok"] for v in checks.values()) else 1)
