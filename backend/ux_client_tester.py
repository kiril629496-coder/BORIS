#!/root/BORIS/backend/venv/bin/python3
"""BORIS UX Client Tester.
Read-only browser journeys from several client personas. No publish/send/payment mutations.
Produces JSON + HTML feedback under images/qa/ux_client/.
"""
import os, json, time, datetime, html, re, traceback
import requests
from playwright.sync_api import sync_playwright

BASE_API = "http://127.0.0.1:8000"
BASE_WEB = "https://boris-ai.pro"
EMAIL = os.getenv("BORIS_QA_EMAIL", "qa_test@borisqa.ru")
PWD = os.getenv("BORIS_QA_PASSWORD", "QaTest2026!")
OUT = "/root/BORIS/backend/images/qa/ux_client"
TASK = os.getenv("BORIS_UX_TASK", "Пройти BORIS как реальный клиент: оценить удобство, логику, понятность, лишние действия и следующий шаг.")
os.makedirs(OUT, exist_ok=True)

PERSONAS = [
    {
        "id":"new_owner", "name":"Новый предприниматель",
        "about":"Первый день в BORIS, не технарь, хочет понять с чего начать и запустить рекламу.",
        "viewport":{"width":1440,"height":960},
        "routes":["/dashboard/command-center","/dashboard/connect-avito","/dashboard/campaign","/messages"],
    },
    {
        "id":"busy_mobile", "name":"Занятый владелец с телефона",
        "about":"Открывает BORIS на ходу; важны читаемость, крупные цели, минимум лишних действий.",
        "viewport":{"width":390,"height":844},
        "routes":["/dashboard/command-center","/messages","/dashboard/marketing","/dashboard/analytics"],
    },
    {
        "id":"agency", "name":"Агентство / несколько аккаунтов",
        "about":"Ведёт много клиентов и должен быстро понимать какой аккаунт выбран и где проблема.",
        "viewport":{"width":1440,"height":960},
        "routes":["/dashboard/command-center","/dashboard/connect-avito","/dashboard/campaign","/dashboard/analytics","/dashboard/crm"],
    },
    {
        "id":"sales_owner", "name":"Владелец, сфокусированный на продажах",
        "about":"Хочет быстро увидеть лиды, сообщения, сделки и понять что BORIS делает сам.",
        "viewport":{"width":1366,"height":900},
        "routes":["/dashboard/command-center","/messages","/dashboard/crm","/rop"],
    },
    {
        "id":"skeptic", "name":"Скептичный платящий клиент",
        "about":"Не верит обещаниям; ищет факты, статусы, понятные действия и реальный результат.",
        "viewport":{"width":1440,"height":960},
        "routes":["/dashboard/command-center","/dashboard/marketing","/dashboard/analytics","/dashboard/billing"],
    },
]

def login_token():
    r=requests.post(BASE_API+"/api/auth/login",json={"email":EMAIL,"password":PWD},timeout=30)
    r.raise_for_status(); return r.json().get("access_token")

def _norm(s): return re.sub(r"\s+"," ",str(s or "")).strip()

