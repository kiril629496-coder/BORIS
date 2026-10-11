#!/usr/bin/env python3
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import io,hashlib
from PIL import Image
tokens={};sessions=[];checks={};failures=[];before_dm=dm_ids();group_id=None;uploaded=[]
def check(name,ok,details=None):
 checks[name]={"ok":bool(ok),"details":details}
 if not ok:failures.append(name)
def req(uid,method,path,body=None,**kw):
 return requests.request(method,WEB+path,headers={"Authorization":"Bearer "+tokens[uid]},json=body,timeout=30,**kw)
def count_messages():
 return int(dbrow("SELECT count(*) AS n FROM internal_messages WHERE conversation_id=:c",{"c":group_id})["n"])
try:
 for uid in (OWNER_ID,EMPLOYEE_ID,VIEWER_ID):
  token,sid,_=issue(uid,"V7 attachment and report QA");tokens[uid]=token;sessions.append((uid,sid))
 r=req(OWNER_ID,"POST","/api/internal-messenger/conversations",{"account_id":ACCOUNT,"kind":"GROUP","title":GROUP_TITLE,"member_user_ids":[EMPLOYEE_ID]})
 assert r.status_code==200,r.status_code
 group_id=r.json()["conversation_id"]
 files=[]
 for ext,fmt,mime in [("png","PNG","image/png"),("jpg","JPEG","image/jpeg")]:
  data=io.BytesIO();Image.new("RGB",(32,32),(40,90,160)).save(data,format=fmt);files.append((ext,mime,data.getvalue()))
 files.append(("pdf","application/pdf",b"%PDF-1.4\n1 0 obj<</Type /Catalog /Pages 2 0 R>>endobj\n2 0 obj<</Type /Pages /Count 0 /Kids []>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"))
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  owner=browser.new_page(viewport={"width":1280,"height":800})
  emp=browser.new_page(viewport={"width":1280,"height":800},accept_downloads=True)
  for uid,page in [(OWNER_ID,owner),(EMPLOYEE_ID,emp)]:
   page.goto(WEB+"/login",wait_until="domcontentloaded")
   page.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[tokens[uid],ACCOUNT])
   page.goto(WEB+f"/messenger?account_id={ACCOUNT}&internal_conversation_id={group_id}",wait_until="domcontentloaded")
   page.get_by_role("heading",name=GROUP_TITLE,exact=True).wait_for(timeout=25000)
  for ext,mime,data in files:
   try:
    name=PREFIX+"."+ext
    composer(owner).get_by_role("button",name="Добавить",exact=True).click()
    with owner.expect_response(lambda r:"/attachments/upload" in r.url and r.request.method=="POST",timeout=30000) as upload:
     composer(owner).locator('input[type="file"]').set_input_files({"name":name,"mimeType":mime,"buffer":data})
    r=upload.value;check(ext.upper()+"_UPLOAD",r.status==200,r.status)
    assert r.status==200,r.status
    uploaded.append(r.json()["upload"]["storage_ref"])
    with owner.expect_response(lambda r:re.search("/messages/[0-9]+/attachments$",r.url) and r.request.method=="POST",timeout=30000) as linked:
     composer(owner).get_by_role("button",name="Отправить",exact=True).click()
    check(ext.upper()+"_MESSAGE",linked.value.status==200,linked.value.status)
    aid=linked.value.json()["attachment"]["id"]
    card=emp.locator('[class*="attachmentCard"]').filter(has_text=name)
    card.wait_for(timeout=20000)
    with emp.expect_download(timeout=20000) as download:card.click()
    saved=download.value.path()
    check(ext.upper()+"_SECOND_USER_DOWNLOAD",hashlib.sha256(Path(saved).read_bytes()).digest()==hashlib.sha256(data).digest())
    deny=req(VIEWER_ID,"GET",f"/api/internal-messenger/attachments/{aid}")
    check(ext.upper()+"_NONMEMBER_DENIAL",deny.status_code in (403,404),deny.status_code)
   except Exception as exc:check(ext.upper()+"_EXCEPTION",False,type(exc).__name__+":"+str(exc))
  before=count_messages()
  commands=["/баланс","/реклама 7","/реклама 30","/реактивация","/ab 30","/утро"]
  for command in commands:
   try:
    composer(owner).locator("textarea").fill(command)
    composer(owner).get_by_role("button",name="Отправить",exact=True).click()
    preview=owner.locator('[class*="composerReportPreview"]');preview.wait_for(timeout=25000)
    check("SLASH_"+command,count_messages()==before)
    owner.get_by_role("button",name="Закрыть сводку",exact=True).click()
   except Exception as exc:check("SLASH_"+command,False,type(exc).__name__+":"+str(exc))
  for label in ["Утренний отчёт","Финансы","Реклама · 7 дней","Реактивация","A/B · 30 дней"]:
   try:
    composer(owner).get_by_role("button",name="Добавить",exact=True).click()
    composer(owner).get_by_role("button",name=re.compile("Сводка BORIS")).click()
    composer(owner).locator('[class*="composerReportMenu"] > button').filter(has_text=label).click()
    owner.locator('[class*="composerReportPreview"]').wait_for(timeout=25000)
    check("COMPOSER_"+label,count_messages()==before)
    owner.get_by_role("button",name="Закрыть сводку",exact=True).click()
   except Exception as exc:check("COMPOSER_"+label,False,type(exc).__name__+":"+str(exc))
  try:
   composer(owner).locator("textarea").fill("/утро");composer(owner).get_by_role("button",name="Отправить",exact=True).click()
   owner.locator('[class*="composerReportPreview"]').wait_for(timeout=20000)
   with owner.expect_response(lambda r:"/api/messenger-reports/share" in r.url,timeout=25000) as share:
    owner.get_by_role("button",name="Вставить в чат",exact=True).click()
   check("REPORT_TO_CHAT",share.value.status==200 and count_messages()==before+1,share.value.status)
  except Exception as exc:check("REPORT_TO_CHAT",False,type(exc).__name__+":"+str(exc))
  browser.close()
 negatives=[("MIME","fake.png","image/png",b"<script>alert(1)</script>",415),
 ("EXECUTABLE","bad.exe","application/octet-stream",b"MZ"+b"0"*100,415),
 ("SVG","bad.svg","image/svg+xml",b"<svg onload='alert(1)'/>",415),
 ("HTML","bad.html","text/html",b"<html><script>alert(1)</script></html>",415),
 ("DANGEROUS_EXTENSION","bad.ps1","image/png",files[0][2],415),
 ("OVERSIZE","big.pdf","application/pdf",b"%PDF-1.4\n"+b"x"*(21*1024*1024),413),
 ("TRAVERSAL","../escape.png","image/png",files[0][2],400)]
 for label,name,mime,data,expected in negatives:
  r=req(OWNER_ID,"POST",f"/api/internal-messenger/conversations/{group_id}/attachments/upload",files={"file":(name,data,mime)})
  if r.status_code==200:uploaded.append(r.json()["upload"]["storage_ref"])
  check("ATTACHMENT_"+label,r.status_code==expected,{"actual":r.status_code,"expected":expected})
except Exception as exc:check("UNEXPECTED",False,type(exc).__name__+":"+str(exc))
finally:
 for uid,sid in sessions:revoke(uid,sid)
 checks["cleanup"]=cleanup(before_dm)
 for ref in uploaded:
  parts=ref.split(":")
  if len(parts)==3 and parts[0]=="internal" and parts[1]==str(group_id) and re.fullmatch("[a-f0-9]{32}",parts[2]):
   (Path("/root/BORIS/data/internal-messenger-attachments")/parts[1]/parts[2]).unlink(missing_ok=True)
print(json.dumps({"status":"PASS" if not failures else "FAIL","checks":checks,"failures":failures},ensure_ascii=False,indent=2))
raise SystemExit(1 if failures else 0)


