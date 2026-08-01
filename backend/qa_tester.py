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
EMAIL="qa_test@borisqa.ru"; PWD="QaTest2026!"
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
    "newbie": ("Ты Гарик, кладёшь плитку и брусчатку в Подмосковье, не технарь. Впервые открыл этот "
               "экран рекламного сервиса, хочешь подключить свой Авито и запустить объявления, но в "
               "интерфейсах не разбираешься и боишься сложных слов."),
    "skeptic": ("Ты Грант, продаёшь бетон в Калужской области. Уже заплатил за сервис и придирчиво "
                "смотришь, за что деньги: хочешь видеть результат — сколько объявлений, есть ли отклик, "
                "что сервис реально сделал. Раздражаешься от пустых экранов и обещаний без цифр."),
    "agency": ("Ты управляешь агентством и ведёшь 10 клиентских Авито-аккаунтов сразу. Тебе важно быстро "
               "переключаться между клиентами и с одного взгляда понимать статус каждого: где всё ок, а "
               "где проблема. Бесит, когда аккаунты путаются и непонятно, где что горит."),
    "chatter": ("Ты клиент, который общается с ассистентом Борисом в чате: пишешь ему задачи вроде "
                "«сделай объявления по моему сайту». Оцени, понимает ли он тебя, не теряется ли, понятны "
                "ли его ответы, и делает ли он работу сам или только советует."),
    "mobile": ("Ты занятой предприниматель, открыл кабинет с телефона на бегу между делами. Смотри, не "
               "сломана ли мобильная вёрстка, читаемо ли всё, помещается ли в экран, можно ли что-то "
               "сделать быстро одной рукой."),
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
               "есть ли лишние или повторяющиеся кнопки; не перегружен ли экран; что упростить. "
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
        # Вход с повтором: /login отдаётся статически, и клик может уйти в пустоту,
        # пока React не ожил (гидратация). Раньше это давало плавающие 0/8 при
        # полностью рабочем проде. Ждём форму, жмём, ПРОВЕРЯЕМ смену адреса.
        _logged = False
        for _try in range(3):
            try:
                page.goto(f"{BASE_WEB}/login", timeout=25000)
                page.wait_for_selector("input[type=email]", timeout=15000)
                page.wait_for_timeout(1500)          # даём гидратации завершиться
                page.fill("input[type=email]", EMAIL)
                page.fill("input[type=password]", PWD)
                page.get_by_text("Войти", exact=True).first.click(timeout=8000)
                page.wait_for_url(lambda u: "/login" not in u, timeout=20000)
                _logged = True
                break
            except Exception as _e:
                print(f"попытка входа {_try + 1} не удалась:", str(_e)[:70])
                page.wait_for_timeout(2000)
        if not _logged:
            print("ВХОД НЕ ВЫПОЛНЕН — остальные проверки недостоверны")
        print("URL после логина:", page.url)
        # Вход может вести на /dashboard/home - дальше тестируем старый
        # кабинет, поэтому переходим в него явно: не зависим от точки входа.
        page.goto(f"{BASE_WEB}/dashboard", timeout=25000)
        page.wait_for_timeout(2500)
        # открыть список клиентских аккаунтов
        try:
            page.click("text=Показать список клиентских аккаунтов", timeout=6000)
            page.wait_for_timeout(2000)
        except Exception as e:
            print("список не открылся:", str(e)[:80])
        # зайти в первый аккаунт (первая кнопка "Открыть")
        try:
            page.get_by_text("Открыть", exact=False).first.click(timeout=6000)
            page.wait_for_timeout(3500)
            print("Зашли в аккаунт, URL:", page.url)
        except Exception as e:
            print("не зашли в аккаунт:", str(e)[:80])
        # теперь проходим вкладки внутри аккаунта
        for name, click_text in TABS:
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
                entry["ux"]=ux_review(os.path.join(QA_DIR,shot))
            results.append(entry)
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
    doc=f'<html><head><meta charset="utf-8"><title>QA {ts}</title></head><body style="font-family:sans-serif;max-width:900px;margin:20px auto"><h2>🧪 Автотест Бориса {ts}</h2><p>Пройдено: {ok}/{total} связок. Ошибки 5xx/консоль: {len(set(errors))}</p>{rows}</body></html>'
    rpath=os.path.join(QA_DIR,f"report_{ts}.html")
    open(rpath,"w",encoding="utf-8").write(doc)
    url=f"https://boris-ai.pro/images/qa/report_{ts}.html"
    # Telegram
    chat=os.environ.get("QA_CHAT_ID") or "-1003952038222"
    # развёрнутый список: что сломано (конкретные связки с ошибками)
    failed = [r for r in results if not r["ok"]]
    passed = [r for r in results if r["ok"]]
    fail_lines = ""
    if failed:
        fail_lines = "\n\n❌ Проблемы:\n" + "\n".join(
            f"  • {r['name']} — {r['err'][:60] if r.get('err') else 'ошибка загрузки'} ({r['time']}с)" for r in failed
        )
    ok_names = ", ".join(r["name"] for r in passed[:12])
    ok_line = f"\n\n✅ Работает ({len(passed)}): {ok_names}" if passed else ""
    msg=f"🧪 Автотест Бориса {ts}\n{ok}/{total} связок | ошибок 5xx: {len(set(errors))}{fail_lines}{ok_line}\n\nПодробно: {url}"
    if chat: send_telegram_message(chat, msg, thread_id=17)
    print("Отчёт:", rpath)
    print("URL:", url)
    print(f"Результат: {ok}/{total}")
    return url

