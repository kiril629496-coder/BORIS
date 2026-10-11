"""Automated PWA and keyboard acceptance. Isolated browser context, synthetic session."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
checks={};details={};tok,sid,_=issue(OWNER_ID,"V71 final PWA a11y")
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  context=b.new_context(viewport={"width":1280,"height":800})
  p=context.new_page();workspace(p,tok);open_general(p)
  cdp=context.new_cdp_session(p)
  manifest=cdp.send("Page.getAppManifest")
  details["manifest_errors"]=manifest.get("errors",[])
  data=json.loads(manifest.get("data") or "{}")
  checks["MANIFEST"]=data.get("display")=="standalone" and data.get("scope")=="/messenger" and data.get("start_url")=="/messenger" and not manifest.get("errors")
  details["installability"]=cdp.send("Page.getInstallabilityErrors")
  checks["INSTALLABILITY"]=details["installability"].get("installabilityErrors")==[]
  p.evaluate("async()=>{await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;}")
  p.reload(wait_until="domcontentloaded")
  p.wait_for_function("!!navigator.serviceWorker.controller",timeout=20000)
  checks["SERVICE_WORKER_CONTROLS_PAGE"]=True
  version=p.evaluate("""async()=>await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('version_timeout')),5000);const handler=e=>{if(e.data?.type==='BORIS_SW_VERSION'){clearTimeout(timer);navigator.serviceWorker.removeEventListener('message',handler);resolve(e.data.version);}};navigator.serviceWorker.addEventListener('message',handler);navigator.serviceWorker.controller.postMessage({type:'BORIS_SW_VERSION'});})""")
  checks["UPDATE_MESSAGE_RUNTIME"]=bool(version);details["sw_version"]=version
  checks["UPDATE_CHECK_RUNTIME"]=p.evaluate("async()=>{const r=await navigator.serviceWorker.getRegistration();await r.update();return !!r.active;}")
  cache=p.evaluate("async()=>{const names=await caches.keys();const out=[];for(const name of names){const c=await caches.open(name);out.push(...(await c.keys()).map(r=>new URL(r.url).pathname));}return out;}")
  details["cached_paths"]=cache
  checks["OFFLINE_SHELL_CACHED"]="/messenger-offline.html" in cache
  checks["NO_PRIVATE_API_CACHE"]=not any(x.startswith("/api/") for x in cache)
  context.set_offline(True);p.goto(WEB+"/messenger?qa_offline=1",wait_until="domcontentloaded")
  offline=p.locator("body").inner_text();details["offline_text"]=offline[:500]
  checks["OFFLINE_SHELL_RUNTIME"]="нет" in offline.lower() or "связ" in offline.lower() or "offline" in offline.lower()
  context.set_offline(False);p.goto(WEB+"/messenger?account_id="+ACCOUNT,wait_until="domcontentloaded")
  p.get_by_text("Онлайн",exact=True).first.wait_for(timeout=20000);checks["RECONNECT_AFTER_OFFLINE"]=True
  # Real keyboard traversal, not DOM click substitution.
  p.keyboard.press("Tab");first=p.evaluate("document.activeElement?.outerHTML")
  p.keyboard.press("Tab");second=p.evaluate("document.activeElement?.outerHTML")
  p.keyboard.press("Shift+Tab");back=p.evaluate("document.activeElement?.outerHTML")
  checks["TAB_SHIFT_TAB"]=first==back and first!=second
  more=p.get_by_role("button",name="Ещё",exact=True).first
  more.focus();p.keyboard.press("Enter")
  dialog=p.get_by_role("dialog",name="Ещё");dialog.wait_for(timeout=10000)
  checks["MORE_KEYBOARD_ENTER"]=True
  p.keyboard.press("Escape");dialog.wait_for(state="hidden",timeout=10000)
  checks["MORE_ESCAPE_FOCUS_RETURN"]=more.evaluate("(e)=>document.activeElement===e")
  p.goto(WEB+"/messenger?account_id="+ACCOUNT+"&section=team",wait_until="domcontentloaded")
  trigger=p.locator('button[aria-haspopup="listbox"]').first;trigger.wait_for(timeout=20000);trigger.focus();p.keyboard.press("Enter")
  options=p.get_by_role("option");options.first.wait_for(timeout=10000)
  p.keyboard.press("ArrowDown")
  checks["DROPDOWN_ARROW_FOCUS"]=p.evaluate("document.activeElement?.getAttribute('role')==='option'")
  p.keyboard.press("End")
  checks["DROPDOWN_END"]=options.last.evaluate("(e)=>document.activeElement===e")
  p.keyboard.press("Home")
  checks["DROPDOWN_HOME"]=options.first.evaluate("(e)=>document.activeElement===e")
  p.keyboard.press("Escape")
  checks["DROPDOWN_ESCAPE_FOCUS_RETURN"]=trigger.evaluate("(e)=>document.activeElement===e")
  details["standalone_note"]="Physical installed standalone window is not claimed by headless emulation."
  b.close()
finally:revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if all(checks.values()) else "FAIL","checks":checks,"details":details},ensure_ascii=False,indent=2))
assert checks and all(checks.values())
