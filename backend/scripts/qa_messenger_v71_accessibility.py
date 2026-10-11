"""Keyboard acceptance of current deployed Messenger; read-only navigation."""
from pathlib import Path
exec(Path("/root/BORIS/frontend/scripts/qa-messenger-v70-multiuser.py").read_text().split("access0,team0=set_viewer()")[0])
token,sid,_=issue(OWNER_ID,"V71 keyboard acceptance");checks={}
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch(headless=True,args=["--no-sandbox"]);p=b.new_page(viewport={"width":1440,"height":900})
  workspace(p,token)
  new=p.get_by_role("button",name="Новый чат",exact=True);new.focus();p.keyboard.press("Enter")
  dlg=p.get_by_role("dialog",name="Новый чат");dlg.wait_for()
  search=dlg.get_by_role("textbox",name="Поиск сотрудника");search.wait_for();search.focus()
  p.keyboard.press("Tab");forward=p.evaluate("document.activeElement.tagName")
  p.keyboard.press("Shift+Tab")
  checks["TAB_SHIFT_TAB"]=forward=="BUTTON" and search.evaluate("(e)=>e===document.activeElement")
  p.keyboard.press("Escape");dlg.wait_for(state="hidden")
  checks["NEW_CHAT_ESCAPE_FOCUS"]=new.evaluate("(e)=>e===document.activeElement")
  more=p.get_by_role("button",name="Ещё",exact=True);more.focus();p.keyboard.press("Enter")
  p.get_by_role("dialog",name="Ещё").wait_for();p.keyboard.press("Escape")
  checks["MORE_ESCAPE_FOCUS"]=more.evaluate("(e)=>e===document.activeElement")
  selector=p.get_by_role("button",name="Разрешённый аккаунт",exact=True);selector.focus();p.keyboard.press("Enter")
  box=p.get_by_role("listbox",name="Разрешённый аккаунт");box.wait_for()
  p.keyboard.press("End");checks["DROPDOWN_END"]=box.get_by_role("option").last.evaluate("(e)=>e===document.activeElement")
  p.keyboard.press("Home");checks["DROPDOWN_HOME"]=box.get_by_role("option").first.evaluate("(e)=>e===document.activeElement")
  p.keyboard.press("ArrowDown");checks["DROPDOWN_ARROW"]=box.get_by_role("option").nth(1).evaluate("(e)=>e===document.activeElement")
  p.keyboard.press("Escape");checks["DROPDOWN_ESCAPE_FOCUS"]=selector.evaluate("(e)=>e===document.activeElement")
  checks["NATIVE_SELECTS_ZERO"]=p.locator("select").count()==0
  b.close()
except Exception as e:checks["UNEXPECTED"]=type(e).__name()+":"+str(e)
finally:revoke(OWNER_ID,sid)
print(json.dumps({"status":"PASS" if checks and all(v is True for v in checks.values()) else "FAIL","checks":checks},ensure_ascii=False,indent=2))
assert checks and all(v is True for v in checks.values())
