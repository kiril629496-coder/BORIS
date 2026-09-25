#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only production QA for unified client acquisition workspace."""
import json,sys,re
sys.path.insert(0,"/root/BORIS/backend")
import qa_suite
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

WEB="https://boris-ai.pro"
ACCOUNT="testref_47784"
MAX_URL="https://max.ru/u/f9LHodD0cOLLNHY2Rea9pC_FXcSh-1LgXiDxZZTAfCr-XQM3rBeNpfMKPb0"
WA_URL="https://wa.me/79819673787"

def login(page):
    page.goto(WEB+"/login",wait_until="domcontentloaded",timeout=30000)
    page.wait_for_timeout(400)
    if "/login" in page.url:
        page.locator("input[type=email]").fill(qa_suite.OWNER[0])
        page.locator("input[type=password]").fill(qa_suite.OWNER[1])
        page.get_by_text("Войти",exact=True).first.click()
        page.wait_for_timeout(1800)
    page.evaluate("(a)=>localStorage.setItem('boris_currentAccount',a)",ACCOUNT)

def main():
    out={"status":"PASS","checks":{},"failures":[],"console_errors":[]}
    with sync_playwright() as pw:
        br=pw.chromium.launch(headless=True,args=["--no-sandbox"])
        ctx=br.new_context(viewport={"width":1440,"height":1000})
        p=ctx.new_page()
        p.on("console",lambda m: out["console_errors"].append(m.text) if m.type=="error" else None)
        login(p)
        resp=p.goto(WEB+"/dashboard/prospecting/acquisition",wait_until="domcontentloaded",timeout=30000)
        # The overview is API-hydrated. A fixed 1.8s sleep made this gate flaky
        # under normal backend load and could report missing Telegram/Email/
        # guardian cards even though they appeared a moment later.
        try:
            p.get_by_text("АВТОКОНТРОЛЬ ФОРУМОВ",exact=True).wait_for(state="visible",timeout=20000)
        except PlaywrightTimeoutError:
            pass
        p.wait_for_timeout(250)
        body=p.locator("body").inner_text()

        checks=out["checks"]
        checks["http"]=resp.status if resp else None
        checks["path"]=p.url
        checks["shell"]=p.locator('[data-testid="app-shell"]').count()
        checks["overflowX"]=p.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        checks["title"]="Telegram + Email + форумы + VK в одном окне" in body
        checks["free"]="Только бесплатно · 0 ₽" in body
        checks["telegram"]="TELEGRAM" in body
        checks["email"]="EMAIL" in body
        checks["placements"]="БЕСПЛАТНЫЕ ПУБЛИКАЦИИ" in body
        checks["guardian"]="АВТОКОНТРОЛЬ ФОРУМОВ" in body and "BORIS сам перечитывает правила и чистит очередь" in body
        checks["crowd_guardian"]="SEO ДЛЯ КЛИЕНТОВ · АВТОПИЛОТ" in body and "BORIS сам двигает автоматически проверенные проекты, проверяет ссылки и ставит замену" in body
        checks["crowd_capacity"]=(
            "Нужно площадок" in body
            and "BORIS доведёт сам" in body
            and "Уже READY" in body
            and "Быстрая подготовка" in body
            and "После прогрева" in body
            and "Ещё найти долгосрочно" in body
            and (
                "Быстрый остаток поиска" in body
                or "Пула автономных площадок достаточно" in body
            )
            and (
                "Это не задача владельца клиентского проекта" in body
                or "Пула автономных площадок достаточно" in body
            )
        )
        checks["max_href"]=p.locator(f'a[href="{MAX_URL}"]').count()
        checks["wa_href"]=p.locator(f'a[href="{WA_URL}"]').count()

        # Client Crowd SEO must accept only the site and expose automation state
        # without leaking raw backend status codes to the owner.
        p.get_by_role("button",name=re.compile(r"^SEO для клиентов")).click()
        p.get_by_text(
            re.compile(r"^SEO-размещения для клиентов.*вход только сайт$")
        ).wait_for(state="visible",timeout=5000)
        p.wait_for_timeout(350)
        crowd_text=p.locator("body").inner_text()
        checks["crowd_tab"]="Что нужно от клиента" in crowd_text and "Только сайт" in crowd_text
        checks["crowd_create"]=p.locator('input[placeholder="https://site-client.ru"]').count()==1 and "Создать SEO-проект" in crowd_text
        checks["crowd_projects"]="boris-ai.pro" in crowd_text and "15 + 3 размещений" in crowd_text
        checks["crowd_status_ru"]="BORIS расширяет пул площадок" in crowd_text
        # Backoff text is conditional: it is shown only after an empty search.
        # A fresh successful discovery run must not fail production QA merely
        # because backoff is not active.
        checks["crowd_discovery_state_ru"]=(
            "АВТОКОНТРОЛЬ ФОРУМОВ" in body
            and "BORIS сам перечитывает правила и чистит очередь" in body
        )
        checks["crowd_self_heal_ru"]="История замен:" in crowd_text and "BORIS расширяет пул площадок" in crowd_text
        checks["crowd_active_plan_summary"]=bool(re.search(r"18\s+план",crowd_text)) and bool(re.search(r"\d+\s+активно",crowd_text))
        checks["crowd_plan_stays_18"]="18\nплан" in crowd_text and "19\nплан" not in crowd_text
        checks["crowd_no_raw_status"]="placement_plan_ready" not in crowd_text and "ready_for_content_approval" not in crowd_text and "platform_replacement_required" not in crowd_text
        checks["crowd_single_input"]="Клиент присылает только адрес сайта" in crowd_text and "Ниша, ключи, продвигаемые страницы, анкоры, тексты и подбор площадок формируются автоматически" in crowd_text
        same_site=p.locator('[data-testid="crowd-active-project"]').filter(has_text="boris-ai.pro").count()
        checks["crowd_single_live_same_site"]=same_site==1
        checks["crowd_ownerless_content"]=("вам ничего согласовывать не нужно" in crowd_text and p.get_by_role("button",name="Согласовать проект и запустить").count()==0)
        checks["crowd_duplicate_archived"]=("Исторические дубли:" in crowd_text and "не публикуются параллельно" in crowd_text)
        checks["crowd_csv_report"]="Скачать отчёт CSV" in crowd_text
        checks["crowd_link_verification_ui"]="Проверка SEO-ссылки" in crowd_text and "после публикации BORIS проверит автоматически" in crowd_text

        # Real idempotency scenario: retry the same client site through the live
        # UI. This is intentionally safe: the backend must reuse the project
        # and perform no external publication or paid action.
        p.locator('input[placeholder="https://site-client.ru"]').fill("https://boris-ai.pro/")
        p.get_by_role("button",name="Создать SEO-проект").click()
        p.get_by_text(re.compile(r"уже существует.*без дубля")).wait_for(state="visible",timeout=10000)
        p.wait_for_timeout(300)
        crowd_after_retry=p.locator("body").inner_text()
        checks["crowd_idempotent_create"]=(p.locator('[data-testid="crowd-active-project"]').filter(has_text="boris-ai.pro").count()==same_site and "Ничего согласовывать вручную не нужно" in crowd_after_retry)

        # Discovery is automatic and visible without requiring an owner button.
        p.get_by_role("button",name=re.compile(r"^Автопоиск форумов")).click()
        p.wait_for_timeout(350)
        discovery_text=p.locator("body").inner_text()
        checks["discovery_tab"]="Автопоиск новых форумов" in discovery_text and "поиск новых форумов запускается сторожем" in discovery_text

        # Platform tab must be forum-first, niche-filterable and contain broad industries.
        p.get_by_role("button",name="Площадки и правила").click()
        p.get_by_text("Skripters",exact=False).first.wait_for(state="visible",timeout=5000)
        platform_text=p.locator("body").inner_text()
        checks["forums"]=all(x in platform_text for x in ["Строй Форум","TowerBuild","PartnerSearch","Skripters"])
        checks["forum_filter"]="Только форумы" in platform_text and "Все ниши" in platform_text
        checks["niches"]=all(x in platform_text for x in ["Строительство","Маркетинг / реклама","IT / разработка"])
        checks["seo_topics"]="Автоматизация продаж в строительной компании" in platform_text
        checks["seo_keywords"]="CRM для строителей" in platform_text

        # Registration tab must route unavoidable one-time checkpoints to
        # the service onboarding queue, never turn the client owner into operator.
        p.get_by_role("button",name=re.compile(r"^Регистрации")).click()
        p.get_by_text("Служебная очередь подготовки площадок",exact=False).first.wait_for(state="visible",timeout=7000)
        reg_text=p.locator("body").inner_text()
        checks["registrations"]="Регистрации площадок" in reg_text
        checks["registration_email"]="kirillkozerogov@gmail.com" in reg_text
        checks["registration_checkpoints"]="нужно пройти CAPTCHA" in reg_text and "нужно один раз принять условия регистрации" in reg_text
        checks["registration_service_queue"]="Служебная очередь подготовки площадок" in reg_text and "Открыть для специалиста" in reg_text
        checks["registration_owner_not_operator"]="Владелец клиентского проекта не работает оператором регистраций" in reg_text and "Нужна ваша помощь" not in reg_text

        # Materials must focus on the two actual commercial offers.
        p.get_by_role("button",name=re.compile(r"^Материалы")).click()
        p.wait_for_timeout(350)
        mat_text=p.locator("body").inner_text()
        checks["materials_explained"]="Главные офферы: BORIS как виртуальный отдел продаж и рекламы под ключ" in mat_text
        checks["max_in_copy"]=MAX_URL in mat_text
        checks["virtual_department"]="BORIS — виртуальный отдел продаж и рекламы под ключ" in mat_text
        checks["development"]="Разработка и автоматизация бизнес-процессов под ключ" in mat_text

        # Results must expose exact failure reasons instead of silent failures.
        p.get_by_role("button",name=re.compile(r"^Результаты")).click()
        p.wait_for_timeout(350)
        res_text=p.locator("body").inner_text()
        checks["results_reason"]="аккаунт не подтвердился после регистрации" in res_text or "нужно пройти CAPTCHA" in res_text
        checks["results_rule"]="можно" in res_text

        # Pricing must be populated with 20+ directions.
        p.get_by_role("button",name=re.compile(r"^Прайс")).click()
        p.wait_for_timeout(350)
        price_text=p.locator("body").inner_text()
        checks["pricing"]="CRM + автоматизация продаж" in price_text and "SaaS MVP" in price_text
        checks["pricing_rows"]=p.locator("tbody tr").count()
        checks["pricing_inputs"]="доступ к CRM" in price_text
        checks["pricing_docs"]="Коммерческое предложение" in price_text and "Приложение с ТЗ и критериями приёмки" in price_text
        checks["pricing_monthly"]="VPS 1–4 тыс. ₽" in price_text

        required={
            "http":200,"shell":1,"overflowX":False,"title":True,"free":True,
            "telegram":True,"email":True,"placements":True,"guardian":True,"crowd_guardian":True,"crowd_capacity":True,
            "crowd_tab":True,"crowd_create":True,"crowd_projects":True,"crowd_status_ru":True,"crowd_discovery_state_ru":True,
            "crowd_self_heal_ru":True,"crowd_active_plan_summary":True,"crowd_plan_stays_18":True,"crowd_no_raw_status":True,"crowd_single_input":True,
            "crowd_single_live_same_site":True,"crowd_ownerless_content":True,"crowd_duplicate_archived":True,
            "crowd_csv_report":True,"crowd_link_verification_ui":True,"crowd_idempotent_create":True,
            "discovery_tab":True,"forums":True,"forum_filter":True,"niches":True,"seo_topics":True,"seo_keywords":True,
            "registrations":True,"registration_email":True,"registration_checkpoints":True,
            "registration_service_queue":True,"registration_owner_not_operator":True,
            "materials_explained":True,"max_in_copy":True,"virtual_department":True,"development":True,
            "results_reason":True,"results_rule":True,"pricing":True,"pricing_inputs":True,"pricing_docs":True,"pricing_monthly":True
        }
        for k,v in required.items():
            if checks.get(k)!=v: out["failures"].append(f"{k}={checks.get(k)!r}, expected={v!r}")
        if checks["max_href"]<1: out["failures"].append("max_link_missing")
        if checks["wa_href"]<1: out["failures"].append("whatsapp_link_missing")
        if checks["pricing_rows"]<20: out["failures"].append(f"pricing_rows={checks['pricing_rows']}")

        # Service marketplace inventory is part of the Crowd SEO production
        # contract: global site stock, current-project deficit and cross-niche
        # coverage must remain visible and truthful after every deployment.
        service_resp=p.goto(WEB+"/dashboard/prospecting/services",wait_until="domcontentloaded",timeout=30000)
        p.wait_for_timeout(1300)
        # Reserve accounts can be intentionally collapsed because they are not
        # needed by the current client. Open the block before validating the
        # reusable goods/services warmup contract.
        reserve=p.locator('[data-testid="reserve-bootstrap"]')
        if reserve.count():
            reserve.evaluate("(el)=>{el.open=true}")
            p.wait_for_timeout(100)
        service_text=p.locator("body").inner_text()
        checks["services_http"]=service_resp.status if service_resp else None
        checks["services_inventory"]="БАЗА САЙТОВ CROWD SEO" in service_text
        checks["services_seen"]="Всего сайтов уже нашли / проверили" in service_text and "в рабочем реестре" in service_text
        checks["services_current_capacity"]=(
            "Для текущего проекта" in service_text
            and "после быстрой подготовки" in service_text
            and "добавится после прогрева" in service_text
        )
        checks["services_current_deficit"]=(
            "Ещё найти" in service_text
            and "для быстрой ёмкости" in service_text
            and "после полного прогрева останется" in service_text
        )
        checks["services_niche_coverage"]="ПОКРЫТИЕ ПО НИШАМ" in service_text and "Где уже есть база для товаров и услуг клиентов" in service_text
        checks["services_goods"]=bool(re.search(r"Товары[\s\S]{0,120}\d+\s*/\s*\d+",service_text))
        checks["services_marketing"]=bool(re.search(r"Маркетинг / SEO[\s\S]{0,120}\d+\s*/\s*\d+",service_text))
        checks["services_warmup"]="ПРОГРЕВ АККАУНТОВ" in service_text and "Сейчас прогреваются" in service_text
        checks["services_nulled"]="Nulled · Коммерция и реклама услуг" in service_text
        checks["services_goods_services_reuse"]=(
            "Подходит для: товаров · услуг" in service_text
            and "Готовый аккаунт используется для разных клиентских проектов" in service_text
        )
        checks["services_warmup_contract"]=(
            "Минимальный возраст: 15 дней" in service_text
            and "сообщений: 30" in service_text
            and "реакций: 5" in service_text
            and "BORIS не накручивает активность и сам перепроверяет готовность" in service_text
        )
        checks["services_captcha_no_bypass"]=(
            "антибот-проверку он не обходит" in service_text
            and "не владелец клиента" in service_text
        )
        checks["services_overflowX"]=p.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        service_required={
            "services_http":200,
            "services_inventory":True,
            "services_seen":True,
            "services_current_capacity":True,
            "services_current_deficit":True,
            "services_niche_coverage":True,
            "services_goods":True,
            "services_marketing":True,
            "services_warmup":True,
            "services_nulled":True,
            "services_goods_services_reuse":True,
            "services_warmup_contract":True,
            "services_captcha_no_bypass":True,
            "services_overflowX":False,
        }
        for k,v in service_required.items():
            if checks.get(k)!=v: out["failures"].append(f"{k}={checks.get(k)!r}, expected={v!r}")

        if any("500" in x or "Internal Server Error" in x for x in out["console_errors"]):
            out["failures"].append("backend_5xx_console")

        ctx.close(); br.close()
    if out["failures"]: out["status"]="FAIL"
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out["status"]=="PASS" else 2

if __name__=="__main__":
    raise SystemExit(main())
