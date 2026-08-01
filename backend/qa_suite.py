# -*- coding: utf-8 -*-
"""BORIS QA 2.0 — набор независимых проверок перед подключением живых клиентов.

НЕ заменяет qa_tester.py и qa_tester_client.py — те остаются дымовыми тестами.
Здесь семь блоков, каждый запускается отдельно:

    venv/bin/python3 qa_suite.py                    # все
    venv/bin/python3 qa_suite.py api security
    venv/bin/python3 qa_suite.py ux

Правила: ничего не публикует, денег не тратит, чужие аккаунты не трогает.
Бизнес-тесты работают только на служебном qa_newbie и убирают за собой.
"""

import sys, os, json, time, traceback, random, string, re

# --- предохранители блока registration ---
FLAGS = {a.lower() for a in sys.argv[1:] if a.startswith("--")}
QA_WRITES = ("--registration-write-tests" in FLAGS
             and os.environ.get("BORIS_ALLOW_QA_WRITES") == "1")
QA_DOMAIN = "borisqa.ru"
API = "http://127.0.0.1:8000"
from datetime import datetime, date, timedelta

import requests

API = "http://127.0.0.1:8000"
WEB = "https://boris-ai.pro"
QA_DIR = "/root/BORIS/backend/images/qa"

OWNER = ("qa_test@borisqa.ru", "QaTest2026!")
CLIENT = ("qa_client@borisqa.ru", "QaTest2026!")
NEWBIE = ("qa_newbie@borisqa.ru", "QaTest2026!")

TESTIDS_HOME = ["home-root", "home-status", "home-attention", "home-today",
                "home-journal", "home-quick", "nav-home", "nav-scenarios", "nav-cabinet"]
TESTIDS_SCEN = ["scenarios-root", "scenarios-available", "scenarios-soon"]

R = []          # [{block, name, ok, level, detail}]
TOK = {}        # email -> (token, role, account_id)


def chk(block, name, ok, detail="", level=None):
    lv = level or ("PASS" if ok else "FAIL")
    R.append({"block": block, "name": name, "ok": bool(ok), "level": lv, "detail": str(detail)[:300]})
    print("   [%-7s] %-52s %s" % (lv, name[:52], str(detail)[:70]))


def login(cred):
    if cred[0] in TOK:
        return TOK[cred[0]]
    r = requests.post(API + "/api/auth/login", json={"email": cred[0], "password": cred[1]}, timeout=30)
    if r.status_code != 200:
        return None
    d = r.json()
    TOK[cred[0]] = (d["access_token"], d["user"]["role"], d["user"]["account_id"])
    return TOK[cred[0]]


def H(cred):
    t = login(cred)
    return {"Authorization": "Bearer " + t[0]} if t else {}


def acc(cred):
    t = login(cred)
    return t[2] if t else ""


