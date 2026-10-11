"""Real browser navigation/metadata; login responses stubbed, no production sessions."""
import json,os
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
WEB=os.getenv("QA_WEB","https://boris-ai.pro");out={}
with sync_playwright() as pw:
 for engine in ["chromium","webkit"]:
  browser=getattr(pw,engine).launch(headless=True,**({"args":["--no-sandbox"]} if engine=="chromium" else {}))
  try:
   context=browser.new_context(service_workers="block",viewport={"width":390,"height":844})
   page=context.new_page()
   page.goto(WEB+"/messenger?section=settings",wait_until="domcontentloaded")
   page.wait_for_url("**/messenger/login",timeout=15000)
   assert page.locator('link[rel="manifest"]').get_attribute("href")=="/messenger/manifest.webmanifest"
   assert page.locator('link[rel="apple-touch-icon"]').get_attribute("href")=="/messenger/apple-touch-icon.png"
   icon=context.request.get(WEB+"/messenger/apple-touch-icon.png")
   assert icon.status==200 and icon.body()[:8]==b"\x89PNG\r\n\x1a\n"
   out[engine+"_cold_launch_metadata"]="PASS"
   context.close()
   for saved in [None,"/messenger?account_id=fixture&internal_conversation_id=7"]:
    context=browser.new_context(service_workers="block")
    def api(route):
     path=urlparse(route.request.url).path
     data={"status":"ok"}
     if path=="/api/auth/login":data.update(access_token="browser-fixture-not-a-real-token",user={"id":999999,"role":"client","status":"active"})
     elif path=="/api/legal/messenger/status":data["accepted"]=True
     elif path in ["/api/messenger-workspace/accounts","/api/accounts/list"]:data["accounts"]=[]
     elif path=="/api/auth/me":data["user"]={"id":999999,"role":"client","status":"active"}
     route.fulfill(status=200,content_type="application/json",body=json.dumps(data))
    context.route("**/api/**",api)
    page=context.new_page();page.goto(WEB+"/messenger/login",wait_until="domcontentloaded")
    if saved:page.evaluate("(v)=>localStorage.setItem('boris_post_login_return',v)",saved)
    page.locator('input[type="email"]').fill("fixture@example.com")
    page.locator('input[type="password"]').fill("fixture-unused-password")
    page.get_by_role("button",name="Войти",exact=True).click()
    page.wait_for_url(WEB+(saved or "/messenger"),timeout=15000)
    out[engine+("_deep_link_login" if saved else "_fresh_storage_login")]="PASS"
    context.close()
  finally:browser.close()
print(json.dumps({"status":"PASS","checks":out,"boundary":"browser login response stubbed; no physical iPhone PASS claimed"},ensure_ascii=False,indent=2))
