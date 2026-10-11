"""Real PWA install and standalone in isolated headed Chromium, exact session cleanup."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import tempfile
out={};token,sid,_=issue(OWNER_ID,"V71 isolated PWA installation")
try:
 with tempfile.TemporaryDirectory(prefix="boris-pwa-install-") as profile:
  with sync_playwright() as pw:
   context=pw.chromium.launch_persistent_context(profile,headless=False,args=["--no-sandbox"])
   try:
    page=context.pages[0];workspace(page,token)
    cdp=context.new_cdp_session(page)
    out["browser"]=cdp.send("Browser.getVersion")
    out["installability"]=cdp.send("Page.getInstallabilityErrors")
    manifest_id="https://boris-ai.pro/messenger"
    try:
     out["install"]=cdp.send("PWA.install",{"manifestId":manifest_id,"installUrlOrBundleUrl":manifest_id})
     out["state"]=cdp.send("PWA.getOsAppState",{"manifestId":manifest_id})
     out["user_display_setting"]=cdp.send("PWA.changeAppUserSettings",{"manifestId":manifest_id,"displayMode":"standalone"})
     out["launch"]=cdp.send("PWA.launch",{"manifestId":manifest_id})
     page.wait_for_timeout(3000)
     out["pages"]=[{"url":p.url,"standalone":p.evaluate("matchMedia('(display-mode: standalone)').matches")} for p in context.pages]
     out["standalone_pass"]=any(p["standalone"] and "/messenger" in p["url"] for p in out["pages"])
    except Exception as e:out["capability_error"]=str(e)
    finally:
     if "install" in out:out["uninstall"]=cdp.send("PWA.uninstall",{"manifestId":manifest_id})
   finally:context.close()
finally:revoke(OWNER_ID,sid)
print(json.dumps(out,ensure_ascii=False,indent=2))
assert out.get("standalone_pass"),"real standalone not established"
