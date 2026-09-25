#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,sys
sys.path.insert(0,'/root/BORIS/backend')
import qa_suite
from playwright.sync_api import sync_playwright
WEB='https://boris-ai.pro'
with sync_playwright() as pw:
    br=pw.chromium.launch(headless=True,args=['--no-sandbox'])
    ctx=br.new_context(viewport={'width':390,'height':844})
    page=ctx.new_page()
    page.goto(WEB+'/login',wait_until='domcontentloaded',timeout=30000); page.wait_for_timeout(400)
    if '/login' in page.url:
        page.locator('input[type=email]').fill(qa_suite.OWNER[0]); page.locator('input[type=password]').fill(qa_suite.OWNER[1]); page.get_by_text('Войти',exact=True).first.click(); page.wait_for_timeout(2200)
    out={}
    for route in ('/messages','/dashboard/command-center'):
        page.goto(WEB+route,wait_until='domcontentloaded',timeout=30000); page.wait_for_timeout(900)
        out[route]=page.evaluate("""() => [...document.querySelectorAll('button,a')].map(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {tag:el.tagName,text:(el.getAttribute('aria-label')||el.textContent||'').trim().slice(0,80),w:Math.round(r.width),h:Math.round(r.height),display:s.display,cls:String(el.className||'').slice(0,160),testid:el.getAttribute('data-testid'),html:el.outerHTML.slice(0,700)}}).filter(x=>x.text&&(x.w<36||x.h<36))""")
    print(json.dumps(out,ensure_ascii=False,indent=2))
    ctx.close(); br.close()