def inspect_page(page, persona, route):
    started=time.time(); issues=[]; positives=[]
    console=[]; network_errors=[]
    page.on("console", lambda m: console.append(m.text) if m.type=="error" else None)
    page.on("response", lambda r: network_errors.append({"status":r.status,"method":r.request.method,"url":r.url}) if r.status >= 400 else None)
    try:
        page.goto(BASE_WEB+route,wait_until="domcontentloaded",timeout=25000)
        page.wait_for_timeout(1200)
    except Exception as e:
        return {"route":route,"ok":False,"score":0,"issues":["Страница не открылась: "+str(e)[:140]],"positives":[],"time":round(time.time()-started,2)}
    body=_norm(page.locator("body").inner_text(timeout=8000))
    title=_norm(page.locator("h1").first.inner_text(timeout=1000)) if page.locator("h1").count() else ""
    buttons=page.locator("button").count(); links=page.locator("a").count()
    visible_controls=page.locator("button:visible, a:visible, input:visible, textarea:visible, select:visible").count()
    viewport_metrics=page.evaluate("""() => { const els=[...document.querySelectorAll('button,a,input,textarea,select')].filter(e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth}); const shell=els.filter(e=>e.closest('aside,header')).length; return {in_viewport:els.length,shell_in_viewport:shell,content_in_viewport:Math.max(0,els.length-shell)} }""")
    vw=persona["viewport"]["width"]
    overflow=page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 2")
    tiny=page.evaluate("""() => [...document.querySelectorAll('button,a,input,textarea,select')].filter(e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);const inView=r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth&&s.display!=='none'&&s.visibility!=='hidden';return inView&&(r.height<40||r.width<40)}).length""")
    long_text=page.evaluate("""() => [...document.querySelectorAll('main p,main small,main span,main div')].filter(e=>{const r=e.getBoundingClientRect();return e.children.length===0&&r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight&&(e.textContent||'').trim().length>260}).length""")
    unlabeled=page.evaluate("""() => [...document.querySelectorAll('button')].filter(b=>{const r=b.getBoundingClientRect();const visible=b.offsetParent!==null&&r.width>0&&r.height>0;const name=(b.getAttribute('aria-label')||b.getAttribute('title')||b.textContent||'').trim();return visible&&!name}).length""")
    if not title and route not in ["/messages","/rop"]: issues.append("Неочевидный заголовок экрана: клиенту сложнее понять, где он находится.")
    else: positives.append("Экран имеет понятную точку входа/заголовок.")
    if overflow: issues.append("Есть горизонтальный выход за экран — особенно критично на телефоне.")
    else: positives.append("Горизонтального переполнения не найдено.")
    if vw<=430 and tiny>3: issues.append(f"На мобильном найдено {tiny} мелких интерактивных элементов — часть действий неудобна пальцем.")
    content_controls=int(viewport_metrics.get("content_in_viewport",0))
    if content_controls>28: issues.append(f"В рабочей области первого экрана много действий ({content_controls}); стоит проверить визуальный приоритет главного шага.")
    elif content_controls<=18: positives.append("Рабочая область первого экрана не выглядит перегруженной.")
    if long_text>5: issues.append("Есть несколько длинных текстовых блоков без достаточного дробления — снижается сканируемость.")
    if unlabeled: issues.append(f"Есть кнопки без понятной текстовой/ARIA-подписи: {unlabeled}.")
    low=body.lower()
    if persona["id"]=="new_owner":
        if any(x in low for x in ["следующий шаг","начать","подключить","перейти к работе"]): positives.append("Новичку показывается следующий шаг или явное начало работы.")
        else: issues.append("Новичку не хватает явного ответа «что мне сделать первым».")
    if persona["id"]=="agency":
        if any(x in low for x in ["аккаунт","клиент"]): positives.append("Контекст аккаунта/клиента присутствует на экране.")
        else: issues.append("Для агентства неочевидно, к какому аккаунту относятся данные.")
    if persona["id"]=="sales_owner":
        if any(x in low for x in ["лид","сделк","сообщен","продаж"]): positives.append("Продажный контекст виден без глубокого поиска.")
        else: issues.append("Владельцу продаж не хватает явной связи экрана с лидами/сделками/сообщениями.")
    if persona["id"]=="skeptic":
        if any(x in low for x in ["факт","результат","сегодня","статус","план"]): positives.append("Есть язык фактов/статусов, а не только обещаний.")
        else: issues.append("Скептичному клиенту мало фактов и измеримых статусов.")
    if network_errors:
        for err in network_errors[:4]: issues.append(f"HTTP {err['status']}: {err['method']} {err['url'].replace(BASE_WEB,'')}")
    elif console: issues.append("Есть ошибки в console: "+"; ".join(console[:2])[:220])
    score=max(0,100-len(issues)*11)
    if overflow: score=max(0,score-12)
    if vw<=430 and tiny>3: score=max(0,score-8)
    stamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    shot=f"{persona['id']}_{route.strip('/').replace('/','_') or 'root'}_{stamp}.png"
    try: page.screenshot(path=os.path.join(OUT,shot),full_page=True)
    except Exception: shot=""
    return {"route":route,"title":title,"ok":True,"score":score,"issues":issues[:8],"positives":positives[:6],"visible_controls":visible_controls,"viewport_controls":viewport_metrics.get("in_viewport",0),"shell_controls":viewport_metrics.get("shell_in_viewport",0),"content_controls":viewport_metrics.get("content_in_viewport",0),"buttons":buttons,"links":links,"mobile_tiny_controls":tiny,"overflow":bool(overflow),"network_errors":network_errors[:8],"shot":shot,"time":round(time.time()-started,2)}

