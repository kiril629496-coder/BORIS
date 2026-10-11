"""Real browser account switch with delayed old-account HTTP responses."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import asyncio
from playwright.async_api import async_playwright
token,sid,_=issue(OWNER_ID,"V71 account isolation")
B="qa_dry_penza";checks={}
async def main():
 async with async_playwright() as pw:
  browser=await pw.chromium.launch(headless=True,args=["--no-sandbox"])
  for label,section,extra in [("chats","chats",""),("clients","clients",""),("tasks","crm",""),("members","team",""),("reports","reports",""),("finance","reports","&report=finance"),("reactivation","reports","&report=reactivation"),("ab","reports","&report=ab_tests")]:
   if label in ("chats","clients","tasks","members"):continue
   context=await browser.new_context(viewport={"width":1440,"height":900})
   p=await context.new_page()
   await p.goto(WEB+"/login")
   await p.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[token,ACCOUNT])
   await p.goto(WEB+f"/messenger?account_id={ACCOUNT}&section={section}"+extra)
   button=p.get_by_role("button",name="Разрешённый аккаунт",exact=True)
   try:await button.wait_for(timeout=20000)
   except Exception:
    checks[label]={"ok":False,"url":p.url,"body":(await p.locator("body").inner_text())[:1800]}
    raise
   await p.wait_for_function("()=>Boolean(document.querySelector('[class*=internalSurface],[class*=workspaceSurface],[class*=shell]'))",timeout=15000)
   await p.wait_for_timeout(500)
   pending=asyncio.Event();delayed=[]
   async def delay(route):
    response=await route.fetch()
    delayed.append(route.request.url.split("?")[0]);pending.set()
    await asyncio.sleep(2)
    try:await route.fulfill(response=response)
    except Exception:pass
   await p.route("**/api/internal-messenger/readiness?**",delay)
   await p.evaluate("a=>{fetch('/api/internal-messenger/readiness?account_id='+a,{headers:{Authorization:'Bearer '+localStorage.getItem('boris_token')}}).catch(()=>{})}",ACCOUNT)
   await asyncio.wait_for(pending.wait(),10)
   await p.evaluate("""([a,b])=>{
    sessionStorage.setItem('v71frames',JSON.stringify({frames:0,leaks:[]}));
    const tick=()=>{
     if(localStorage.getItem('account_id')===b){
      const data=JSON.parse(sessionStorage.getItem('v71frames'));data.frames++;
      const oldUrl=new URL(location.href).searchParams.get('account_id')===a;
      const surface=document.querySelector('[class*=internalSurface],[class*=workspaceSurface],[class*=shell]');
      if(oldUrl&&surface)data.leaks.push('old workspace rendered after switch');
      sessionStorage.setItem('v71frames',JSON.stringify(data));
     }
     requestAnimationFrame(tick);
    };requestAnimationFrame(tick);
   }""",[ACCOUNT,B])
   await button.click()
   await p.get_by_role("listbox",name="Разрешённый аккаунт").get_by_role("option").filter(has_text="QA сухой прогон Пенза").click()
   await p.wait_for_url("**account_id="+B,timeout=20000)
   await p.get_by_role("button",name="Разрешённый аккаунт",exact=True).filter(has_text="Пенза").wait_for(timeout=20000)
   await p.wait_for_timeout(2200)
   data=await p.evaluate("JSON.parse(sessionStorage.getItem('v71frames'))")
   text_body=await p.locator("body").inner_text()
   checks[label]={"ok":not data["leaks"] and "QA сухой прогон Москва" not in text_body and bool(delayed),"old_document_frames":data["frames"],"leaks":data["leaks"],"delayed_requests":len(delayed)}
   await context.close()
  await browser.close()
try:asyncio.run(main())
except Exception as e:checks["UNEXPECTED"]={"ok":False,"detail":type(e).__name__+":"+str(e)}
finally:revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if checks and all(v["ok"] for v in checks.values()) else "FAIL","checks":checks},ensure_ascii=False,indent=2))
assert len(checks)==4 and all(v["ok"] for v in checks.values())
