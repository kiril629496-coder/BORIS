import os, time, datetime, html
import sys
QA_MODE = "ux" if "--ux" in sys.argv else "fast"
for _l in open('/root/BORIS/backend/.env'):
    _l=_l.strip()
    if _l and not _l.startswith('#') and '=' in _l:
        _k,_v=_l.split('=',1); os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

import requests
from playwright.sync_api import sync_playwright
from app.telegram_bot import send_telegram_message

BASE_API="http://127.0.0.1:8000"
BASE_WEB="https://boris-ai.pro"
EMAIL="qa_client@borisqa.ru"; PWD="QaTest2026!"
import sys as _sys
PERSONA_ARG = _sys.argv[_sys.argv.index("--persona")+1] if "--persona" in _sys.argv else "newbie"
QA_DIR="/root/BORIS/backend/images/qa"
os.makedirs(QA_DIR, exist_ok=True)

# связки для проверки: (название, путь после /dashboard или клик по тексту)
TABS=[
    ("Объявления","Объявления"),
    ("Задачи и план","Задачи и план"),
    ("Режим работы","Режим работы"),
    ("Маркетинг","Маркетинг"),
    ("Продажи","Продажи"),
    ("Баннеры и картинки","Баннеры и картинки"),
]

PERSONAS = {
    "newbie": ("Ты владелец малого бизнеса, впервые открыл этот экран рекламного сервиса. Хочешь "
               "запустить рекламу на Avito, но совсем не разбираешься в интерфейсах и боишься сложных слов."),
    "intermediate": ("Ты предприниматель, уже пользовался рекламными сервисами и в теме. Тебе важно, чтобы "
                     "всё было логично и быстро: без лишних шагов, понятно где что, сразу ясно что делать и "
                     "сколько стоит. Раздражают нелогичный порядок экранов и неочевидные переходы."),
}

