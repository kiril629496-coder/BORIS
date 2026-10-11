"""QA employee session epoch acceptance; no tokens in output."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
sessions=[];checks={};fresh=None
def call(token,method,path):
 return requests.request(method,WEB+path,headers={"Authorization":"Bearer "+token},timeout=20)
try:
 one,sid,epoch=issue(EMPLOYEE_ID,"V71 epoch first");sessions.append(sid)
 two,sid2,_=issue(EMPLOYEE_ID,"V71 epoch second");sessions.append(sid2)
 route=f"/api/internal-messenger/events?account_id={ACCOUNT}"
 checks["SERVER_SESSION_VALID"]=call(one,"GET",route).status_code==200
 r=call(one,"POST","/api/auth/sessions/revoke-all")
 checks["REVOKE_ALL_HTTP"]=r.status_code==200
 assert r.status_code==200
 fresh=r.json()["access_token"]
 current=dbrow("SELECT security_epoch FROM users WHERE id=:u",{"u":EMPLOYEE_ID})["security_epoch"]
 checks["REVOKE_ALL_SECURITY_EPOCH"]=current==epoch+1
 checks["OLD_JWT_AFTER_REVOKE_ALL_401"]=all(call(t,"GET",route).status_code==401 for t in (one,two))
 checks["FRESH_SESSION_VALID"]=call(fresh,"GET",route).status_code==200
 checks["OLD_SESSIONS_REVOKED"]=dbrow("SELECT count(*) n FROM auth_user_sessions WHERE session_id=ANY(:ids) AND revoked_at IS NULL",{"ids":sessions})["n"]==0
finally:
 for sid in sessions:revoke(EMPLOYEE_ID,sid)
 if fresh:
  call(fresh,"POST","/api/auth/logout")
print(json.dumps({"status":"PASS" if checks and all(checks.values()) else "FAIL","checks":checks}))
assert checks and all(checks.values())