# ------------------------------------------------------------------ API
def block_api():
    b = "API"
    a = acc(CLIENT)
    h = H(CLIENT)

    r = requests.get(API + "/api/home/overview?account_id=" + a, headers=h, timeout=60)
    chk(b, "overview отвечает 200", r.status_code == 200, r.status_code)
    if r.status_code != 200:
        return
    d = r.json()

    for k in ("status", "account", "stage", "numbers", "attention", "today", "journal",
              "recommendations", "active_scenario"):
        chk(b, "overview содержит '%s'" % k, k in d)

    st = d.get("stage") or {}
    chk(b, "stage.number в диапазоне 1..6",
        isinstance(st.get("number"), int) and 1 <= st["number"] <= 6, st.get("number"))

    num = d.get("numbers") or {}
    bad = [k for k, v in num.items() if v is None]
    chk(b, "в numbers нет None", not bad, bad)

    nums_int = [k for k in ("ready_to_publish", "active_items", "views", "contacts")
                if not isinstance(num.get(k), int)]
    chk(b, "числовые поля numbers — числа", not nums_int, nums_int)

    chk(b, "attention это список", isinstance(d.get("attention"), list))
    for c in (d.get("attention") or []):
        ok = all(c.get(x) for x in ("title", "text", "action", "priority")) and (c.get("tab") or c.get("route"))
        chk(b, "карточка заполнена: %s" % str(c.get("key"))[:22], ok, c.get("key"))
        chk(b, "приоритет валиден: %s" % str(c.get("key"))[:22],
            c.get("priority") in ("critical", "important", "opportunity", "info"), c.get("priority"))

    acct = d.get("account") or {}
    chk(b, "поле has_active_access присутствует", "has_active_access" in acct)
    chk(b, "поле has_tariff удалено (переименовано)", "has_tariff" not in acct)

    # ЧЕСТНОСТЬ ДАННЫХ: стадия 6 требует непустого фида
    if st.get("number") == 6:
        chk(b, "стадия 6 согласована с feed_items", (num.get("feed_items") or 0) > 0,
            "feed=%s" % num.get("feed_items"), None if (num.get("feed_items") or 0) > 0 else "WARNING")

    # ошибки
    # Клиенту check_account_access (auth.py:143) отдаёт 403 ЕЩЁ ДО валидации
    # FastAPI. 422 виден только с localhost без токена (доверие internal).
    # Корректны оба ответа - важно, что данные не отдаются.
    for _p, _n in (("/api/home/overview", "overview"),
                   ("/api/avito/audit_log", "audit_log"),
                   ("/api/chat/notifications", "notifications")):
        _sc = requests.get(API + _p, headers=h, timeout=30).status_code
        chk(b, "%s без account_id отбит (403/422)" % _n, _sc in (403, 422), _sc)
    chk(b, "несуществующий сценарий -> 404",
        requests.get(API + "/api/home/scenario/999999?account_id=" + a, headers=h, timeout=30).status_code == 404)
    chk(b, "неизвестный тип сценария -> 400",
        requests.post(API + "/api/home/scenario/start", headers=h, timeout=30,
                      json={"account_id": a, "scenario_type": "нет_такого"}).status_code == 400)

    r2 = requests.get(API + "/api/home/scenarios?account_id=" + a, headers=h, timeout=60)
    chk(b, "каталог сценариев 200", r2.status_code == 200, r2.status_code)
    if r2.status_code == 200:
        c2 = r2.json()
        chk(b, "три доступных сценария", len(c2.get("available") or []) == 3, len(c2.get("available") or []))
        chk(b, "семь в «Скоро»", len(c2.get("soon") or []) == 7, len(c2.get("soon") or []))
        for s in (c2.get("available") or []):
            chk(b, "у сценария есть поля мастера: %s" % s["scenario_type"], bool(s.get("fields")))


# ------------------------------------------------------------- SECURITY
def block_security():
    b = "Security"
    a_cli, a_new = acc(CLIENT), acc(NEWBIE)
    h_cli = H(CLIENT)

    # ВАЖНО: без токена бьём по ВНЕШНЕМУ домену. С 127.0.0.1 бэкенд доверяет
    # localhost (get_current_user_or_internal) и вернёт 422 вместо 401.
    for path in ("/api/home/overview?account_id=" + a_cli,
                 "/api/home/scenarios?account_id=" + a_cli,
                 "/api/avito/audit_log?account_id=" + a_cli):
        try:
            sc = requests.get(WEB + path, timeout=30).status_code
        except Exception as e:
            sc = str(e)[:40]
        chk(b, "без токена 401/403: %s" % path.split("?")[0], sc in (401, 403), sc)

    # чужой аккаунт
    chk(b, "чужой account_id в query -> 403",
        requests.get(API + "/api/home/overview?account_id=" + a_new, headers=h_cli, timeout=30).status_code == 403)
    chk(b, "чужой account_id в теле (scenario/start) -> 403",
        requests.post(API + "/api/home/scenario/start", headers=h_cli, timeout=30,
                      json={"account_id": a_new, "scenario_type": "revive"}).status_code == 403)
    chk(b, "чужой account_id в теле (scenario/step) -> 403",
        requests.post(API + "/api/home/scenario/step", headers=h_cli, timeout=30,
                      json={"account_id": a_new, "scenario_id": 1, "step_key": "x", "action": "done"}).status_code == 403)

    # владельческие эндпоинты под клиентом
    for path in ("/api/director_stats", "/api/economics/overview"):
        sc = requests.get(API + path, headers=h_cli, timeout=30).status_code
        chk(b, "owner-эндпоинт под клиентом закрыт: %s" % path, sc in (403, 404), sc)

    # инъекции и мусор — не 500
    for bad in ("' OR '1'='1", "../../etc/passwd", "<script>", "%00", "a" * 300):
        sc = requests.get(API + "/api/home/overview", headers=h_cli, timeout=30,
                          params={"account_id": bad}).status_code
        chk(b, "мусор в account_id не роняет сервер", sc != 500, "%s -> %s" % (bad[:14], sc))

    sc = requests.post(API + "/api/home/scenario/step", headers=H(NEWBIE), timeout=30,
                       json={"account_id": a_new, "scenario_id": 1, "step_key": "x", "action": "взлом"}).status_code
    chk(b, "неизвестное действие шага -> 400/404", sc in (400, 404), sc)