def main():
    ts=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    report={"run_id":ts,"task":TASK,"started_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"personas":[],"summary":{}}
    token=login_token()
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        for persona in PERSONAS:
            ctx=browser.new_context(viewport=persona["viewport"],ignore_https_errors=True)
            # Use localStorage auth contract used by the frontend; login form fallback remains available.
            p=ctx.new_page()
            p.goto(BASE_WEB+"/login",wait_until="domcontentloaded",timeout=20000)
            p.evaluate("(t)=>localStorage.setItem('boris_token',t)", token)
            persona_result={"id":persona["id"],"name":persona["name"],"about":persona["about"],"viewport":persona["viewport"],"pages":[]}
            for route in persona["routes"]:
                persona_result["pages"].append(inspect_page(p,persona,route))
            scores=[x["score"] for x in persona_result["pages"] if x.get("ok")]
            persona_result["score"]=round(sum(scores)/len(scores)) if scores else 0
            persona_result["top_issues"]=[i for x in persona_result["pages"] for i in x.get("issues",[])][:8]
            report["personas"].append(persona_result)
            ctx.close()
        browser.close()
    all_scores=[p["score"] for p in report["personas"]]
    all_issues=[i for p in report["personas"] for i in p["top_issues"]]
    report["summary"]={"score":round(sum(all_scores)/len(all_scores)) if all_scores else 0,"personas":len(report["personas"]),"pages":sum(len(p["pages"]) for p in report["personas"]),"issues":len(all_issues),"top_issues":all_issues[:12]}
    report["finished_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    jpath=os.path.join(OUT,f"report_{ts}.json")
    with open(jpath,"w",encoding="utf-8") as f: json.dump(report,f,ensure_ascii=False,indent=2)
    cards=[]
    for p in report["personas"]:
        pages=''.join(f"<li><b>{html.escape(x['route'])}</b> — {x['score']}/100"+("<ul>"+''.join('<li>'+html.escape(i)+'</li>' for i in x['issues'])+'</ul>' if x['issues'] else '')+"</li>" for x in p['pages'])
        cards.append(f"<section><h2>{html.escape(p['name'])} — {p['score']}/100</h2><p>{html.escape(p['about'])}</p><ul>{pages}</ul></section>")
    doc=f"""<!doctype html><meta charset='utf-8'><title>BORIS UX Client Tester</title><style>body{{font:15px/1.5 system-ui;max-width:1100px;margin:30px auto;color:#172033}}section{{border:1px solid #dfe7f3;border-radius:16px;padding:18px;margin:14px 0;box-shadow:0 8px 24px #1f3a6710}}h1,h2{{margin:0 0 8px}}.score{{font-size:28px;font-weight:800}}li{{margin:5px 0}}</style><h1>BORIS UX-тестировщик глазами клиентов</h1><p><b>Задача:</b> {html.escape(TASK)}</p><div class='score'>Общая оценка: {report['summary']['score']}/100</div><p>Персон: {report['summary']['personas']} · экранов: {report['summary']['pages']}</p>{''.join(cards)}"""
    hpath=os.path.join(OUT,f"report_{ts}.html")
    open(hpath,"w",encoding="utf-8").write(doc)
    open(os.path.join(OUT,"latest.json"),"w",encoding="utf-8").write(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({"status":"ok","run_id":ts,"score":report['summary']['score'],"json":jpath,"html":hpath},ensure_ascii=False))

if __name__=="__main__":
    try: main()
    except Exception:
        traceback.print_exc(); raise
