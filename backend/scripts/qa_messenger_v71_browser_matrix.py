"""Final viewport and keyboard acceptance in dedicated QA tenant."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
token,sid,_=issue(OWNER_ID,"V71 browser matrix");checks={};errors=[]
def check(key,ok,detail=None):checks[key]={"ok":bool(ok),"detail":detail}
def measure(page,key,locator=None):
 data=page.evaluate("()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,native:document.querySelectorAll('select').length})")
 ok=data["scroll"]<=data["width"]+1 and data["native"]==0
 if locator is not None:
  box=locator.bounding_box();data["box"]=box
  ok=ok and box is not None and box["x"]>=-1 and box["x"]+box["width"]<=data["width"]+1
 check(key,ok,data)
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
  for width,height in [(320,568),(360,800),(390,844),(430,932),(1024,768),(1280,800),(1440,900)]:
   p=browser.new_page(viewport={"width":width,"height":height});p.on("pageerror",lambda e:errors.append(str(e)))
   p.goto(WEB+"/login",wait_until="domcontentloaded")
   p.evaluate("([t,a])=>{localStorage.setItem('boris_token',t);localStorage.setItem('account_id',a)}",[token,ACCOUNT])
   p.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded")
   p.get_by_role("button",name="Новый чат",exact=True).wait_for(timeout=25000)
   measure(p,str(width)+"_CHATS")
   p.get_by_role("button",name="Новый чат",exact=True).click()
   dlg=p.get_by_role("dialog",name="Новый чат");dlg.wait_for()
   measure(p,str(width)+"_NEW_DM",dlg)
   dlg.get_by_role("button",name="Группа",exact=True).click()
   dlg.get_by_role("textbox",name="Название новой группы").fill("QA viewport draft")
   measure(p,str(width)+"_NEW_GROUP",dlg)
   p.keyboard.press("Escape");dlg.wait_for(state="hidden",timeout=5000)
   check(str(width)+"_DIALOG_ESCAPE",True)
   p.get_by_role("button",name="Ещё",exact=True).click()
   more=p.get_by_role("dialog",name="Ещё");measure(p,str(width)+"_MORE",more)
   p.keyboard.press("Escape")
   for section,label,heading in [("team","Команда и доступы",None),("settings","Уведомления и приложение","Настройки Messenger"),("security","Безопасность и документы","Безопасность"),("reports","Отчёты и сводки","Отчёты и сводки")]:
    p.get_by_role("button",name="Ещё",exact=True).click()
    p.get_by_role("dialog",name="Ещё").get_by_role("button",name=label,exact=False).click()
    p.wait_for_url("**section="+section,timeout=15000)
    if heading:p.get_by_role("heading",name=heading,exact=True).first.wait_for(timeout=15000)
    else:p.get_by_role("button",name="＋ Пригласить",exact=True).wait_for(timeout=15000)
    measure(p,str(width)+"_"+section.upper())
   selector=p.get_by_role("button",name="Разрешённый аккаунт",exact=True)
   selector.focus();p.keyboard.press("Enter")
   choices=p.get_by_role("listbox",name="Разрешённый аккаунт");choices.wait_for()
   measure(p,str(width)+"_CUSTOM_DROPDOWN",choices)
   p.keyboard.press("Escape");choices.wait_for(state="hidden")
   check(str(width)+"_CUSTOM_KEYBOARD",True)
   p.goto(WEB+f"/messenger?account_id={ACCOUNT}",wait_until="domcontentloaded")
   open_general(p)
   composer(p).locator("textarea").wait_for(state="visible",timeout=15000)
   measure(p,str(width)+"_COMPOSER",composer(p))
   composer(p).get_by_role("button",name="Добавить",exact=True).click()
   composer(p).get_by_role("button",name=re.compile("Сводка BORIS")).click()
   menu=composer(p).locator('[class*="composerReportMenu"]')
   measure(p,str(width)+"_REPORT_MENU",menu)
   box=menu.bounding_box()
   check(str(width)+"_REPORT_MENU_VERTICAL",box is not None and box["y"]>=0 and box["y"]+box["height"]<=height+1,box)
   p.close()
  browser.close()
 check("JS_RUNTIME_ERRORS_ZERO",not errors,errors)
except Exception as exc:check("UNEXPECTED",False,{"error":type(exc).__name__+":"+str(exc),"body":p.locator("body").inner_text()[:1800],"url":p.url})
finally:revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if checks and all(v["ok"] for v in checks.values()) else "FAIL","checks":checks},ensure_ascii=False,indent=2))
assert checks and all(v["ok"] for v in checks.values())