def ux_review(shot_path, persona="newbie"):
    """OpenAI Vision смотрит на скриншот глазами клиента и ищет запутанность."""
    try:
        import base64, requests
        from proxy_pool import get_intl_requests_proxies
        api_key=os.getenv("OPENAI_API_KEY")
        if not api_key or not os.path.exists(shot_path): return ""
        b64=base64.b64encode(open(shot_path,"rb").read()).decode()
        _tail=(" Посмотри честно и коротко (маркеры '- '): что непонятно; куда нажать неочевидно; "
               "есть ли лишние или повторяющиеся кнопки; не перегружен ли экран; что упростить; понятно ли, как пользоваться и за что платить. "
               "Говори как обычный пользователь, не как дизайнер. Максимум 5 пунктов.")
        prompt = PERSONAS.get(persona, PERSONAS["newbie"]) + _tail
        proxies=get_intl_requests_proxies()
        r=requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},
            json={"model":"gpt-4o-mini","messages":[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":"data:image/png;base64,"+b64}}]}],"max_tokens":350},
            proxies=proxies, timeout=60)
        if r.status_code==200:
            return r.json()["choices"][0]["message"]["content"].strip()
        return "vision статус "+str(r.status_code)
    except Exception as e:
        return "UX-разбор не удался: "+str(e)[:80]

def login():
    r=requests.post(f"{BASE_API}/api/auth/login", json={"email":EMAIL,"password":PWD}, timeout=60)
    d=r.json()
    return d.get("access_token"), d.get("user",{}).get("account_id")

def run():
    ts=datetime.datetime.now().strftime("%Y%m%d_%H%M")
    token, acc = login()
    results=[]
    with sync_playwright() as p:
        b=p.chromium.launch(headless=True, args=["--no-sandbox"])
        page=b.new_page()
        errors=[]
        page.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
        page.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status>=500 else None)
        # === СЦЕНАРИЙ АГЕНТСТВА: логин owner -> список -> аккаунт -> вкладки ===
        page.goto(f"{BASE_WEB}/login", timeout=25000); page.wait_for_timeout(1000)
        page.fill("input[type=email]", EMAIL); page.fill("input[type=password]", PWD)
        page.get_by_text("Войти", exact=True).first.click(timeout=5000)
        login_entry={"name":"Вход в кабинет","ok":False,"time":0,"err":"","shot":""}
        _t0=time.time()
        try:
            page.wait_for_url("**/dashboard**", timeout=45000)
            login_entry["ok"]=True
        except Exception:
            login_entry["err"]=f"не попали в кабинет за 45с, остались на {page.url} — вход подвисает"
        login_entry["time"]=round(time.time()-_t0,2)
        _shot=f"shot_{ts}_login.png"
        try:
            page.screenshot(path=os.path.join(QA_DIR,_shot)); login_entry["shot"]=_shot
        except: pass
        results.append(login_entry)
        print("URL после логина:", page.url, "| вход за", login_entry["time"], "с")
        # Вход может вести на /dashboard/home - дальше тестируем старый
        # кабинет, поэтому переходим в него явно: не зависим от точки входа.
        page.goto(f"{BASE_WEB}/dashboard", timeout=25000)
        page.wait_for_timeout(2500)
        # частный клиент: аккаунт один, выбирать не надо, сразу вкладки
        print("Частный клиент, дашборд:", page.url)

        # === ФАЗА 1: ОНБОРДИНГ (визард нового клиента) ===
        onboarding_visible = False
        entry={"name":"Онбординг: визард","ok":False,"time":0,"err":"","shot":""}
        t0=time.time()
        try:
            page.wait_for_timeout(4000)
            body = page.inner_text("body")
            if "С чего начнём" in body:
                onboarding_visible = True
                entry["ok"] = True
                missing=[t for t in ["Перенести товары","Подтянуть объявления","Создать объявления","Другое"] if t not in body]
                if missing:
                    entry["ok"]=False
                    entry["err"]="нет плиток: "+", ".join(missing)
            elif page.get_by_text("Открыть", exact=False).count() > 0:
                entry["ok"]=True
                entry["err"]="визарда нет: аккаунт уже подключён"
            elif "Добавить аккаунт" in body and "Аккаунты Авито" in body:
                entry["err"]="визард НЕ открылся автоматически — клиент видит пустой экран"
            else:
                entry["ok"]=True
                entry["err"]="экран не распознан"
        except Exception as e:
            entry["err"]=str(e)[:150]
        entry["time"]=round(time.time()-t0,2)
        shot=f"shot_{ts}_onboarding.png"
        try:
            page.screenshot(path=os.path.join(QA_DIR,shot)); entry["shot"]=shot
        except: pass
        if entry["shot"] and QA_MODE=="ux":
            entry["ux"]=ux_review(os.path.join(QA_DIR,shot), PERSONA_ARG)
        results.append(entry)
        print("Онбординг:", "визард показан" if onboarding_visible else "визарда нет")

        # === ФАЗА 2: вкладки (только если аккаунт подключён) ===
        if not onboarding_visible:
            enter={"name":"Вход в аккаунт","ok":False,"time":0,"err":"","shot":""}
            _t=time.time()
            try:
                # частник с одним аккаунтом попадает в кабинет сразу — кнопки «Открыть» нет.
                # ждём её мягко: есть → клик; нет за 5с → считаем что уже в кабинете.
                try:
                    page.wait_for_selector("text=Открыть", timeout=5000)
                    page.get_by_text("Открыть", exact=False).first.click(timeout=8000)
                    page.wait_for_timeout(4000)
                except Exception:
                    pass  # частник — уже в кабинете
                enter["ok"]=True
            except Exception as e:
                enter["err"]="не удалось зайти в аккаунт: "+str(e)[:100]
            enter["time"]=round(time.time()-_t,2)
            results.append(enter)
            print("Вход в аккаунт:", "ок" if enter["ok"] else enter["err"])
        tabs_to_check = [] if onboarding_visible else TABS
        for name, click_text in tabs_to_check:
            entry={"name":name,"ok":False,"time":0,"err":"","shot":""}
            t0=time.time()
            try:
                page.locator("button.b-nav", has_text=click_text).first.click(timeout=6000)
                page.wait_for_timeout(1800)
                entry["ok"]=True
            except Exception as e:
                entry["err"]=str(e)[:150]
            entry["time"]=round(time.time()-t0,2)
            shot=f"shot_{ts}_{name}.png".replace(" ","_")
            try:
                page.screenshot(path=os.path.join(QA_DIR,shot)); entry["shot"]=shot
            except: pass
            if entry["shot"] and QA_MODE=="ux":
                entry["ux"]=ux_review(os.path.join(QA_DIR,shot), PERSONA_ARG)
            results.append(entry)
        # === ФАЗА 3: Продажи → Менеджер, Продажи → РОП, Соцсети (до оплаты) ===
        def _snap(nm, shot_key):
            e={"name":nm,"ok":False,"time":0,"err":"","shot":""}
            t=time.time()
            sh=f"shot_{ts}_{shot_key}.png"
            try:
                page.wait_for_timeout(1500)
                page.screenshot(path=os.path.join(QA_DIR,sh)); e["shot"]=sh; e["ok"]=True
            except Exception as ex: e["err"]=str(ex)[:120]
            e["time"]=round(time.time()-t,2)
            if e["shot"] and QA_MODE=="ux": e["ux"]=ux_review(os.path.join(QA_DIR,sh), PERSONA_ARG)
            results.append(e)
        if not onboarding_visible:
            try:
                page.locator("button.b-nav", has_text="Продажи").first.click(timeout=6000); page.wait_for_timeout(1500)
                page.get_by_text("Открыть менеджера", exact=False).first.click(timeout=6000)
                _snap("Продажи → Менеджер (до оплаты)", "sales_mop")
                page.get_by_text("Назад к выбору", exact=False).first.click(timeout=5000); page.wait_for_timeout(1000)
                page.get_by_text("Открыть РОПа", exact=False).first.click(timeout=6000)
                _snap("Продажи → РОП (до оплаты)", "sales_rop")
            except Exception as ex:
                results.append({"name":"Продажи → МОП/РОП","ok":False,"time":0,"err":str(ex)[:150],"shot":""})
        try:
            page.goto(f"{BASE_WEB}/social", timeout=25000); page.wait_for_timeout(3000)
            _snap("Соцсети (постинг)", "social")
        except Exception as ex:
            results.append({"name":"Соцсети","ok":False,"time":0,"err":str(ex)[:150],"shot":""})
        # === Новый рабочий стол: проверка по data-testid, не по русскому тексту ===
        _BASE = globals().get("BASE_WEB") or "https://boris-ai.pro"
        for _nm, _path, _tid in [
            ("Рабочий стол", "/dashboard/home", "home-root"),
            ("Каталог сценариев", "/dashboard/scenarios", "scenarios-root"),
        ]:
            _e = {"name": _nm, "ok": False, "time": 0, "err": "", "shot": ""}
            _t0 = time.time()
            try:
                page.goto(_BASE + _path, timeout=25000)
                page.wait_for_selector('[data-testid="%s"]' % _tid, timeout=15000)
                page.wait_for_timeout(1200)
                _e["ok"] = True
            except Exception as _ex:
                _e["err"] = str(_ex)[:150]
            _e["time"] = round(time.time() - _t0, 2)
            _sh = "shot_%s_%s.png" % (ts, _tid)
            try:
                page.screenshot(path=os.path.join(QA_DIR, _sh)); _e["shot"] = _sh
            except Exception:
                pass
            results.append(_e)
        b.close()
    # HTML отчёт
    ok=sum(1 for r in results if r["ok"]); total=len(results)
    rows=""
    for r in results:
        badge="✅" if r["ok"] else "❌"
        img=f'<img src="{r["shot"]}" style="max-width:420px;border:1px solid #ccc;border-radius:8px">' if r["shot"] else "нет скрина"
        ux=r.get("ux","")
        ux_html=('<div style="margin-top:10px;padding:10px;background:#FFF7ED;border-radius:8px;font-size:14px;white-space:pre-wrap">🎨 UX-разбор (глазами клиента):\n'+html.escape(ux)+'</div>') if ux else ""
        rows+=f'<div style="margin:18px 0;padding:14px;border:1px solid #e3e7f0;border-radius:10px"><b>{badge} {html.escape(r["name"])}</b> — {r["time"]}с {("<span style=color:red>"+html.escape(r["err"])+"</span>") if r["err"] else ""}<br>{img}{ux_html}</div>'
    doc=f'<html><head><meta charset="utf-8"><title>QA {ts}</title></head><body style="font-family:sans-serif;max-width:900px;margin:20px auto"><h2>🧪 Автотест: Частный клиент {ts}</h2><p>Пройдено: {ok}/{total} связок. Ошибки 5xx/консоль: {len(set(errors))}</p>{rows}</body></html>'
    rpath=os.path.join(QA_DIR,f"report_{ts}.html")
    open(rpath,"w",encoding="utf-8").write(doc)
    url=f"https://boris-ai.pro/images/qa/report_{ts}.html"
    # Telegram
    chat=os.environ.get("DIRECTOR_CHAT_ID")
    msg=f"🧪 Автотест: Частный клиент {ts}\n✅ {ok}/{total} связок\nОшибок 5xx: {len(set(errors))}\n\nПодробно: {url}"
    if chat: send_telegram_message(chat, msg)
    print("Отчёт:", rpath)
    print("URL:", url)
    print(f"Результат: {ok}/{total}")
    return url

if __name__=="__main__":
    run()
