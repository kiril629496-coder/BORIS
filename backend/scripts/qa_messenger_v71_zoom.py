"""Real Chromium browser zoom via X11 keyboard in an isolated Xvfb display."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
import ctypes,ctypes.util,subprocess
t,sid,_=issue(OWNER_ID,"V71 real 200 percent zoom");checks={}
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=False,args=["--no-sandbox","--window-size=1440,1000","--force-device-scale-factor=1"])
  context=b.new_context(no_viewport=True);p=context.new_page()
  p.goto(WEB+"/login");p.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[t,ACCOUNT])
  p.goto(WEB+f"/messenger?account_id={ACCOUNT}&section=settings")
  p.get_by_role("heading",name="Настройки Messenger",exact=True).wait_for(timeout=25000)
  p.bring_to_front();before=p.evaluate("({w:innerWidth,dpr:devicePixelRatio})")
  tree=subprocess.check_output(["xwininfo","-root","-tree"],text=True)
  windows=[int(m.group(1),16) for line in tree.splitlines() if '"Chromium-browser")' in line for m in [re.search(r"(0x[0-9a-f]+)",line)] if m]
  assert windows,"No Chromium X11 window: "+tree
  x=ctypes.CDLL(ctypes.util.find_library("X11"));xt=ctypes.CDLL(ctypes.util.find_library("Xtst"))
  x.XOpenDisplay.argtypes=[ctypes.c_char_p];x.XOpenDisplay.restype=ctypes.c_void_p
  d=x.XOpenDisplay(None);assert d
  x.XSetInputFocus.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
  x.XStringToKeysym.argtypes=[ctypes.c_char_p];x.XStringToKeysym.restype=ctypes.c_ulong
  x.XKeysymToKeycode.argtypes=[ctypes.c_void_p,ctypes.c_ulong];x.XKeysymToKeycode.restype=ctypes.c_uint
  x.XFlush.argtypes=[ctypes.c_void_p]
  xt.XTestFakeKeyEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
  x.XSetInputFocus(d,windows[0],1,0);x.XFlush(d)
  ctrl=x.XKeysymToKeycode(d,x.XStringToKeysym(b"Control_L"));eq=x.XKeysymToKeycode(d,x.XStringToKeysym(b"equal"))
  for _ in range(5):
   for key,down in [(ctrl,1),(eq,1),(eq,0),(ctrl,0)]:xt.XTestFakeKeyEvent(d,key,down,0)
   x.XFlush(d);p.wait_for_timeout(250)
  p.wait_for_timeout(500);after=p.evaluate("({w:innerWidth,dpr:devicePixelRatio,scroll:document.documentElement.scrollWidth})")
  checks["REAL_BROWSER_ZOOM_200"]={"ok":1.95<=after["dpr"]/before["dpr"]<=2.05 and .48<=after["w"]/before["w"]<=.52,"before":before,"after":after}
  checks["ZOOM_SETTINGS_NO_HORIZONTAL_SCROLL"]={"ok":after["scroll"]<=after["w"]+1}
  for label,heading in [("Отчёты и сводки","Отчёты и сводки"),("Безопасность и документы","Безопасность")]:
   p.get_by_role("button",name="Ещё",exact=True).click()
   p.get_by_role("dialog",name="Ещё").get_by_role("button",name=label,exact=False).click()
   p.get_by_role("heading",name=heading,exact=True).first.wait_for(timeout=15000)
   checks["ZOOM_"+label]={"ok":p.evaluate("document.documentElement.scrollWidth<=innerWidth+1")}
  p.goto(WEB+f"/messenger?account_id={ACCOUNT}")
  open_general(p);composer(p).locator("textarea").fill("QA zoom unsent draft")
  send_button=composer(p).get_by_role("button",name="Отправить",exact=True)
  send_button.scroll_into_view_if_needed()
  checks["ZOOM_COMPOSER_CONTROLS"]={"ok":send_button.is_visible() and send_button.is_enabled() and p.evaluate("document.documentElement.scrollWidth<=innerWidth+1")}
  b.close()
except Exception as e:checks["UNEXPECTED"]={"ok":False,"detail":type(e).__name__+":"+str(e)}
finally:revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if checks and all(v["ok"] for v in checks.values()) else "FAIL","checks":checks},ensure_ascii=False,indent=2))
assert checks and all(v["ok"] for v in checks.values())
