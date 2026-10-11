#!/usr/bin/env python3
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
tokens={};sessions=[];results={};failures=[];before_dm=dm_ids();access0=team0=None;group_id=None
def check(name,ok,detail=None):
 results[name]={"ok":bool(ok),"detail":detail}
 if not ok:failures.append(name)
def http(uid,method,path,body=None):
 return requests.request(method,WEB+path,headers={"Authorization":"Bearer "+tokens[uid]},json=body,timeout=20)
def im(uid,method,path,body=None):
 r=http(uid,method,"/api/internal-messenger"+path,body)
 if r.status_code>=400:raise RuntimeError(method+" "+path+" HTTP "+str(r.status_code))
 return r.json()
try:
 for uid in (OWNER_ID,EMPLOYEE_ID,VIEWER_ID):
  token,sid,epoch=issue(uid,"V7 groups and viewer");tokens[uid]=token;sessions.append((uid,sid))
 group_id=im(OWNER_ID,"POST","/conversations",{"account_id":ACCOUNT,"kind":"GROUP","title":GROUP_TITLE,"member_user_ids":[EMPLOYEE_ID,VIEWER_ID]})["conversation_id"]
 own_mid=im(VIEWER_ID,"POST",f"/conversations/{group_id}/messages",{"body":PREFIX+" VIEWER SEED"})["message"]["id"]
 access0,team0=set_viewer()
 cases=[
 ("SEND","POST",f"/conversations/{group_id}/messages",{"body":PREFIX+" forbidden"}),
 ("CREATE_DM","POST","/conversations",{"account_id":ACCOUNT,"kind":"DM","member_user_ids":[OWNER_ID]}),
 ("CREATE_GROUP","POST","/conversations",{"account_id":ACCOUNT,"kind":"GROUP","title":PREFIX+" forbidden","member_user_ids":[OWNER_ID]}),
 ("EDIT","PATCH",f"/messages/{own_mid}",{"body":PREFIX+" forbidden"}),
 ("DELETE","DELETE",f"/messages/{own_mid}",None),
 ("REACTION","POST",f"/messages/{own_mid}/reactions",{"reaction":"👍"}),
 ("PIN","POST",f"/messages/{own_mid}/pin",None),
 ("ADD_MEMBER","POST",f"/conversations/{group_id}/members",{"user_id":OWNER_ID}),
 ("REMOVE_MEMBER","DELETE",f"/conversations/{group_id}/members/{EMPLOYEE_ID}",None),
 ("MEMBER_ROLE","PATCH",f"/conversations/{group_id}/members/{EMPLOYEE_ID}",{"role":"ADMIN"}),
 ("RENAME","PATCH",f"/conversations/{group_id}",{"title":PREFIX+" forbidden"}),
 ]
 for name,method,path,body in cases:
  r=http(VIEWER_ID,method,"/api/internal-messenger"+path,body);check("VIEWER_"+name+"_403",r.status_code==403,r.status_code)
 r=http(VIEWER_ID,"POST","/api/team/invite",{"account_id":ACCOUNT,"name":"QA Forbidden","phone":"+70000000000","role":"MANAGER"})
 check("VIEWER_TEAM_INVITE_403",r.status_code==403,r.status_code)
 r=http(VIEWER_ID,"POST","/api/messenger-workspace/ownership/take",{"account_id":ACCOUNT,"avito_chat_id":"qa-no-client"})
 check("VIEWER_TAKE_CLIENT_403",r.status_code==403,r.status_code)
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  viewer=browser.new_page(viewport={"width":1280,"height":800})
  viewer.goto(WEB+"/login",wait_until="domcontentloaded")
  viewer.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[tokens[VIEWER_ID],ACCOUNT])
  viewer.goto(WEB+f"/messenger?account_id={ACCOUNT}&internal_conversation_id={group_id}",wait_until="domcontentloaded")
  viewer.get_by_text(PREFIX+" VIEWER SEED",exact=True).wait_for(timeout=20000)
  check("VIEWER_COMPOSER_DISABLED",composer(viewer).locator("textarea").is_disabled() and composer(viewer).get_by_role("button",name="Отправить",exact=True).is_disabled())
  b=viewer.locator("#internal-message-"+str(own_mid));b.hover()
  buttons=b.locator("button").filter(has_text=re.compile("Ответить|Изменить|Удалить|Закрепить|😊"))
  check("VIEWER_MESSAGE_ACTIONS_DISABLED",buttons.count()>=4 and all(buttons.nth(i).is_disabled() for i in range(buttons.count())))
  newchat=viewer.get_by_role("button",name=re.compile("Новый чат|Создать чат"))
  check("VIEWER_CREATE_UI_DISABLED",newchat.count()>0 and all(newchat.nth(i).is_disabled() for i in range(newchat.count())))
  browser.close()
 first=im(OWNER_ID,"POST","/conversations",{"account_id":ACCOUNT,"kind":"DM","member_user_ids":[EMPLOYEE_ID]})["conversation_id"]
 second=im(OWNER_ID,"POST","/conversations",{"account_id":ACCOUNT,"kind":"DM","member_user_ids":[EMPLOYEE_ID]})["conversation_id"]
 check("DM_DEDUP",first==second)
 im(OWNER_ID,"DELETE",f"/conversations/{group_id}/members/{VIEWER_ID}")
 denied=http(VIEWER_ID,"GET",f"/api/internal-messenger/conversations/{group_id}/messages")
 check("GROUP_REMOVE_ACCESS",denied.status_code in (403,404),denied.status_code)
 im(OWNER_ID,"POST",f"/conversations/{group_id}/members",{"user_id":VIEWER_ID})
 check("GROUP_ADD_MEMBER",any(m["user_id"]==VIEWER_ID for m in im(OWNER_ID,"GET",f"/conversations/{group_id}/members")["members"]))
 im(OWNER_ID,"PATCH",f"/conversations/{group_id}",{"title":GROUP_TITLE+" renamed"})
 check("GROUP_RENAME",dbrow("SELECT title FROM internal_conversations WHERE id=:c",{"c":group_id})["title"]==GROUP_TITLE+" renamed")
 for role in ("ADMIN","MEMBER"):
  im(OWNER_ID,"PATCH",f"/conversations/{group_id}/members/{EMPLOYEE_ID}",{"role":role})
  check("GROUP_"+role,any(m["user_id"]==EMPLOYEE_ID and m["role"]==role for m in im(OWNER_ID,"GET",f"/conversations/{group_id}/members")["members"]))
 im(OWNER_ID,"POST",f"/conversations/{group_id}/transfer-ownership",{"new_owner_user_id":EMPLOYEE_ID})
 check("GROUP_TRANSFER_OWNER",any(m["user_id"]==EMPLOYEE_ID and m["role"]=="OWNER" for m in im(EMPLOYEE_ID,"GET",f"/conversations/{group_id}/members")["members"]))
 im(OWNER_ID,"POST",f"/conversations/{group_id}/leave")
 check("GROUP_LEAVE",all(m["user_id"]!=OWNER_ID for m in im(EMPLOYEE_ID,"GET",f"/conversations/{group_id}/members")["members"]))
except Exception as exc:failures.append(type(exc).__name__+":"+str(exc))
finally:
 if access0 is not None:restore_viewer(access0,team0)
 for uid,sid in sessions:revoke(uid,sid)
 results["cleanup"]=cleanup(before_dm)
print(json.dumps({"status":"PASS" if not failures else "FAIL","results":results,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(1 if failures else 0)