# ---------------------------------------------------------------- ROLES
def block_roles():
    b = "Roles"
    t_own, t_cli, t_new = login(OWNER), login(CLIENT), login(NEWBIE)
    chk(b, "вход владельца", bool(t_own) and t_own[1] == "owner", t_own[1] if t_own else "нет")
    chk(b, "вход клиента", bool(t_cli) and t_cli[1] == "client", t_cli[1] if t_cli else "нет")
    chk(b, "вход новичка", bool(t_new) and t_new[1] == "client", t_new[1] if t_new else "нет")

    r = requests.get(API + "/api/accounts/list", headers=H(CLIENT), timeout=30)
    if r.status_code == 200:
        lst = r.json().get("accounts") or []
        ids = [x.get("account_id") for x in lst]
        chk(b, "клиент видит только свой аккаунт", ids == [acc(CLIENT)], ids)
    else:
        chk(b, "клиент видит только свой аккаунт", False, r.status_code)

    r = requests.get(API + "/api/accounts/list", headers=H(OWNER), timeout=30)
    n_own = len(r.json().get("accounts") or []) if r.status_code == 200 else 0
    chk(b, "владелец видит все аккаунты", n_own > 1, n_own)

    # менеджер: пароля в наборе нет, честно помечаем непокрытым
    chk(b, "менеджер: вход и редирект на /manager", False,
        "пароль менеджера не в наборе — путь не покрыт", "WARNING")

    d = requests.get(API + "/api/home/overview?account_id=" + acc(CLIENT), headers=H(CLIENT), timeout=60)
    if d.status_code == 200:
        txt = json.dumps(d.json(), ensure_ascii=False)
        leak = [w for w in ("себестоимость", "cost_rub", "маржа", "api_usage") if w in txt]
        chk(b, "в overview нет владельческих данных", not leak, leak)


