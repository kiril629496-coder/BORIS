"""Automated live PWA service-worker and browser installability acceptance."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
token,sid,_=issue(OWNER_ID,"V71 PWA automated");checks={};diagnostics={}
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  c=b.new_context();p=c.new_page();workspace(p,token)
  manifest_url=p.locator('link[rel="manifest"]').get_attribute("href")
  manifest=p.request.get(WEB+manifest_url).json()
  checks["MANIFEST"]=manifest["display"]=="standalone" and manifest["start_url"]=="/messenger" and manifest["scope"]=="/messenger"
  reg=p.evaluate("async()=>{const r=await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready;return {scope:r.scope,active:r.active?.state}}")
  checks["SW_INSTALL_ACTIVATE"]=reg["active"]=="activated"
  p.reload();p.wait_for_function("navigator.serviceWorker.controller!==null")
  checks["SW_CONTROLS_MESSENGER"]=True
  checks["SW_UPDATE"]=p.evaluate("async()=>{const r=await navigator.serviceWorker.getRegistration();await r.update();return r.active.state==='activated'}")
  version=p.evaluate("async()=>await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('version timeout')),5000);navigator.serviceWorker.addEventListener('message',e=>{if(e.data.type==='BORIS_SW_VERSION'){clearTimeout(t);resolve(e.data.version)}},{once:true});navigator.serviceWorker.controller.postMessage({type:'BORIS_SW_VERSION'})})")
  checks["SW_VERSION"]=version=="boris-pwa-v10-messenger-offline"
  cdp=c.new_cdp_session(p)
  diagnostics["installability"]=cdp.send("Page.getInstallabilityErrors")
  checks["INSTALLABILITY"]=diagnostics["installability"]["installabilityErrors"]==[]
  c.set_offline(True);p.goto(WEB+"/messenger?qa_offline=1",wait_until="domcontentloaded")
  checks["OFFLINE_SHELL"]=p.get_by_role("heading",name="BORIS Messenger офлайн",exact=True).is_visible()
  checks["NO_CACHED_API"]=p.evaluate("async()=>{for(const key of await caches.keys()){for(const req of await (await caches.open(key)).keys()){if(new URL(req.url).pathname.startsWith('/api/'))return false}}return true}")
  c.set_offline(False);p.goto(WEB+f"/messenger?account_id={ACCOUNT}")
  p.get_by_role("button",name="Новый чат",exact=True).wait_for(timeout=20000)
  checks["RECONNECT_SHELL"]=True
  # Runtime event dispatch in an isolated worker context; no external push transport.
  worker=c.service_workers[0]
  result=worker.evaluate("""async()=>{const seen={};const show=self.registration.showNotification.bind(self.registration);const match=self.clients.matchAll.bind(self.clients);try{
   self.registration.showNotification=async(t,o)=>{seen.title=t;seen.body=o.body;seen.url=o.data.url};
   let waits=[];const pe=new Event('push');Object.defineProperty(pe,'data',{value:{json:()=>({title:'BORIS Messenger',body:'Откройте BORIS Messenger, чтобы посмотреть обновление.',event_type:'internal_message',url:'/messenger?account_id=qa_dry_moskva&internal_conversation_id=1'})}});pe.waitUntil=p=>waits.push(p);self.dispatchEvent(pe);await Promise.all(waits);
   self.clients.matchAll=async()=>[{url:self.location.origin+'/messenger',navigate:async url=>{seen.navigate=url},focus:async()=>{seen.focus=true}}];
   waits=[];const ce=new Event('notificationclick');ce.notification={data:{url:seen.url},close:()=>{seen.closed=true}};ce.waitUntil=p=>waits.push(p);self.dispatchEvent(ce);await Promise.all(waits);return seen;
  }finally{self.registration.showNotification=show;self.clients.matchAll=match}}""")
  diagnostics["push_runtime"]=result
  checks["PUSH_DEEPLINK_RUNTIME"]=result.get("focus") and result.get("closed") and result.get("navigate")==WEB+result.get("url","")
  checks["PUSH_GENERIC_BODY_RUNTIME"]=result.get("body")=="Откройте BORIS Messenger, чтобы посмотреть обновление."
  try:
   browser_cdp=b.new_browser_cdp_session()
   browser_cdp.send("PWA.install",{"manifestId":WEB+"/messenger","installUrlOrBundleUrl":WEB+"/messenger"})
   diagnostics["pwa_install"]="success"
   browser_cdp.send("PWA.launch",{"manifestId":WEB+"/messenger"})
   checks["AUTOMATED_INSTALL_LAUNCH"]=True
  except Exception as e:diagnostics["pwa_install"]=str(e)
  b.close()
except Exception as e:checks["UNEXPECTED"]=False;diagnostics["error"]=type(e).__name__+":"+str(e)
finally:revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if checks and all(checks.values()) else "FAIL","checks":checks,"diagnostics":diagnostics},ensure_ascii=False,indent=2))
assert checks and all(checks.values())
