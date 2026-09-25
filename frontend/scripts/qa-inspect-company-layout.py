#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json,sys
sys.path.insert(0,'/root/BORIS/backend')
import qa_suite
from playwright.sync_api import sync_playwright
WEB='https://boris-ai.pro'
with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True,args=['--no-sandbox'])
    c=b.new_context(viewport={'width':320,'height':760})
    p=c.new_page(); p.goto(WEB+'/login',wait_until='domcontentloaded',timeout=30000); p.wait_for_timeout(400)
    if '/login' in p.url:
        p.locator('input[type=email]').fill(qa_suite.OWNER[0]); p.locator('input[type=password]').fill(qa_suite.OWNER[1]); p.get_by_text('Войти',exact=True).first.click(); p.wait_for_timeout(2200)
    p.goto(WEB+'/dashboard/company',wait_until='domcontentloaded',timeout=30000); p.wait_for_timeout(1200)
    out=p.evaluate("""() => {
      const pick=(sel)=>{const e=document.querySelector(sel);if(!e)return null;const r=e.getBoundingClientRect(),s=getComputedStyle(e);return {sel,rect:{x:r.x,y:r.y,w:r.width,h:r.height},display:s.display,gridTemplateColumns:s.gridTemplateColumns,minWidth:s.minWidth,width:s.width,padding:s.padding,gap:s.gap,overflowX:s.overflowX}};
      return {
        root:pick('[data-boris-area="company"]'),
        page:pick('.boris-company-reference-page'),
        grid:pick('.boris-company-form-grid'),
        card:pick('.boris-company-mini-card'),
        content:pick('.boris-company-mini-content'),
        input:pick('.boris-company-mini-content input'),
        allInputs:[...document.querySelectorAll('.boris-company-mini-content input')].map(e=>{const r=e.getBoundingClientRect();return {ph:e.getAttribute('placeholder'),x:r.x,w:r.width,h:r.height}})
      };
    }""")
    print(json.dumps(out,ensure_ascii=False,indent=2))
    c.close(); b.close()