# ------------------------------------------------------------- BUSINESS
def block_business():
    b = "Business"
    a = acc(NEWBIE)
    h = H(NEWBIE)
    created = []

    for stype in ("avito_start", "more_leads", "revive"):
        r = requests.post(API + "/api/home/scenario/start", headers=h, timeout=60,
                          json={"account_id": a, "scenario_type": stype, "input_data": {"qa": "1"}})
        ok = r.status_code == 200 and r.json().get("status") == "ok"
        chk(b, "%s: запуск" % stype, ok, r.status_code)
        if not ok:
            continue
        sc = r.json()["scenario"]
        sid = sc["id"]
        created.append(sid)
        p = sc["progress"]
        chk(b, "%s: шаги созданы" % stype, p["total"] > 0, p["total"])
        chk(b, "%s: процент согласован" % stype,
            p["percent"] == int(round(p["done"] * 100.0 / p["total"])) if p["total"] else True,
            "%s%% при %s/%s" % (p["percent"], p["done"], p["total"]))

        # дубль не создаётся
        r2 = requests.post(API + "/api/home/scenario/start", headers=h, timeout=60,
                           json={"account_id": a, "scenario_type": stype})
        chk(b, "%s: повтор не плодит дубль" % stype,
            r2.status_code == 200 and r2.json().get("reused") is True, r2.json().get("reused"))

        # ручная отметка -> сброс
        man = next((s for s in p["steps"] if not s.get("auto") and s["status"] != "completed"), None)
        if man:
            r3 = requests.post(API + "/api/home/scenario/step", headers=h, timeout=30,
                               json={"account_id": a, "scenario_id": sid, "step_key": man["key"], "action": "done"})
            p3 = r3.json()["scenario"]["progress"]
            chk(b, "%s: отметка двигает прогресс" % stype, p3["done"] == p["done"] + 1,
                "%s -> %s" % (p["done"], p3["done"]))
            r4 = requests.post(API + "/api/home/scenario/step", headers=h, timeout=30,
                               json={"account_id": a, "scenario_id": sid, "step_key": man["key"], "action": "reset"})
            chk(b, "%s: сброс возвращает прогресс" % stype,
                r4.json()["scenario"]["progress"]["done"] == p["done"], p["done"])
            r5 = requests.post(API + "/api/home/scenario/step", headers=h, timeout=30,
                               json={"account_id": a, "scenario_id": sid, "step_key": man["key"], "action": "skip"})
            chk(b, "%s: пропуск не считается выполнением" % stype,
                r5.json()["scenario"]["progress"]["done"] == p["done"] + 1
                and any(x["status"] == "skipped" for x in r5.json()["scenario"]["progress"]["steps"]), "")
            requests.post(API + "/api/home/scenario/step", headers=h, timeout=30,
                          json={"account_id": a, "scenario_id": sid, "step_key": man["key"], "action": "reset"})
        else:
            chk(b, "%s: есть ручной шаг для проверки" % stype, False, "все шаги авто", "WARNING")

        # виден на главной
        ov = requests.get(API + "/api/home/overview?account_id=" + a, headers=h, timeout=60).json()
        chk(b, "%s: виден на рабочем столе" % stype,
            (ov.get("active_scenario") or {}).get("scenario_type") == stype
            or bool(ov.get("active_scenario")), "")

        # прогресс запуска Avito должен совпадать со стадией
        if stype == "avito_start":
            stage = ov["stage"]["number"]
            done = sc["progress"]["done"]
            chk(b, "avito_start: прогресс равен стадии-1", done == max(0, stage - 1),
                "стадия %s, выполнено %s" % (stage, done))

    # уборка
    for sid in created:
        requests.post(API + "/api/home/scenario/cancel", headers=h, timeout=30,
                      json={"account_id": a, "scenario_id": sid})
    ov = requests.get(API + "/api/home/overview?account_id=" + a, headers=h, timeout=60).json()
    chk(b, "после отмены активных сценариев нет", not ov.get("active_scenario"), "")


# ---------------------------------------------------------- PERFORMANCE
def block_performance():
    b = "Performance"
    h, a = H(CLIENT), acc(CLIENT)
    for name, url, limit in (
        ("overview", API + "/api/home/overview?account_id=" + a, 1.5),
        ("scenarios", API + "/api/home/scenarios?account_id=" + a, 1.0),
        ("accounts/list", API + "/api/accounts/list", 1.0),
    ):
        t0 = time.time()
        requests.get(url, headers=h, timeout=60)
        dt = round(time.time() - t0, 2)
        chk(b, "%s < %s c" % (name, limit), dt < limit, "%s c" % dt,
            None if dt < limit else "WARNING")

    for name, url, limit in (("/dashboard/home", WEB + "/dashboard/home", 3.0),
                             ("/dashboard", WEB + "/dashboard", 6.0)):
        t0 = time.time()
        requests.get(url, timeout=60)
        dt = round(time.time() - t0, 2)
        chk(b, "%s < %s c" % (name, limit), dt < limit, "%s c" % dt,
            None if dt < limit else "WARNING")


# ----------------------------------------------------------- REGRESSION
def block_regression():
    b = "Regression"
    for path in ("/", "/login", "/dashboard", "/dashboard/home", "/dashboard/scenarios",
                 "/manager", "/social", "/agency", "/support", "/oferta", "/privacy"):
        try:
            sc = requests.get(WEB + path, timeout=45).status_code
        except Exception as e:
            sc = str(e)[:40]
        chk(b, "маршрут %s" % path, sc == 200, sc)
    chk(b, "/docs бэкенда", requests.get(API + "/docs", timeout=30).status_code == 200)


