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
 r=req("POST","/api/team/invite",{"account_id":ACCOUNT,"name":name,"phone":"+70000"+STAMP[-5:]+str({"accept":1,"expired":2,"cancelled":3}[label]),"email":email,"role":"MANAGER"})
 assert r.status_code==200,r.status_code
 secret=parse_qs(urlparse(r.json()["invite_url"]).query)["token"][0]
 row=dbrow("SELECT id,account_id,role,invite_expires_at>now() valid,invite_token_hash FROM team_memberships WHERE account_id=:a AND email=:e",{"a":ACCOUNT,"e":email})
 assert row["valid"] and row["invite_token_hash"]!=secret
 invites.append(row["id"]);return secret,email,name,row["id"]
try:
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
