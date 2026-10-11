"""V10 permission-denied browser acceptance in the isolated Messenger QA tenant."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])

import os
for _key in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy"):
    os.environ.pop(_key, None)
os.environ["NO_PROXY"]="127.0.0.1,localhost,boris-ai.pro"
os.environ["no_proxy"]=os.environ["NO_PROXY"]

token,sid,_=issue(OWNER_ID,"V10 permission UX")
checks={}
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        context=browser.new_context()
        page=context.new_page()
        page.add_init_script("""
          (() => {
            const reject = async () => { throw new DOMException('Permission denied by QA', 'NotAllowedError'); };
            if (navigator.mediaDevices) {
              Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { configurable: true, value: reject });
            } else {
              Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: reject } });
            }
          })();
        """)
        workspace(page,token)
        open_general(page)
        page.get_by_role("button",name="Записать голосовое сообщение",exact=True).click()
        page.get_by_role("status").filter(has_text="Разрешите доступ к микрофону в настройках браузера/устройства.").last.wait_for(timeout=8000)
        checks["MIC_DENIED_HUMAN_TEXT"]=True
        body=page.locator("body").inner_text()
        checks["MIC_NO_TECH_ERROR"]=not any(x in body for x in ("NotAllowedError","PermissionDeniedError","DOMException"))
        page.get_by_role("button",name="Записать видеосообщение",exact=True).click()
        page.get_by_role("status").filter(has_text="Разрешите доступ к камере и микрофону в настройках браузера/устройства.").last.wait_for(timeout=8000)
        checks["VIDEO_DENIED_HUMAN_TEXT"]=True
        body=page.locator("body").inner_text()
        checks["VIDEO_NO_TECH_ERROR"]=not any(x in body for x in ("NotAllowedError","PermissionDeniedError","DOMException"))
        # The settings block must expose all three device permissions.
        page.get_by_role("button",name="Ещё",exact=True).click()
        page.get_by_role("dialog",name="Ещё").get_by_role("button",name="Уведомления и приложение",exact=False).click()
        page.get_by_text("Разрешения устройства",exact=True).wait_for(timeout=8000)
        checks["SETTINGS_PERMISSION_CARD"]=True
        checks["SETTINGS_MIC"]=page.get_by_text("Микрофон",exact=True).first.is_visible()
        checks["SETTINGS_CAMERA"]=page.get_by_text("Камера",exact=True).first.is_visible()
        checks["SETTINGS_NOTIFICATIONS"]=page.get_by_text("Уведомления",exact=True).first.is_visible()
        browser.close()
finally:
    revoke(OWNER_ID,sid)

print(json.dumps({"status":"PASS" if checks and all(checks.values()) else "FAIL","checks":checks},
                 ensure_ascii=False,indent=2))
assert checks and all(checks.values())