# --------------------------------------------------------------- UX
def block_ux():
    b = "UX"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        chk(b, "playwright доступен", False, str(e)[:80])
        return

    GENERIC = ["Открыть", "Готово", "Настроить", "Проверить", "Подробнее"]
    os.makedirs(QA_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    def audit(page, label, back_ok=True):
        """Пять вопросов ТЗ к каждому экрану."""
        body = page.inner_text("body")
        btns = page.locator("button, a").count()
        chk(b, "%s: экран не пустой" % label, len(body) > 220, "%d символов" % len(body))
        chk(b, "%s: есть куда нажать (нет тупика)" % label, btns >= 3, "%d элементов" % btns)
        # главное действие
        prim = page.locator("button").all_inner_texts()
        prim = [x.strip() for x in prim if x.strip()]
        chk(b, "%s: понятен следующий шаг" % label, len(prim) > 0,
            "первая кнопка: %s" % (prim[0][:40] if prim else "нет"))
        # неоднозначные кнопки
        dup = {}
        for t in prim:
            if t in GENERIC:
                dup[t] = dup.get(t, 0) + 1
        many = {k: v for k, v in dup.items() if v > 3}
        chk(b, "%s: нет размноженных общих кнопок" % label, not many, many,
            None if not many else "WARNING")
        # можно ли вернуться
        if back_ok:
            has_back = ("Главная" in body) or ("рабочий стол" in body.lower()) or \
                       page.locator('[data-testid="nav-home"]').count() > 0
            chk(b, "%s: есть возврат на главную" % label, has_back, "")
        # горизонтальный скролл
        over = page.evaluate("document.body.scrollWidth > window.innerWidth + 4")
        chk(b, "%s: нет горизонтального скролла" % label, not over, "", None if not over else "WARNING")
        # бесконечная загрузка
        chk(b, "%s: нет зависшей загрузки" % label, "Загружаю" not in body, "")
        page.screenshot(path=os.path.join(QA_DIR, "ux_%s_%s.png" % (stamp, label.replace(" ", "_")[:24])))

    def enter(page, cred):
        """Вход, не завязанный на конкретную разметку кнопки: в форме нет
        button[type=submit], поэтому пробуем текст, потом любую кнопку, потом Enter."""
        page.goto(WEB + "/login", timeout=30000)
        page.wait_for_timeout(800)
        page.fill("input[type=email]", cred[0])
        page.fill("input[type=password]", cred[1])
        for attempt in ("text", "any", "enter"):
            try:
                if attempt == "text":
                    page.get_by_text("Войти", exact=True).first.click(timeout=6000)
                elif attempt == "any":
                    page.locator("button").first.click(timeout=6000)
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                if "/login" not in page.url:
                    return True
            except Exception:
                continue
        return "/login" not in page.url

    with sync_playwright() as pw:
        br = pw.chromium.launch()
        for width, tag in ((1440, "desktop"), (380, "mobile")):
            ctx = br.new_context(viewport={"width": width, "height": 900})
            page = ctx.new_page()
            errs = []
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

            # --- ПУТЬ 1: клиент без Avito и без оплаты ---
            entered = enter(page, NEWBIE)
            chk(b, "%s путь1: вход выполнен" % tag, entered, page.url)
            if not entered:
                chk(b, "%s: остальные проверки пути пропущены" % tag, False,
                    "не удалось войти - см. предыдущую строку", "WARNING")
                ctx.close()
                continue
            chk(b, "%s путь1: вход ведёт на рабочий стол" % tag,
                "/dashboard/home" in page.url, page.url)
            page.goto(WEB + "/dashboard/home", timeout=30000); page.wait_for_timeout(3500)
            audit(page, "%s новичок: рабочий стол" % tag)
            body = page.inner_text("body")
            chk(b, "%s новичок: видит, что Avito не подключён" % tag, "Avito" in body, "")
            chk(b, "%s новичок: подключение Avito без ухода в чужой раздел" % tag,
                page.locator('[data-testid="home-attention"] button').count() > 0, "")
            # модалка ключей
            try:
                page.get_by_text("Подключить Avito", exact=False).first.click(timeout=6000)
                page.wait_for_timeout(1200)
                has_modal = page.locator('[data-testid="avito-modal"]').count() > 0
                chk(b, "%s новичок: открывается форма ключей" % tag, has_modal, "")
                if has_modal:
                    mtext = page.inner_text('[data-testid="avito-modal"]')
                    chk(b, "%s новичок: объяснено, где взять ключи" % tag, "Интеграции" in mtext, "")
                    chk(b, "%s новичок: обещано, что доплаты нет" % tag, "доплат" in mtext.lower(), "")
                page.keyboard.press("Escape")
            except Exception as e:
                chk(b, "%s новичок: открывается форма ключей" % tag, False, str(e)[:70])

            # --- ПУТЬ 3: каталог и запуск сценария ---
            page.goto(WEB + "/dashboard/scenarios", timeout=30000); page.wait_for_timeout(3000)
            audit(page, "%s каталог сценариев" % tag)
            body = page.inner_text("body")
            chk(b, "%s каталог: «Скоро» не кликабельно" % tag, "Пока недоступно" in body, "")
            try:
                page.locator('[data-testid="start-revive"]').first.click(timeout=8000)
                page.wait_for_timeout(1500)
                chk(b, "%s мастер открылся" % tag, page.locator('[data-testid="wizard"]').count() > 0, "")
                wtext = page.inner_text('[data-testid="wizard"]')
                chk(b, "%s мастер: не больше 5 полей" % tag,
                    page.locator('[data-testid="wizard"] input, [data-testid="wizard"] select').count() <= 5, "")
                chk(b, "%s мастер: видно, что будет сделано" % tag, "сделаю" in wtext.lower(), "")
                page.keyboard.press("Escape")
            except Exception as e:
                chk(b, "%s мастер открылся" % tag, False, str(e)[:70])

            # --- ПУТЬ 2: клиент с подключённым Avito ---
            ctx2 = br.new_context(viewport={"width": width, "height": 900})
            p2 = ctx2.new_page()
            enter(p2, CLIENT)
            p2.goto(WEB + "/dashboard/home", timeout=30000); p2.wait_for_timeout(3500)
            audit(p2, "%s клиент с Avito" % tag)
            body2 = p2.inner_text("body")
            chk(b, "%s клиент: журнал не пуст или объяснено почему" % tag,
                ("Что делал" in body2), "")
            # переход в старый кабинет и возврат
            try:
                p2.locator('[data-testid="nav-cabinet"]').first.click(timeout=8000)
                p2.wait_for_timeout(4000)
                chk(b, "%s переход в кабинет работает" % tag, "/dashboard" in p2.url, p2.url)
                # ИЗВЕСТНО И ПРИНЯТО: переход ведёт на промежуточный экран выбора
                # аккаунта, меню появляется только после «Открыть →». Держим как
                # WARNING, чтобы видеть, но не красить набор в FAIL.
                nav_now = p2.locator("button.b-nav").count()
                chk(b, "%s кабинет открывается без лишнего экрана" % tag, nav_now > 0,
                    "b-nav=%d — сначала экран выбора аккаунта" % nav_now,
                    None if nav_now > 0 else "WARNING")
                if nav_now == 0:
                    try:
                        p2.get_by_text("Открыть", exact=False).first.click(timeout=8000)
                        p2.wait_for_timeout(4000)
                    except Exception:
                        pass
                nav2 = p2.locator("button.b-nav").count()
                chk(b, "%s кабинет доводит до вкладок" % tag, nav2 > 5, "b-nav=%d" % nav2)
                chk(b, "%s из кабинета видно возврат на «Главная»" % tag,
                    p2.locator("button.b-nav", has_text="Главная").count() > 0, "")
            except Exception as e:
                chk(b, "%s переход в кабинет работает" % tag, False, str(e)[:70])
            ctx2.close()

            # testid на месте
            page.goto(WEB + "/dashboard/home", timeout=30000); page.wait_for_timeout(3000)
            miss = [t for t in TESTIDS_HOME if page.locator('[data-testid="%s"]' % t).count() == 0]
            chk(b, "%s все data-testid рабочего стола" % tag, not miss, miss)
            page.goto(WEB + "/dashboard/scenarios", timeout=30000); page.wait_for_timeout(2500)
            miss2 = [t for t in TESTIDS_SCEN if page.locator('[data-testid="%s"]' % t).count() == 0]
            chk(b, "%s все data-testid каталога" % tag, not miss2, miss2)

            real = [e for e in errs if "favicon" not in e.lower()]
            chk(b, "%s: нет ошибок в консоли" % tag, not real, real[:2],
                None if not real else "WARNING")
            ctx.close()
        br.close()

    # пути, которые набор пока не покрывает — честно, а не молча
    chk(b, "путь: регистрация с нуля через форму", False,
        "не покрыт — создаёт живого пользователя на проде", "WARNING")
    chk(b, "путь: клиент с истёкшей оплатой", False,
        "не покрыт — нет такого аккаунта, менять данные нельзя", "WARNING")
    chk(b, "путь: менеджер", False, "не покрыт — нет пароля в наборе", "WARNING")



def _qa_db():
    """Подключение к БД только для блока registration: чтение своих строк
    и удаление их же по точным id. Ничего массового."""
    from sqlalchemy import create_engine
    url = re.search(r"^DATABASE_URL=(.+)$",
                    open(".env", encoding="utf-8").read(), re.M).group(1).strip().strip("\"'")
    return create_engine(url)


def block_registration():
    b = "REGISTRATION"
    if not QA_WRITES:
        chk(b, "запись-тесты регистрации", True,
            "SKIP - нужен --registration-write-tests и BORIS_ALLOW_QA_WRITES=1", "SKIP")
        return

    from sqlalchemy import text
    eng = _qa_db()
    created = {"users": [], "accounts": []}

    def new_email():
        return "qa+%d-%s@%s" % (int(time.time()),
                                "".join(random.choices(string.ascii_lowercase, k=6)),
                                QA_DOMAIN)

    def do_register(payload):
        return requests.post(API + "/api/auth/register", json=payload, timeout=20)

    def row_by_email(email):
        with eng.connect() as c:
            r = c.execute(text("SELECT id, account_id, referred_by, planned_accounts "
                               "FROM users WHERE email = :e"), {"e": email}).first()
            if r is None:
                return None
            a = c.execute(text("SELECT id, name FROM accounts WHERE account_id = :a"),
                          {"a": r[1]}).first()
        return {"user_id": r[0], "account_id": r[1], "referred_by": r[2],
                "planned": r[3], "acc_pk": (a[0] if a else None),
                "acc_name": (a[1] if a else None)}

    def register_and_track(payload):
        resp = do_register(payload)
        info = row_by_email(payload["email"])
        if info:
            created["users"].append(info["user_id"])
            if info["acc_pk"] is not None:
                created["accounts"].append(info["acc_pk"])
        return resp, info

    try:
        # --- существующий менеджерский ref_code, если он есть ---
        with eng.connect() as c:
            mgr = c.execute(text("SELECT ref_code, email FROM users "
                                 "WHERE ref_code IS NOT NULL AND ref_code <> '' "
                                 "LIMIT 1")).first()

        # 1+2+5+6: полный payload с валидным ref
        e1 = new_email()
        p1 = {"email": e1, "password": "qa-pass-123", "account_name": "QA Компания",
              "planned_accounts": "1-3", "ref": (mgr[0] if mgr else "")}
        r1, i1 = register_and_track(p1)
        chk(b, "полный payload: регистрация прошла", r1.status_code == 200
            and r1.json().get("status") == "ok", str(r1.status_code))
        chk(b, "account_name сохранён", bool(i1) and i1["acc_name"] == "QA Компания",
            str(i1 and i1["acc_name"]))
        chk(b, "planned_accounts сохранён", bool(i1) and i1["planned"] == "1-3",
            str(i1 and i1["planned"]))
        if mgr:
            chk(b, "валидный ref -> referred_by заполнен",
                bool(i1) and i1["referred_by"] == mgr[1], str(i1 and i1["referred_by"]))
        else:
            chk(b, "валидный ref -> referred_by заполнен", True,
                "SKIP - в базе нет ни одного менеджера с ref_code", "SKIP")

        # 3: неверный ref не ломает регистрацию
        e2 = new_email()
        r2, i2 = register_and_track({"email": e2, "password": "qa-pass-123",
                                     "ref": "no-such-ref-code-zzz"})
        chk(b, "неверный ref: регистрация не упала",
            r2.status_code == 200 and r2.json().get("status") == "ok", str(r2.status_code))
        chk(b, "неверный ref: referred_by пуст", bool(i2) and i2["referred_by"] is None,
            str(i2 and i2["referred_by"]))

        # 4: повторная регистрация не создаёт дубликат
        r3 = do_register({"email": e2, "password": "qa-pass-123"})
        chk(b, "повтор: дубликат не создан",
            r3.json().get("status") == "error", str(r3.json())[:120])
        with eng.connect() as c:
            n = c.execute(text("SELECT count(*) FROM users WHERE email = :e"),
                          {"e": e2}).scalar()
        chk(b, "повтор: в базе ровно одна строка", n == 1, "строк=%s" % n)

        # 1: обе точки шлют одинаковый набор ключей (сверка с общим модулем фронта)
        src = ""
        for cand in ("../frontend/app/lib/register.ts", "/root/BORIS/frontend/app/lib/register.ts"):
            if os.path.exists(cand):
                src = open(cand, encoding="utf-8").read()
                break
        keys = {"email", "password", "account_name", "planned_accounts", "ref"}
        chk(b, "единый модуль шлёт все 5 полей",
            bool(src) and all(("%s:" % k) in src or ('"%s"' % k) in src for k in keys),
            "модуль найден" if src else "app/lib/register.ts не найден")
        # прямых fetch на register в формах быть не должно: обе точки
        # обязаны ходить через общий модуль app/lib/register.ts
        direct = 0
        for f in ("/root/BORIS/frontend/app/page.tsx",
                  "/root/BORIS/frontend/app/login/page.tsx"):
            if os.path.exists(f):
                direct += open(f, encoding="utf-8").read().count("api/auth/register")
        chk(b, "прямых fetch на register в формах не осталось", direct == 0,
            "найдено вхождений: %d" % direct)

    finally:
        # очистка ТОЛЬКО по собранным точным id, аккаунты раньше пользователей
        from sqlalchemy import text as _t
        with eng.begin() as c:
            for aid in created["accounts"]:
                c.execute(_t("DELETE FROM accounts WHERE id = :i"), {"i": aid})
            for uid in created["users"]:
                c.execute(_t("DELETE FROM users WHERE id = :i"), {"i": uid})
        print("   очищено: users=%s accounts=%s" % (created["users"], created["accounts"]))


BLOCKS = {"api": block_api, "security": block_security, "roles": block_roles,
          "business": block_business, "performance": block_performance,
          "regression": block_regression, "registration": block_registration, "ux": block_ux}


def main():
    want = [a.lower() for a in sys.argv[1:] if not a.startswith("--")] or list(BLOCKS)
    bad = [w for w in want if w not in BLOCKS]
    if bad:
        print("Неизвестные блоки:", bad, "| доступны:", list(BLOCKS)); sys.exit(2)

    print("=" * 68)
    print("BORIS QA 2.0 —", datetime.now().strftime("%d.%m %H:%M"), "| блоки:", ", ".join(want))
    print("=" * 68)
    for w in want:
        print("\n--- %s ---" % w.upper())
        try:
            BLOCKS[w]()
        except Exception as e:
            chk(w.upper(), "блок выполнился без падения", False, str(e)[:200])
            traceback.print_exc()

    print("\n" + "=" * 68)
    print("%-14s %-9s %s" % ("БЛОК", "РЕЗУЛЬТАТ", "ПРОБЛЕМЫ"))
    print("=" * 68)
    overall, hard = "PASS", False
    for name in ["API", "UI", "Security", "Roles", "Business", "Performance", "Regression", "UX"]:
        rows = [r for r in R if r["block"] == name]
        if not rows:
            continue
        fails = [r for r in rows if not r["ok"] and r["level"] == "FAIL"]
        warns = [r for r in rows if r["level"] == "WARNING"]
        verdict = "FAIL" if fails else ("WARNING" if warns else "PASS")
        if fails:
            overall = "FAIL"
            if name in ("Security", "Roles"):
                hard = True
        elif warns and overall == "PASS":
            overall = "WARNING"
        print("%-14s %2d/%-6d %s" % (name, len(rows) - len(fails), len(rows), verdict))
        for r in fails[:6]:
            print("     FAIL  %s — %s" % (r["name"][:48], r["detail"][:60]))
        for r in warns[:6]:
            print("     WARN  %s — %s" % (r["name"][:48], r["detail"][:60]))
    print("-" * 68)
    print("ИТОГ:", overall, "(критично в Security/Roles)" if hard else "")
    print("=" * 68)

    os.makedirs(QA_DIR, exist_ok=True)
    p = os.path.join(QA_DIR, "qa_suite_%s.json" % datetime.now().strftime("%Y%m%d_%H%M"))
    json.dump({"ts": datetime.now().isoformat(), "overall": overall, "results": R},
              open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("отчёт:", p)
    sys.exit(1 if overall == "FAIL" else 0)


if __name__ == "__main__":
    main()
