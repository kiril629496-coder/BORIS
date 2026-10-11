#!/usr/bin/env python3
import json,re,sys,time
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite, requests
from playwright.sync_api import sync_playwright

WEB="https://boris-ai.pro"
ACCOUNT="planetazayavki_65985"

def auth():
    r=requests.post(WEB+"/api/auth/login",json={"email":qa_suite.OWNER[0],"password":qa_suite.OWNER[1]},timeout=20)
    r.raise_for_status()
    d=r.json()
    return d["access_token"], (d.get("user") or {}).get("role","owner")

def login(page, token, role):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.evaluate("""([token,email,role,account])=>{
      localStorage.setItem('boris_token',token);
      localStorage.setItem('boris_user_email',email);
      localStorage.setItem('boris_user_role',role);
      localStorage.setItem('boris_currentAccount',account);
    }""",[token,qa_suite.OWNER[0],role,ACCOUNT])

def main():
    token,role=auth()
    out={"status":"PASS","cases":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        ctx=br.new_context(viewport={"width":1440,"height":1000},user_agent="BORIS-QA-Campaign-Scope/1.0")
        p=ctx.new_page()
        network=[]
        browser_errors=[]
        def capture(req):
            u=req.url
            if "/api/campaign/" in u and ("/readiness" in u or "/ai-marketer" in u):
                network.append({"url":u,"method":req.method})
        p.on("request",capture)
        p.on("pageerror",lambda exc: browser_errors.append(str(exc)))
        login(p,token,role)
        p.goto(WEB+"/dashboard/campaign?account_id="+ACCOUNT,wait_until="domcontentloaded",timeout=30000)
        p.wait_for_timeout(2500)
        sel=p.locator('select').filter(has=p.locator('option[value="250"]')).first
        if not sel.count():
            print(json.dumps({"status":"FAIL","reason":"campaign_selector_missing"},ensure_ascii=False))
            return 2
        for cid in ("252","250","249"):
            network.clear()
            sel.select_option(cid)
            p.wait_for_timeout(2200)
            value=sel.input_value()
            text=p.locator("#campaign-readiness").inner_text()
            m=re.search(r"Campaign\s+#(\d+)",text)
            rendered=int(m.group(1)) if m else 0
            row={"requested":int(cid),"selector":int(value or 0),"rendered":rendered,"text":text[:800],"network":list(network)}
            out["cases"].append(row)
            if int(value or 0)!=int(cid) or rendered!=int(cid):
                out["status"]="FAIL"
        ctx.close(); br.close()
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
