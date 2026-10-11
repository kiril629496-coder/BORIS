"""Remaining invite boundaries and role UI; synthetic employees only."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
from urllib.parse import urlparse,parse_qs
import secrets
tok,sid,_=issue(OWNER_ID,"V71 invite edges");checks={};invites=[];created_user=None
password=secrets.token_urlsafe(24)+"A9"
def req(method,path,body=None):
 return requests.request(method,WEB+path,headers={"Authorization":"Bearer "+tok},json=body,timeout=20)
def invite(label):
 email="qa.v71."+STAMP+"."+label+"@example.com";name="QA V71 "+STAMP+" "+label
 r=req("POST","/api/team/invite",{"account_id":ACCOUNT,"name":name,"phone":"+700000"+STAMP[-5:],"email":email,"role":"MANAGER"})
 assert r.status_code==200,r.status_code
 secret=parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
 row=dbrow("SELECT id,account_id,role,invite_expires_at>now() valid,invite_token_hash FROM team_memberships WHERE account_id=:a AND email=:e",{"a":ACCOUNT,"e":email})
 assert row["valid"] and row["invite_token_hash"]!=secret
 invites.append(row["id"]);return secret,email,name,row["id"]
try:
 secret,email,name,mid=invite("accept")
 r=req("POST","/api/team/invite/accept",{"token":secret,"email":email,"password":password})
 assert r.status_code==200,r.status_code
 created_user=r.json()["user_id"]
 checks["ACCOUNT_ROLE_BINDING"]=r.json()["account_id"]==ACCOUNT and dbrow("SELECT role FROM user_account_access WHERE user_id=:u AND account_id=:a",{"u":created_user,"a":ACCOUNT})["role"]=="MANAGER"
 checks["ONE_TIME_ACCEPT"]=req("POST","/api/team/invite/accept",{"token":secret,"email":email,"password":password}).status_code==400
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=["--no-sandbox"]);p=b.new_page(viewport={"width":1440,"height":900})
  p.goto(WEB+"/login");p.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[tok,ACCOUNT])
  p.goto(WEB+f"/messenger?account_id={ACCOUNT}&section=team")
  row=p.locator('[class*="teamMemberRow"]').filter(has_text=email);row.wait_for(timeout=20000)
  for role,label in [("MANAGER_LEAD","Старший менеджер"),("VIEWER","Наблюдатель"),("MANAGER","Менеджер")]:
   trigger=row.get_by_role("button",name="Роль "+name,exact=True)
   if trigger.get_attribute("aria-expanded")=="true":p.keyboard.press("Escape")
   trigger.click()
   row.get_by_role("listbox").wait_for(timeout=10000)
   print("ROLE_OPTIONS",role,row.get_by_role("option").all_text_contents(),flush=True)
   with p.expect_response(lambda r:r.request.method=="PATCH" and f"/api/team/memberships/{mid}" in r.url,timeout=20000) as response:
    p.get_by_role("option").filter(has=p.get_by_text(label,exact=True)).click()
   row.get_by_role("button",name="Роль "+name,exact=True).get_by_text(label,exact=True).wait_for(timeout=10000)
   checks["ROLE_UI_"+role]=response.value.status==200 and dbrow("SELECT role FROM user_account_access WHERE user_id=:u AND account_id=:a",{"u":created_user,"a":ACCOUNT})["role"]==role
  b.close()
 for case in ("expired","cancelled"):
  secret,email,_,invite_id=invite(case)
  if case=="expired":
   db=SessionLocal()
   try:
    db.execute(text("UPDATE team_memberships SET invite_expires_at=now()-interval '1 second' WHERE id=:id AND account_id=:a AND status='INVITED'"),{"id":invite_id,"a":ACCOUNT});db.commit()
   finally:db.close()
  else:assert req("POST",f"/api/team/memberships/{invite_id}/revoke-invite").status_code==200
  checks["INVITE_"+case.upper()]=req("POST","/api/team/invite/accept",{"token":secret,"email":email,"password":password}).status_code==400
except Exception as e:checks["UNEXPECTED"]={"error":type(e).__name__+":"+str(e)}
finally:
 if created_user:req("POST","/api/team/revoke",{"user_id":created_user,"account_id":ACCOUNT})
 for i in invites:
  if dbrow("SELECT status FROM team_memberships WHERE id=:id",{"id":i})["status"]=="INVITED":req("POST",f"/api/team/memberships/{i}/revoke-invite")
 revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if checks and all(v is True for v in checks.values()) else "FAIL","checks":checks,"qa_user_id":created_user,"qa_memberships":invites},ensure_ascii=False,indent=2))
assert checks and all(v is True for v in checks.values())