def audit_landing():
    """Аудит публичного лендинга глазами гостя-предпринимателя: ПК + моб, скорость, vision."""
    ts=datetime.datetime.now().strftime("%Y%m%d_%H%M")
    views=[("ПК","desktop",1440,900),("Моб","mobile",390,844)]
    cards=[]
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True, args=["--no-sandbox"])
        for label,key,w,h in views:
            ctx=b.new_context(viewport={"width":w,"height":h})
            page=ctx.new_page()
            errs=[]
            page.on("response", lambda r: errs.append(f"{r.status} {r.url}") if r.status>=500 else None)
            t0=time.time()
            ok=True; err=""
            try:
                page.goto(f"{BASE_WEB}/", timeout=45000, wait_until="networkidle")
            except Exception as e:
                ok=False; err=str(e)[:150]
            load_t=round(time.time()-t0,2)
            shot=f"landing_{key}_{ts}.png"
            try:
                page.screenshot(path=os.path.join(QA_DIR,shot), full_page=True)
            except Exception as e:
                shot=""; err=(err+" | screenshot: "+str(e)[:80]).strip(" |")
            ux = ux_review_landing(os.path.join(QA_DIR,shot), label) if shot else ""
            cards.append({"label":label,"load":load_t,"shot":shot,"ux":ux,"ok":ok,"err":err,"e5":len(set(errs))})
            ctx.close()
        b.close()
    # HTML отчёт
    blocks=""
    for c in cards:
        badge="✅" if c["ok"] else "❌"
        img=f'<img src="{c["shot"]}" style="max-width:440px;border:1px solid #ccc;border-radius:8px">' if c["shot"] else "нет скрина"
        errline=f'<span style="color:red">{html.escape(c["err"])}</span>' if c["err"] else ""
        ux_html=('<div style="margin-top:10px;padding:12px;background:#FFF7ED;border-radius:8px;font-size:14px;white-space:pre-wrap">🧑‍💼 Глазами гостя:\n'+html.escape(c["ux"])+'</div>') if c["ux"] else ""
        blocks+=f'<div style="margin:18px 0;padding:16px;border:1px solid #e3e7f0;border-radius:12px"><b>{badge} {c["label"]}</b> — загрузка {c["load"]}с, ошибок 5xx: {c["e5"]} {errline}<br>{img}{ux_html}</div>'
    doc=f'<html><head><meta charset="utf-8"><title>Лендинг-аудит {ts}</title></head><body style="font-family:sans-serif;max-width:960px;margin:20px auto"><h2>🌐 Аудит лендинга БОРИС {ts}</h2><p>Публичная витрина {BASE_WEB}/ глазами холодного посетителя (без входа).</p>{blocks}</body></html>'
    rpath=os.path.join(QA_DIR,f"landing_audit_{ts}.html")
    open(rpath,"w",encoding="utf-8").write(doc)
    url=f"https://boris-ai.pro/images/qa/landing_audit_{ts}.html"
    chat=os.environ.get("QA_CHAT_ID") or "-1003952038222"
    dt="/".join(f"{c['label']} {c['load']}с" for c in cards)
    msg=f"🌐 Аудит лендинга {ts}\nСкорость: {dt}\n\nПодробно: {url}"
    if chat: send_telegram_message(chat, msg, thread_id=17)
    print("Лендинг-отчёт:", rpath); print("URL:", url)
    return url


def ux_review_landing(shot_path, device_label):
    """Vision-разбор лендинга глазами предпринимателя, впервые попавшего на страницу."""
    try:
        import base64
        from proxy_pool import get_intl_requests_proxies
        api_key=os.getenv("OPENAI_API_KEY")
        if not api_key or not os.path.exists(shot_path): return ""
        b64=base64.b64encode(open(shot_path,"rb").read()).decode()
        prompt=(f"Это {device_label}-версия главной страницы рекламного сервиса. Ты владелец малого бизнеса, "
                "случайно попал сюда впервые, рекламируешься на Авито и ищешь, чем упростить себе жизнь. "
                "Ответь честно и коротко (маркеры '- ', максимум 6 пунктов): "
                "1) за 5 секунд понятно, что это за сервис и чем полезен? "
                "2) есть ли внятный призыв зарегистрироваться/попробовать и заметная кнопка? "
                "3) видно ли цену или тариф, или непонятно сколько стоит? "
                "4) что отпугивает или вызывает недоверие? "
                "5) удобно ли читать на этом экране (не мелкий ли текст, не ломается ли вёрстка)? "
                "6) чего не хватает, чтобы захотелось оставить заявку? "
                "Говори как обычный предприниматель, не как дизайнер.")
        proxies=get_intl_requests_proxies()
        r=requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json"},
            json={"model":"gpt-4o-mini","messages":[{"role":"user","content":[
                {"type":"text","text":prompt},
                {"type":"image_url","image_url":{"url":"data:image/png;base64,"+b64}}]}],"max_tokens":420},
            proxies=proxies, timeout=60)
        if r.status_code==200:
            return r.json()["choices"][0]["message"]["content"].strip()
        return "vision статус "+str(r.status_code)
    except Exception as e:
        return "Лендинг-разбор не удался: "+str(e)[:80]


if __name__=="__main__":
    if "--landing" in sys.argv:
        audit_landing()
    else:
        run()
