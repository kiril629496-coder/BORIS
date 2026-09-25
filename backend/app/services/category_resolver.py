"""
Универсальный резолвер категорий и обязательных полей Avito.
Источник истины: официальная документация /autoload/documentation/templates/{id}
Кэш: Storage(account_id='_global', key='category_knowledge_base')
"""
from app.services.realtime_ai_guard import realtime_guarded_post as _rt_guarded_post
import asyncio
import html
import json
import re
from app.services.exception_observability import observe_suppressed


def _category_paid_json(account_id: str, operation: str, prompt: str, max_tokens: int, timeout: int):
    """Exactly-once guarded category fallback used only when official Avito docs are insufficient.

    This is deliberately account-scoped: a Feed Factory fallback cannot spend from an
    unowned/global bucket. Same semantic intent is serialized across processes and cached
    before downstream work so ambiguous retries do not repurchase the same answer.
    """
    import datetime as _dt
    import hashlib as _hashlib
    import json as _json
    import os as _os
    import tempfile as _tempfile
    import requests as _requests
    from sqlalchemy import text as _sql_text, create_engine as _create_engine
    from sqlalchemy.pool import NullPool as _NullPool
    from app.db.session import DATABASE_URL as _DATABASE_URL
    from app.services import ai_budget as _ai_budget
    from app.services.ai_guard_audit import log_guard_event as _audit_ai_guard
    from app.usage import log_usage as _log_usage

    aid=str(account_id or "").strip()
    if not aid:
        raise RuntimeError("CATEGORY_PAID_ACCOUNT_REQUIRED")
    api_key=_os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("CATEGORY_OPENAI_KEY_MISSING")
    basis=_json.dumps({"policy":"category_paid_exactly_once_v1","account_id":aid,"operation":operation,"model":"gpt-5.4","prompt":prompt,"max_tokens":int(max_tokens)},ensure_ascii=False,sort_keys=True,separators=(",",":"))
    digest=_hashlib.sha256(basis.encode("utf-8")).hexdigest()
    intent_key="category:"+digest
    run_id="category:%s:%s"%(aid,digest)
    root=_os.environ.get("BORIS_CATEGORY_PAID_CACHE_DIR","/root/BORIS/backend/.ai_paid_cache/category")
    path=_os.path.join(root,digest+".json")
    uncertain_path=path+".uncertain"
    def _audit(code,status="blocked",details=None):
        _audit_ai_guard(account_id=aid,source="category_resolver",operation=operation,
                        event_code=code,status=status,intent_key=intent_key,
                        details=details if isinstance(details,dict) else None)
    def read_uncertain():
        try:
            with open(uncertain_path,"r",encoding="utf-8") as fh: obj=_json.load(fh)
            return obj if isinstance(obj,dict) and obj.get("intent_hash")==digest else None
        except Exception:
            return None
    def write_uncertain(error_code,provider_request_id=None):
        _os.makedirs(root,exist_ok=True)
        fd,tmp=_tempfile.mkstemp(prefix=".category-uncertain-",suffix=".json",dir=root)
        try:
            with _os.fdopen(fd,"w",encoding="utf-8") as fh:
                _json.dump({"schema":1,"intent_hash":digest,"status":"provider_outcome_uncertain",
                            "error_code":str(error_code or "category_outcome_unknown"),
                            "provider_request_id":provider_request_id or None},fh,ensure_ascii=False,sort_keys=True)
            _os.replace(tmp,uncertain_path)
        except Exception:
            try:_os.unlink(tmp)
            except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=67)
            raise
        _audit("CATEGORY_OUTCOME_UNCERTAIN","quarantined",{"reason":str(error_code or "category_outcome_unknown")})
    def read_cache():
        try:
            with open(path,"r",encoding="utf-8") as fh: obj=_json.load(fh)
            if obj.get("intent_hash")==digest and isinstance(obj.get("result"),(dict,list)):
                return obj.get("result")
        except Exception:
            return None
        return None
    cached=read_cache()
    if cached is not None: return cached
    uncertain=read_uncertain()
    if uncertain is not None:
        _audit("CATEGORY_OUTCOME_UNCERTAIN","quarantined",{"reason":str(uncertain.get("error_code") or "unknown")})
        raise RuntimeError("CATEGORY_OUTCOME_UNCERTAIN:"+str(uncertain.get("error_code") or "unknown"))
    raw=int.from_bytes(_hashlib.blake2b((aid+"|"+intent_key).encode("utf-8"),digest_size=8).digest(),"big",signed=False)
    lock_key=raw if raw<(1<<63) else raw-(1<<64)
    _lock_engine=_create_engine(_DATABASE_URL,poolclass=_NullPool,pool_pre_ping=True,
                               connect_args={"application_name":"boris-category-paid-lock"})
    db=_lock_engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        db.execute(_sql_text("select pg_advisory_lock(:k)"),{"k":lock_key})
        cached=read_cache()
        if cached is not None: return cached
        uncertain=read_uncertain()
        if uncertain is not None:
            _audit("CATEGORY_OUTCOME_UNCERTAIN","quarantined",{"reason":str(uncertain.get("error_code") or "unknown")})
            raise RuntimeError("CATEGORY_OUTCOME_UNCERTAIN:"+str(uncertain.get("error_code") or "unknown"))
        now=_dt.datetime.now(_dt.timezone.utc); day=now.replace(hour=0,minute=0,second=0,microsecond=0); burst=now-_dt.timedelta(minutes=10)
        row=db.execute(_sql_text("select count(*) from api_usage where account_id=:a and operation like 'category_%' and created_at>=:c"),{"a":aid,"c":burst}).scalar() or 0
        day_row=db.execute(_sql_text("select count(*) from api_usage where account_id=:a and operation like 'category_%' and created_at>=:c"),{"a":aid,"c":day}).scalar() or 0
        try: burst_cap=max(1,min(50,int(_os.environ.get("BORIS_CATEGORY_AI_BURST_CAP","10"))))
        except Exception: burst_cap=10
        try: daily_cap=max(burst_cap,min(200,int(_os.environ.get("BORIS_CATEGORY_AI_DAILY_CAP","40"))))
        except Exception: daily_cap=40
        if int(day_row)>=daily_cap:
            _audit("CATEGORY_DAILY_CAP","blocked",{"daily_used":int(day_row),"daily_cap":daily_cap})
            raise RuntimeError("CATEGORY_AI_DAILY_CAP_REACHED")
        if int(row)>=burst_cap:
            _audit("CATEGORY_BURST_CAP","blocked",{"used":int(row),"cap":burst_cap,"window_min":10})
            raise RuntimeError("CATEGORY_AI_BURST_CAP_REACHED")
        token=_ai_budget.reserve(account_id=aid,module="category_resolver",operation=operation,run_id=run_id,est_kopeks=100,calls_planned=1)
        reserve_status=str((token or {}).get("status") or "")
        if reserve_status not in {"reserved","observe","exists"}:
            _audit("AI_BUDGET_RESERVE_FAILED","blocked",{"status":reserve_status})
            raise RuntimeError("CATEGORY_BUDGET_RESERVE_FAILED")
        try:
            allow=_ai_budget.allow_next(run_id)
            if not bool((allow or {}).get("allowed") if isinstance(allow,dict) else allow):
                _audit("AI_BUDGET_NOT_ALLOWED","blocked",{"reason":str((allow or {}).get("reason") or "") if isinstance(allow,dict) else ""})
                raise RuntimeError("CATEGORY_BUDGET_NOT_ALLOWED")
            # Durable pre-provider barrier: a worker crash after the request leaves
            # this intent quarantined instead of allowing a blind second purchase.
            _os.makedirs(root,exist_ok=True)
            fd_pending,tmp_pending=_tempfile.mkstemp(prefix=".category-inflight-",suffix=".json",dir=root)
            try:
                with _os.fdopen(fd_pending,"w",encoding="utf-8") as fh:
                    _json.dump({"schema":1,"intent_hash":digest,"status":"provider_in_progress",
                                "error_code":"provider_outcome_unknown"},fh,ensure_ascii=False,sort_keys=True)
                _os.replace(tmp_pending,uncertain_path)
            except Exception:
                try:_os.unlink(tmp_pending)
                except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=131)
                raise
            from app.services.ai_privacy import safe_external_ai_payload, restore_external_ai_output
            external_prompt, privacy_vault = safe_external_ai_payload(prompt)
            try:
                response=_requests.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization":"Bearer "+api_key,"Content-Type":"application/json","Idempotency-Key":intent_key},
                    json={"model":"gpt-5.4","messages":[{"role":"user","content":external_prompt}],"max_completion_tokens":int(max_tokens)},
                    timeout=int(timeout),
                )
            except Exception as exc:
                write_uncertain("category_transport_outcome_unknown")
                raise RuntimeError("CATEGORY_OUTCOME_UNCERTAIN:category_transport_outcome_unknown") from exc
            if int(getattr(response,"status_code",0) or 0) != 200:
                try:_os.unlink(uncertain_path)
                except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=147)
                response.raise_for_status()
            exact=str(response.headers.get("x-request-id") or "").strip()
            try:
                data=response.json()
            except Exception as exc:
                write_uncertain("provider_200_unparseable",exact or None)
                raise RuntimeError("CATEGORY_OUTCOME_UNCERTAIN:provider_200_unparseable") from exc
            usage=data.get("usage") or {}
            raw_text=str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if raw_text:
                raw_text=str(restore_external_ai_output(raw_text, privacy_vault)).replace("```json","").replace("```","").strip()
            if not raw_text:
                write_uncertain("provider_200_empty_result",exact or None)
                raise RuntimeError("CATEGORY_OUTCOME_UNCERTAIN:provider_200_empty_result")
            try:
                result=_json.loads(raw_text)
            except Exception as exc:
                write_uncertain("provider_200_bad_result_json",exact or None)
                raise RuntimeError("CATEGORY_OUTCOME_UNCERTAIN:provider_200_bad_result_json") from exc
            exact=str(response.headers.get("x-request-id") or "").strip(); rid=exact or ("ambiguous-text:category:"+digest)
            _os.makedirs(root,exist_ok=True); fd,tmp=_tempfile.mkstemp(prefix=".category-paid-",suffix=".json",dir=root)
            try:
                with _os.fdopen(fd,"w",encoding="utf-8") as fh: _json.dump({"schema":1,"intent_hash":digest,"provider_request_id":exact or None,"ambiguous_success":not bool(exact),"result":result},fh,ensure_ascii=False,sort_keys=True)
                _os.replace(tmp,path)
                try:_os.unlink(uncertain_path)
                except Exception as _suppressed_exc:observe_suppressed(__name__, _suppressed_exc, line=173)
            except Exception:
                try: _os.unlink(tmp)
                except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=176)
                raise
            _log_usage(aid,"openai",data.get("model") or "gpt-5.4",operation,int(usage.get("prompt_tokens") or 0),int(usage.get("completion_tokens") or 0),request_id=rid,usage_details=usage)
            _ai_budget.commit(run_id,rid)
            return result
        finally:
            _ai_budget.release(run_id)
    finally:
        try: db.execute(_sql_text("select pg_advisory_unlock(:k)"),{"k":lock_key})
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=185)
        db.close()
        try: _lock_engine.dispose()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=188)

async def _block_media_only(route):
    if route.request.resource_type in ("image", "media"):
        await route.abort()
    else:
        await route.continue_()


async def _block_heavy(route):
    if route.request.resource_type in ("image", "stylesheet", "font", "media"):
        await route.abort()
    else:
        await route.continue_()




async def scrape_template_page(template_id: int) -> str:
    """Скрейпит страницу документации шаблона в английском режиме параметров, возвращает сырой текст body.
    Ретраит до 3 раз с НОВЫМ случайным портом резидентного пула на каждую попытку - часть портов
    в пуле всегда мертва, это норма для резидентных прокси, одна неудача не должна ронять всю операцию."""
    from proxy_pool import get_playwright_proxy, get_clean_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url = f"https://www.avito.ru/autoload/documentation/templates/{template_id}"
    stealth = Stealth()
    last_error = None

    async with async_playwright() as p:
        for attempt in range(3):
            proxy = await get_clean_playwright_proxy()
            browser = None
            try:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 1200}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", _block_heavy)

                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

                try:
                    eng_button = page.get_by_text("Английский", exact=True).first
                    await eng_button.click()
                    await page.wait_for_timeout(3000)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=239)

                text = await page.inner_text("body")
                await browser.close()
                # проверка: не бан-страница ли (IP протух между проверкой и запросом)
                if "Доступ ограничен" in text or "проблема с IP" in text:
                    import proxy_pool as _pp
                    _pp._clean_port_cache.clear()  # сбросить протухшие account-scoped маршруты
                    last_error = Exception(f"бан-страница на попытке {attempt+1}, порт сброшен")
                    continue
                return text
            except Exception as e:
                last_error = e
                if browser:
                    try:
                        await browser.close()
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=256)
                continue

    raise Exception(f"scrape_template_page: не удалось загрузить страницу шаблона {template_id} за 3 попытки: {last_error}")


def structure_fields_via_gpt(raw_text: str, category_name: str, account_id: str = None) -> list:
    """
    Отдаёт сырой текст документации в GPT-5.4, получает структурированный список полей.
    Возвращает list[dict]: {tag, name_ru, required, depends_on, allowed_values, example}
    """
    import os
    import requests
    from proxy_pool import get_intl_requests_proxies

    api_key = os.environ.get("OPENAI_API_KEY")
    proxies = get_intl_requests_proxies()

    prompt = (
        "Ниже — текст страницы официальной документации Avito Автозагрузка для категории "
        f"'{category_name}' (английские имена тегов, потому что это XML-совместимые технические имена). "
        "Разбери ВСЕ параметры (кроме общих Id/Title/Description/Price/Images/Address/ContactPhone — "
        "их не включай, они уже обрабатываются отдельно) в JSON-список.\n\n"
        "Для каждого параметра верни объект:\n"
        "{\n"
        '  "tag": "точное английское имя тега (например GoodsType)",\n'
        '  "name_ru": "русское название параметра",\n'
        '  "required": true/false,\n'
        '  "depends_on": "текст условия обязательности если оно условное, иначе null",\n'
        '  "allowed_values": ["список допустимых значений"] или null если значение свободное/открытый список,\n'
        '  "format_rule": "если у поля есть требование к ФОРМАТУ значения помимо списка допустимых '
        '(например \\"целое число строкой, без слов\\" для WorkExperience, или \\"строго Есть или Нет\\" '
        'для Guarantee) - опиши коротко своими словами; если формат свободный - null",\n'
        '  "example": "пример значения из документации"\n'
        "}\n\n"
        "Верни ТОЛЬКО чистый JSON-массив, без markdown, без пояснений.\n\n"
        "ТЕКСТ ДОКУМЕНТАЦИИ:\n" + raw_text[:15000]
    )

    parsed = _category_paid_json(str(account_id or ""), "category_doc_parse", prompt, 4000, 90)
    return parsed if isinstance(parsed, list) else []


def _load_knowledge_base(db) -> dict:
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == '_global', Storage.key == 'category_knowledge_base').first()
    if not row:
        return {}
    return json.loads(row.value)


def _save_knowledge_base(db, data: dict):
    from app.models.storage import Storage
    row = db.query(Storage).filter(Storage.account_id == '_global', Storage.key == 'category_knowledge_base').first()
    value = json.dumps(data, ensure_ascii=False)
    if row:
        row.value = value
    else:
        row = Storage(account_id='_global', key='category_knowledge_base', value=value)
        db.add(row)
    db.commit()


NAV_SCOPE = "nav[class*='navigation-menu-root']"
NAV_ROW_SEL = f"{NAV_SCOPE} [class*='navigation-menu-item-root']"


async def _get_tree_rows(page) -> list:
    """Плоский список строк дерева в DOM-порядке: {text, href, margin}. Разведка живого DOM
    (outerHTML после клика) показала, что Avito рендерит дерево ОДНИМ плоским списком
    div.navigation-menu-item-root с инлайновым margin-left (18px на уровень) вместо настоящей
    DOM-вложенности - значит родитель/потомок определяется отступом и порядком в списке, а НЕ
    containment и НЕ повторением текста (одинаковые подписи вроде "Продам"/"Сдам" законно
    повторяются под РАЗНЫМИ родителями на одном отступе)."""
    try:
        return await page.evaluate(
            """(scopeSel) => {
                const scope = document.querySelector(scopeSel);
                if (!scope) return [];
                const rows = Array.from(scope.querySelectorAll("[class*='navigation-menu-item-root']"));
                return rows.map(row => {
                    const text = (row.innerText || '').trim();
                    const a = row.querySelector('a');
                    const href = a ? a.getAttribute('href') : null;
                    const style = row.getAttribute('style') || '';
                    const m = style.match(/margin-left:\\s*(\\d+)px/);
                    return {text, href, margin: m ? parseInt(m[1], 10) : 0};
                });
            }""",
            NAV_SCOPE
        )
    except Exception:
        return []


def _pick_row_index(rows: list, text: str, margin: int, skip_count: int):
    """Находит (skip_count+1)-ю по счёту строку с данным (text, margin) в DOM-порядке - нужно,
    чтобы после re-scan снова найти ИМЕННО ТОТ узел, что мы уже начали обрабатывать, даже если
    в дереве есть другие строки с точно такой же подписью на том же отступе (у разных родителей)."""
    seen = 0
    for idx, r in enumerate(rows):
        if r.get("text") == text and r.get("margin") == margin:
            if seen == skip_count:
                return idx
            seen += 1
    return None


async def _crawl_tree_node(page, path_labels: list, text: str, margin: int, consumed: dict, leaves: list, depth: int = 0):
    """Раскрывает ОДИН конкретный узел дерева (заданный своими text/margin, см. _get_tree_rows) и
    рекурсивно обходит его ПРЯМЫХ детей - граница "прямой ребёнок" определяется отступом
    (margin > текущего, до первой строки с margin <= текущего), а НЕ повторением текста.
    `consumed` - общий счётчик {(text, margin): сколько раз уже встречалось} через ВЕСЬ обход -
    раньше вместо этого использовалось глобальное множество "видели ли этот текст", из-за чего
    ВТОРАЯ и все следующие ветки с одинаковой подписью ребёнка (например "Продам"/"Сдам" - общие
    для любого типа недвижимости, "Резюме"/"Вакансии" - общие для любой сферы работы) молча
    пропускались рекурсией: инцидент "Недвижимость" (5 листьев вместо сотен - раскрылась только
    одна ветка типа недвижимости из семи) и "Работа" (1 лист вместо десятков сфер)."""
    import re as _re

    if depth > 8:
        return

    key = (text, margin)
    skip = consumed.get(key, 0)
    consumed[key] = skip + 1

    rows = await _get_tree_rows(page)
    idx = _pick_row_index(rows, text, margin, skip)
    if idx is None:
        return
    row = rows[idx]

    m = _re.search(r"/templates/(\d+)", row.get("href") or "")
    if m:
        leaves.append({
            "top_level": path_labels[0],
            "path": " > ".join(path_labels),
            "leaf_name": text,
            "template_id": m.group(1),
        })
        return

    url_before = page.url
    try:
        await page.locator(NAV_ROW_SEL).nth(idx).locator("a").first.click(timeout=6000)
        await page.wait_for_timeout(1500)
    except Exception:
        return

    # SPA: у некоторых узлов template_id появляется в page.url ПОСЛЕ клика, а не в href
    url_after = page.url
    m2 = _re.search(r"/templates/(\d+)", url_after)
    if m2:
        leaves.append({
            "top_level": path_labels[0] if path_labels else text,
            "path": " > ".join(path_labels),
            "leaf_name": text,
            "template_id": m2.group(1),
        })
        print(f"[crawl] ЛИСТ: {' > '.join(path_labels)} -> {m2.group(1)}", flush=True)
        try:
            await page.go_back()
            await page.wait_for_timeout(1200)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=421)
        return

    rows2 = await _get_tree_rows(page)
    children = []
    for r in rows2[idx + 1:]:
        if r.get("margin", 0) > margin:
            children.append(r)
        else:
            break

    for child in children:
        child_text = child["text"]
        m3 = _re.search(r"/templates/(\d+)", child.get("href") or "")
        if m3:
            leaves.append({
                "top_level": path_labels[0],
                "path": " > ".join(path_labels + [child_text]),
                "leaf_name": child_text,
                "template_id": m3.group(1),
            })
            continue
        await _crawl_tree_node(page, path_labels + [child_text], child_text, child["margin"], consumed, leaves, depth + 1)

    if page.url != url_before:
        try:
            await page.go_back()
            await page.wait_for_timeout(1200)
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=450)


async def _crawl_category_tree_async() -> list:
    from proxy_pool import get_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url = "https://www.avito.ru/autoload/documentation/templates"
    stealth = Stealth()
    leaves = []
    last_error = None

    async with async_playwright() as p:
        browser = None
        page = None
        for attempt in range(3):
            proxy = get_playwright_proxy()
            try:
                browser = await p.chromium.launch(headless=False, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 1200}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", _block_media_only)
                await page.goto(url, timeout=60000, wait_until="networkidle")
                await page.wait_for_timeout(6000)
                try:
                    await page.wait_for_selector("a[href*='/templates/']", timeout=20000)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=482)
                await page.wait_for_timeout(15000)
                _live = await page.evaluate("() => document.body.innerHTML")
                open("/tmp/crawl_live.html", "w", encoding="utf-8").write(_live)
                _txt = await page.evaluate("() => document.body.innerText")
                open("/tmp/crawl_text.txt", "w", encoding="utf-8").write(_txt)
                _hrefs = await page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href')).filter(Boolean)")
                print(f"[crawl] живой DOM={len(_live)} | текст={len(_txt)} | ссылок={len(_hrefs)}", flush=True)
                print(f"[crawl] первые href: {_hrefs[:15]}", flush=True)
                print(f"[crawl] первые 400 симв текста: {_txt[:400]!r}", flush=True)
                await page.screenshot(path="/tmp/crawl_shot.png", full_page=True)
                break
            except Exception as e:
                last_error = e
                if browser:
                    try:
                        await browser.close()
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=500)
                browser = None
                page = None
                continue

        if not page:
            raise Exception(f"crawl_category_tree: не удалось открыть дерево категорий за 3 попытки: {last_error}")

        consumed = {}
        top_rows = await _get_tree_rows(page)
        for r in top_rows:
            if r.get("margin", 0) == 0 and r.get("text"):
                await _crawl_tree_node(page, [r["text"]], r["text"], 0, consumed, leaves, 0)
        await browser.close()

    return leaves


def _upsert_tree_leaves(leaves: list) -> int:
    """Дедуп по template_id (дерево иногда обходится с повтором одного и того же листа по разным
    веткам клика) + ON CONFLICT DO UPDATE вместо query-then-insert - обычный select+add падал
    UniqueViolation, если template_id уже был сохранён предыдущим (например частичным) обходом.
    Общий хелпер для полного и точечного (по конкретным top_level) обхода дерева."""
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTreeLeaf
    from sqlalchemy.dialects.postgresql import insert as _pg_insert

    by_template_id = {leaf["template_id"]: leaf for leaf in leaves}
    rows = list(by_template_id.values())

    db = SessionLocal()
    try:
        if rows:
            stmt = _pg_insert(CategoryTreeLeaf).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[CategoryTreeLeaf.template_id],
                set_={
                    "top_level": stmt.excluded.top_level,
                    "path": stmt.excluded.path,
                    "leaf_name": stmt.excluded.leaf_name,
                },
            )
            db.execute(stmt)
        db.commit()
    finally:
        db.close()
    return len(rows)


def crawl_category_tree() -> dict:
    """
    Разовый (админский) обход ВСЕГО дерева документации Автозагрузки Avito
    (.../autoload/documentation/templates) - строит полную карту {путь по дереву -> template_id}
    и сохраняет upsert-ом в CategoryTreeLeaf по template_id. Статический requests тут не годится -
    страница JS SPA и отдаёт один и тот же дефолтный фрагмент независимо от пути (подтверждено
    разведкой), поэтому используется headed-браузер с кликами по accordion.
    Результат живёт в БД и переиспользуется дальше через resolve_template_id_for_niche() -
    сам обход не делается на каждый запрос ниши.
    """
    leaves = asyncio.run(_crawl_category_tree_async())
    _upsert_tree_leaves(leaves)
    return {"status": "ok", "leaves_found": len(leaves)}


async def _crawl_category_tree_partial_async(top_levels: list) -> list:
    """Точечный допрогон: обходит дерево, но раскрывает и рекурсивно обходит ТОЛЬКО указанные
    top_level узлы (например 'Недвижимость', 'Работа', если первый обход прошёл их не полностью) -
    остальные top-level ветки просто не трогаем (см. _crawl_tree_node про margin-based обход)."""
    from proxy_pool import get_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth

    url = "https://www.avito.ru/autoload/documentation/templates"
    stealth = Stealth()
    leaves = []
    last_error = None

    async with async_playwright() as p:
        browser = None
        page = None
        for attempt in range(3):
            proxy = get_playwright_proxy()
            try:
                browser = await p.chromium.launch(headless=False, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 1200}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", _block_media_only)
                await page.goto(url, timeout=60000, wait_until="networkidle")
                await page.wait_for_timeout(6000)
                try:
                    await page.wait_for_selector(NAV_SCOPE, timeout=20000)
                except Exception as _suppressed_exc:
                    observe_suppressed(__name__, _suppressed_exc, line=596)
                await page.wait_for_timeout(5000)
                break
            except Exception as e:
                last_error = e
                if browser:
                    try:
                        await browser.close()
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=605)
                browser = None
                page = None
                continue

        if not page:
            raise Exception(f"crawl_category_tree_partial: не удалось открыть дерево категорий за 3 попытки: {last_error}")

        consumed = {}
        top_rows = await _get_tree_rows(page)
        for r in top_rows:
            if r.get("margin", 0) == 0 and r.get("text") in top_levels:
                await _crawl_tree_node(page, [r["text"]], r["text"], 0, consumed, leaves, 0)
        await browser.close()

    return leaves


def crawl_category_tree_partial(top_levels: list) -> dict:
    """Апсертит листья ТОЛЬКО из указанных top_level веток (см. _crawl_category_tree_partial_async) -
    остальные ветки дерева не трогает вообще, полный обход не требуется."""
    leaves = asyncio.run(_crawl_category_tree_partial_async(top_levels))
    _upsert_tree_leaves(leaves)
    return {"status": "ok", "leaves_found": len(leaves), "top_levels": top_levels}


def _pick_leaf_via_gigachat(candidates: list, niche: str, account_id: str = None):
    """Просит GigaChat выбрать ОДИН лист дерева категорий (по номеру в списке), максимально
    подходящий нише клиента. ИИ не может придумать вариант вне перечисленного списка - только
    вернуть номер, отсюда исключено вранье в духе выдуманных категорий."""
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole
    import json as _json
    import re as _re

    options_text = "\n".join(f"{i}: {c.path}" for i, c in enumerate(candidates))
    prompt = (
        f"Ниша товара/услуги клиента: '{niche}'\n\n"
        "Ниже список листовых категорий дерева документации Avito Автозагрузка (полный путь через ' > '). "
        "Выбери ОДНУ категорию, максимально точно соответствующую нише клиента.\n\n"
        f"{options_text}\n\n"
        'Верни ТОЛЬКО чистый JSON: {"index": <номер выбранной категории из списка выше>}'
    )
    try:
        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)], temperature=0.1, max_tokens=100, account_id=account_id, operation=("category_leaf_selection" if account_id else "system:category_leaf_selection")).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            raw = match.group(0)
        parsed = _json.loads(raw)
        idx = int(parsed.get("index"))
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except Exception as _suppressed_exc:
        observe_suppressed(__name__, _suppressed_exc, line=659)
    return None


def resolve_template_id_for_niche(api_category: str, niche: str, account_id: str = None) -> dict:
    """
    api_category - категория, определённая на уровне поиска/API Avito (detect_category_endpoint) -
    в дереве документации это, как правило, ПРОМЕЖУТОЧНЫЙ узел (например "Спорт и отдых"), а не готовая
    категория шаблона - внутри нее может быть несколько листьев с разными template_id. Используем
    api_category как ФИЛЬТР ветки дерева (CategoryTreeLeaf.path), а не как конечный ответ.
    Если под фильтром один лист - берём его; если несколько - GigaChat выбирает конкретный лист
    по нише клиента (по названиям листьев, не выдумывая несуществующий вариант).
    Возвращает {"status": "ok", "template_id": str, "path": str} | {"status": "not_found", "message": str}
    """
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTreeLeaf

    db = SessionLocal()
    try:
        query = db.query(CategoryTreeLeaf)
        candidates = query.filter(CategoryTreeLeaf.path.ilike(f"%{api_category}%")).all() if api_category else []
        if not candidates:
            # api_category не сматчился ни с одним путём в дереве - ищем по всему дереву как фолбэк
            candidates = query.all()

        if not candidates:
            return {"status": "not_found", "message": "дерево категорий пусто - нужно сначала запустить crawl_category_tree()"}

        if len(candidates) == 1:
            leaf = candidates[0]
        else:
            leaf = _pick_leaf_via_gigachat(candidates, niche, account_id=account_id)
            if not leaf:
                return {"status": "not_found", "message": f"GigaChat не смог выбрать лист дерева категорий для ниши '{niche}'"}

        return {"status": "ok", "template_id": leaf.template_id, "path": leaf.path}
    finally:
        db.close()


def pick_value_via_gpt(field: dict, niche: str, allowed_values, format_rule: str = None, account_id: str = None) -> dict:
    """
    Подбирает значение поля под конкретную нишу клиента.
    Возвращает {"value": ... или None, "clarify_question": ... или None}.
    value=None означает, что поле реально неоднозначно и требует уточнения человека (например разные
    марки бетона у одного клиента - нельзя гадать) - в этом случае clarify_question уже готовый
    вопрос клиенту понятным языком (не тег, не техническое имя поля).
    """
    import os
    import json as _json
    import requests
    from proxy_pool import get_intl_requests_proxies

    api_key = os.environ.get("OPENAI_API_KEY")
    proxies = get_intl_requests_proxies()

    values_hint = ("Допустимые значения: " + ", ".join(allowed_values)) if allowed_values else "Значение свободное (текст/число), допустимых вариантов не задано."
    rule_hint = f"Требование к формату значения: {format_rule}\n" if format_rule else ""

    prompt = (
        f"Поле объявления Avito: '{field.get('name_ru')}' (тег {field.get('tag')}).\n"
        f"{values_hint}\n"
        f"{rule_hint}"
        f"Пример из документации: {field.get('example')}\n"
        f"Ниша товара клиента: '{niche}'\n\n"
        "Если по этой нише можно ОДНОЗНАЧНО и БЕЗОПАСНО определить правильное значение поля "
        "(например 'Состояние' почти всегда 'Новое' для товара от производителя) - верни это значение "
        "СТРОГО в требуемом формате (если указаны допустимые значения - ровно как в списке, если есть "
        "требование к формату - соблюди его точно, например число без слов).\n"
        "Если поле описывает характеристику, которая РЕАЛЬНО РАЗНИТСЯ между конкретными товарами "
        "этой ниши (например конкретная марка/модель/цвет/размер, когда ниша - это целая категория, "
        "а не один товар) - НЕ угадывай, верни value=null и придумай clarify_question - короткий вопрос "
        "клиенту ПОНЯТНЫМ ЖИВЫМ ЯЗЫКОМ (например 'Какой год постройки у домов?'), а НЕ техническое имя поля.\n\n"
        'Верни ТОЛЬКО чистый JSON: {"value": "..." или null, "clarify_question": "..." или null, "reason": "коротко почему"}'
    )

    try:
        parsed = _category_paid_json(str(account_id or ""), "category_field_value", prompt, 300, 30)
        value = parsed.get("value") if isinstance(parsed, dict) else None
        if allowed_values and value is not None and value not in allowed_values:
            return {"value": None, "clarify_question": (parsed.get("clarify_question") if isinstance(parsed,dict) else None) or f"Уточните значение поля «{field.get('name_ru')}»"}
        return {"value": value, "clarify_question": parsed.get("clarify_question") if isinstance(parsed,dict) else None}
    except Exception:
        return {"value": None, "clarify_question": f"Уточните значение поля «{field.get('name_ru')}»"}


# Поля, которые обрабатываются отдельно и не должны попадать в required_fields базы знаний
# (совпадает со списком исключений в самом промпте structure_fields_via_gpt - защита на случай,
# если документация Avito всё же вернёт один из них).
_SKIP_TAGS = {"Id", "Title", "Description", "Price", "Images", "ImageUrls", "Address", "ContactPhone", "Category"}


def _normalize_category_id(text: str) -> str:
    return (text or "").strip().lower()


def _extract_official_field_docs(html_text: str, template_id) -> list:
    """Достаёт официальный структурированный JSON документации Avito (categoryDataCache), который
    страница уже отдаёт SSR прямо в HTML (<script data-mfe-state="true">) - там готовые от Avito
    required/type/values/values_range/dependency для каждого поля, без риска, что GPT ошибётся при
    пересказе сырого текста страницы. Возвращает [] если блок не найден - тогда вызывающий код
    падает на старый способ через Playwright + structure_fields_via_gpt."""
    marker = 'data-mfe-state="true">'
    idx = html_text.find(marker)
    if idx == -1:
        return []
    content_start = idx + len(marker)
    end = html_text.find("</script>", content_start)
    if end == -1:
        return []
    try:
        data = json.loads(html.unescape(html_text[content_start:end]))
    except Exception:
        return []

    cache = data.get("categoryDataCache") or {}
    key = next((k for k in cache.keys() if k.startswith(f"{template_id}-")), None)
    if not key:
        return []

    fields = []
    for grp in cache[key].get("field_groups", []):
        for f in grp.get("fields", []):
            tag = f.get("tag")
            if not tag or tag in _SKIP_TAGS:
                continue
            values = f.get("values")
            dependency = f.get("dependency")
            field_type=str(f.get("type") or f.get("input_type") or "").lower()
            multiple=bool(f.get("multiple") or f.get("is_multiple") or f.get("multi") or field_type in {"multiselect","multi_select","checkboxes","multiple"})
            fields.append({
                "tag": tag,
                "name_ru": f.get("label") or f.get("description"),
                "required": bool(f.get("required")),
                "allowed_values": [v.get("value") for v in values if v.get("value")] if values else None,
                "format_rule": None,
                "example": f.get("example"),
                "depends_on": " | ".join(dependency) if dependency else None,
                "values_range": f.get("values_range"),
                "multiple": multiple,
            })
    return fields


def fetch_template_fields_via_http(template_id) -> list:
    """Тянет документацию шаблона обычным requests (страница SSR-рендерит нужный JSON сразу в HTML -
    headed-браузер тут не нужен вообще), парсит через _extract_official_field_docs. Резидентный
    прокси-пул ретраит до 3 раз новым портом на попытку - часть портов пула всегда мертва, это норма."""
    import requests
    from proxy_pool import get_intl_requests_proxies

    url = f"https://www.avito.ru/autoload/documentation/templates/{template_id}"
    last_err = None
    headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"}
    for _attempt in range(3):
        try:
            proxies = get_intl_requests_proxies()
            r = requests.get(url, proxies=proxies, timeout=20, headers=headers)
            r.raise_for_status()
            fields = _extract_official_field_docs(r.text, template_id)
            if fields:
                return fields
            last_err = "categoryDataCache не найден на странице"
        except Exception as e:
            last_err = e
            continue
    # Proxy pool may be temporarily unavailable (for example 407). Official Avito docs are public;
    # retry directly before falling back to the heavier Playwright/GPT path.
    try:
        r = requests.get(url, timeout=20, headers=headers)
        r.raise_for_status()
        fields = _extract_official_field_docs(r.text, template_id)
        if fields:
            return fields
        last_err = "categoryDataCache не найден на странице при direct HTTPS"
    except Exception as e:
        last_err = e
    print(f"[fetch_template_fields_via_http] не удалось получить поля шаблона {template_id} через HTTP ({last_err}) - падаю на Playwright+GPT", flush=True)
    return []



def _account_docs_cache_fresh_enough(cached_fields, fetched_at, now=None):
    """True only when cached User Docs are fresh and no newer live-xmlcheck learning awaits enrichment."""
    import datetime as _dt
    now = now or _dt.datetime.utcnow()
    try:
        fresh = bool(fetched_at and (now - fetched_at).total_seconds() < 7 * 86400)
    except Exception:
        fresh = False
    fields = [x for x in (cached_fields or []) if isinstance(x, dict)]
    has_user_docs = any(str(x.get("source") or "") == "official_avito_user_docs" for x in fields)
    has_live_xmlcheck_learning = any(str(x.get("source") or "") == "official_avito_xmlcheck" for x in fields)
    return bool(fresh and has_user_docs and not has_live_xmlcheck_learning)


def refresh_category_template_from_account_docs(template_id, category_path, account_id, db, persist=True, force=False):
    """Enrich a cached Avito template from the official authenticated User Docs API.

    Used when public template scraping knows that a field exists but misses enums/dependencies.
    Exact account credentials are used only to read Avito documentation; secrets never leave the
    existing crypto/token path. Fail-open to cached contract on any transport/permission problem.
    """
    import datetime as _dt, json as _json
    if not account_id or not template_id or not category_path:
        return {"status":"skipped","refreshed":False,"fields":0}
    try:
        import httpx
        from app.models.account import Account
        from app.models.category_template import CategoryTemplate
        from app.crypto_utils import decrypt_secret
        acc=db.query(Account).filter(Account.account_id==str(account_id)).first()
        row=db.query(CategoryTemplate).filter(CategoryTemplate.template_id==str(template_id)).first()
        if not acc or not row or not acc.avito_client_id or not acc.avito_client_secret:
            return {"status":"no_account_credentials","refreshed":False,"fields":0}
        try:
            _cached=_json.loads(row.required_fields or "[]") or []
            # A live xmlcheck can reveal newly-required fields after the last User Docs
            # refresh. Do not call the whole template "fresh" merely because older
            # fields already came from User Docs: the new fields must get one official
            # docs enrichment pass first (enum/dependency/multiplicity may be missing).
            if (not force) and _account_docs_cache_fresh_enough(_cached, getattr(row,"fetched_at",None)):
                return {"status":"fresh_account_docs","refreshed":False,"fields":len(_cached)}
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=883)
        # CATEGORY_DOCS_SHARED_ACCOUNT_THROTTLE_V1: official User Docs reads are
        # provider pressure too. A tenant Retry-After observed by any trusted BORIS
        # stage wins before token/docs I/O and a local 429 is published tenant-wide.
        from app.services.avito_account_throttle import account_throttle_remaining as _cat_throttle_remaining, record_account_throttle as _cat_record_throttle
        if _cat_throttle_remaining(account_id) > 0:
            return {"status":"docs_unavailable","http_status":429,"reason":"avito_account_throttled","refreshed":False,"fields":0}
        def _cat_retry_after(resp):
            raw=str(resp.headers.get("Retry-After") or "").strip()
            if not raw: return 30
            try: return max(0,int(float(raw)))
            except Exception:
                try:
                    from email.utils import parsedate_to_datetime
                    from datetime import datetime, timezone
                    dt=parsedate_to_datetime(raw)
                    if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                    return max(0,int((dt.astimezone(timezone.utc)-datetime.now(timezone.utc)).total_seconds()))
                except Exception: return 30
        with httpx.Client(timeout=25,trust_env=False) as client:
            tr=client.post("https://api.avito.ru/token/",data={"grant_type":"client_credentials","client_id":decrypt_secret(acc.avito_client_id),"client_secret":decrypt_secret(acc.avito_client_secret)})
            if tr.status_code==429:
                _cat_record_throttle(account_id,_cat_retry_after(tr),source="category_docs_token_429")
                return {"status":"token_error","http_status":429,"reason":"avito_account_throttled","refreshed":False,"fields":0}
            if tr.status_code!=200:
                return {"status":"token_error","refreshed":False,"fields":0}
            token=(tr.json() or {}).get("access_token")
            if not token: return {"status":"token_error","refreshed":False,"fields":0}
            head={"Authorization":"Bearer "+token}
            if _cat_throttle_remaining(account_id) > 0:
                return {"status":"docs_unavailable","http_status":429,"reason":"avito_account_throttled","refreshed":False,"fields":0}
            tree=client.get("https://api.avito.ru/autoload/v1/user-docs/tree",headers=head)
            if tree.status_code==429:
                _cat_record_throttle(account_id,_cat_retry_after(tree),source="category_docs_tree_429")
            if tree.status_code!=200:
                return {"status":"docs_unavailable","http_status":tree.status_code,"refreshed":False,"fields":0}
            wanted=[x.strip() for x in str(category_path).split(">") if x.strip()]
            candidates=[]
            def walk(node,path):
                if isinstance(node,dict):
                    name=str(node.get("name") or "").strip(); cur=path+([name] if name else [])
                    slug=str(node.get("slug") or "").strip()
                    if slug and wanted and [x.lower() for x in cur[-len(wanted):]]==[x.lower() for x in wanted]:
                        candidates.append(slug)
                    for key,val in node.items():
                        if key not in {"name","slug"}: walk(val,cur)
                elif isinstance(node,list):
                    for val in node: walk(val,path)
            walk(tree.json(),[])
            if not candidates:
                # Safe fallback only to an exact leaf-name match; ambiguity is rejected.
                leaf=wanted[-1].lower() if wanted else ""; leaf_hits=[]
                def walk_leaf(node):
                    if isinstance(node,dict):
                        if str(node.get("name") or "").strip().lower()==leaf and node.get("slug"): leaf_hits.append(str(node.get("slug")))
                        for val in node.values(): walk_leaf(val)
                    elif isinstance(node,list):
                        for val in node: walk_leaf(val)
                walk_leaf(tree.json())
                if len(set(leaf_hits))==1: candidates=[leaf_hits[0]]
            if len(set(candidates))!=1:
                return {"status":"node_ambiguous","refreshed":False,"fields":0}
            slug=candidates[0]
            if _cat_throttle_remaining(account_id) > 0:
                return {"status":"fields_unavailable","http_status":429,"reason":"avito_account_throttled","refreshed":False,"fields":0}
            fr=client.get(f"https://api.avito.ru/autoload/v1/user-docs/node/{slug}/fields",headers=head)
            if fr.status_code==429:
                _cat_record_throttle(account_id,_cat_retry_after(fr),source="category_docs_fields_429")
            if fr.status_code!=200:
                return {"status":"fields_unavailable","http_status":fr.status_code,"refreshed":False,"fields":0}
            official=[]
            for f in (fr.json() or {}).get("fields") or []:
                tag=str(f.get("tag") or "").strip()
                if not tag or tag in _SKIP_TAGS: continue
                content=list(f.get("content") or [])
                allowed=[]; deps=[]; required=False; multiple=False; fmt=""
                for c in content:
                    required=required or bool(c.get("required") or c.get("required_by_dependency"))
                    ft=str(c.get("field_type") or "").lower(); multiple=multiple or ft in {"multiselect","checkbox","checkboxes","multiple"}
                    for v in (c.get("values") or []):
                        val=v.get("value") if isinstance(v,dict) else v
                        if val not in (None,"") and str(val) not in [str(x) for x in allowed]: allowed.append(val)
                    for d in (c.get("dependencies_text") or []):
                        if str(d).strip() and str(d).strip() not in deps: deps.append(str(d).strip())
                    vr=c.get("values_range") or {}
                    if vr: fmt=_json.dumps(vr,ensure_ascii=False)
                official.append({"tag":tag,"name_ru":f.get("label") or f.get("descriptions") or tag,"required":required,
                                 "depends_on":" | ".join(deps),"allowed_values":allowed or None,"format_rule":fmt or None,
                                 "multiple":multiple,"source":"official_avito_user_docs"})
            if not official:
                return {"status":"empty_fields","refreshed":False,"fields":0}
        # Schema-drift callers need authoritative live fields without rewriting a
        # shared legacy CategoryTemplate. Return a read-only contract snapshot.
        if not persist:
            return {"status":"official_account_docs_readonly","refreshed":False,"fields":len(official),"node_slug":slug,
                    "category_path":str(category_path),"official_fields":official}
        # Merge official docs over cached fields so a temporary API omission cannot delete knowledge.
        try: cached=_json.loads(row.required_fields or "[]") or []
        except Exception: cached=[]
        merged={str(x.get("tag")):dict(x) for x in cached if isinstance(x,dict) and x.get("tag")}
        for f in official:
            old=merged.get(f["tag"],{}); combined=dict(old); combined.update({k:v for k,v in f.items() if v not in (None,"",[])})
            if f.get("allowed_values") is not None: combined["allowed_values"]=f.get("allowed_values") or []
            combined["required"]=bool(f.get("required") or old.get("required")); merged[f["tag"]]=combined
        fields=list(merged.values())
        row.required_fields=_json.dumps(fields,ensure_ascii=False)
        row.field_rules=_json.dumps({f["tag"]:f.get("format_rule") for f in fields if f.get("format_rule")},ensure_ascii=False)
        row.enum_values=_json.dumps({f["tag"]:f.get("allowed_values") for f in fields if f.get("allowed_values")},ensure_ascii=False)
        row.fetched_at=_dt.datetime.utcnow(); db.commit()
        return {"status":"official_account_docs","refreshed":True,"fields":len(fields),"node_slug":slug}
    except Exception as exc:
        try: db.rollback()
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=996)
        return {"status":"error","refreshed":False,"fields":0,"reason":type(exc).__name__}


def refresh_category_template_if_stale(template_id, db, max_age_days=7):
    """Refresh the existing CategoryTemplate cache from official Avito docs when stale.
    Fail-open to the last known contract if Avito is temporarily unavailable; never delete cached fields.
    """
    import datetime as _dt
    from app.models.category_template import CategoryTemplate
    row=db.query(CategoryTemplate).filter(CategoryTemplate.template_id==str(template_id)).first()
    if not row:
        return {"status":"missing","refreshed":False,"fields":0}
    fetched=getattr(row,"fetched_at",None)
    now=_dt.datetime.utcnow()
    if fetched and (now-fetched).total_seconds() < max(1,int(max_age_days))*86400:
        try: count=len(json.loads(row.required_fields or "[]"))
        except Exception: count=0
        return {"status":"fresh","refreshed":False,"fields":count}
    fields=fetch_template_fields_via_http(template_id)
    if not fields:
        try: count=len(json.loads(row.required_fields or "[]"))
        except Exception: count=0
        # Cooldown after a failed official-doc refresh (429/proxy/network): preserve the full cached contract
        # and avoid hammering Avito on every intake page load.
        row.fetched_at=now
        db.commit()
        return {"status":"stale_cached","refreshed":False,"fields":count}
    fields=[f for f in fields if f.get("tag") not in _SKIP_TAGS]
    row.required_fields=json.dumps(fields,ensure_ascii=False)
    row.field_rules=json.dumps({f["tag"]:f["format_rule"] for f in fields if f.get("format_rule")},ensure_ascii=False)
    row.enum_values=json.dumps({f["tag"]:f["allowed_values"] for f in fields if f.get("allowed_values")},ensure_ascii=False)
    row.fetched_at=now
    db.commit()
    return {"status":"refreshed","refreshed":True,"fields":len(fields)}


def get_category_template(category_id: str, niche: str = None, category_name_hint: str = None, template_id_hint: int = None, api_category: str = None, account_id: str = None) -> dict:
    """
    База знаний полей категории Avito (модель CategoryTemplate), ленивая загрузка:
    - есть в БД -> возвращает сразу
    - нет -> находит template_id через resolve_template_id_for_niche (дерево документации +
      GigaChat для выбора листа), скрейпит документацию шаблона, структурирует через GPT, сохраняет в БД
    api_category - категория, уже определённая на уровне API/поиска Avito (detect_category_endpoint) -
    используется как фильтр ветки дерева при поиске template_id, см. resolve_template_id_for_niche.
    Возвращает {"status": "ok"|"not_found", "category_id", "category_name", "template_id",
                "required_fields": [...], "field_rules": {...}, "enum_values": {...}}
    "required_fields" - несмотря на имя, здесь ВСЕ поля категории (и обязательные, и нет);
    какое поле обязательно - смотри ключ "required" внутри каждого элемента списка.
    """
    from app.db.session import SessionLocal
    from app.models.category_template import CategoryTemplate

    cid = _normalize_category_id(category_id)
    niche = niche or category_id

    db = SessionLocal()
    try:
        row = db.query(CategoryTemplate).filter(CategoryTemplate.category_id == cid).first()
        if row and row.required_fields:
            return {
                "status": "ok",
                "category_id": row.category_id,
                "category_name": row.category_name,
                "template_id": row.template_id,
                "required_fields": json.loads(row.required_fields),
                "field_rules": json.loads(row.field_rules) if row.field_rules else {},
                "enum_values": json.loads(row.enum_values) if row.enum_values else {},
            }

        template_id = template_id_hint
        found_path = category_name_hint or niche
        if not template_id:
            found = resolve_template_id_for_niche(api_category or niche, niche, account_id=account_id)
            if found.get("status") != "ok":
                return {"status": "not_found", "message": found.get("message", "не удалось определить template_id категории")}
            template_id = found.get("template_id")
            found_path = found.get("path", found_path)

        try:
            # Основной путь - официальный JSON прямо из SSR HTML (точные required/type/values/
            # dependency от Avito, без риска пересказа GPT и без headed-браузера). Playwright+GPT -
            # только запасной вариант, если разметка страницы вдруг изменится и JSON не найдётся.
            fields = fetch_template_fields_via_http(template_id)
            if not fields:
                raw_text = asyncio.run(scrape_template_page(template_id))
                fields = structure_fields_via_gpt(raw_text, found_path, account_id=account_id)
        except Exception as e:
            # Не роняем весь запрос из-за сбоя скрейпинга/структурирования - отдаём тот же
            # понятный "категория не резолвится", что и при провале resolve_template_id_for_niche.
            return {"status": "not_found", "message": f"не удалось загрузить документацию шаблона {template_id}: {e}"}
        fields = [f for f in fields if f.get("tag") not in _SKIP_TAGS]

        field_rules = {f["tag"]: f["format_rule"] for f in fields if f.get("format_rule")}
        enum_values = {f["tag"]: f["allowed_values"] for f in fields if f.get("allowed_values")}

        if row:
            row.category_name = found_path
            row.template_id = str(template_id)
            row.required_fields = json.dumps(fields, ensure_ascii=False)
            row.field_rules = json.dumps(field_rules, ensure_ascii=False)
            row.enum_values = json.dumps(enum_values, ensure_ascii=False)
        else:
            row = CategoryTemplate(
                category_id=cid, category_name=found_path, template_id=str(template_id),
                required_fields=json.dumps(fields, ensure_ascii=False),
                field_rules=json.dumps(field_rules, ensure_ascii=False),
                enum_values=json.dumps(enum_values, ensure_ascii=False),
            )
            db.add(row)
        db.commit()

        return {
            "status": "ok",
            "category_id": cid,
            "category_name": found_path,
            "template_id": str(template_id),
            "required_fields": fields,
            "field_rules": field_rules,
            "enum_values": enum_values,
        }
    finally:
        db.close()


def group_products_by_niche(products: list, account_niche: str = None, account_id: str = None) -> list:
    """
    Группирует товары по нишам через GigaChat - в одном аккаунте могут быть товары РАЗНЫХ
    категорий Avito (пример: дома из бруса + штукатурка фасадов - разные категории с разными
    обязательными полями), хотя продаёт их один и тот же клиент.
    `products` - список dict с ключом "title" (минимум). Возвращает список групп:
    [{"niche": "название ниши", "indices": [0, 3, 5]}, ...] - каждый индекс товара попадает
    РОВНО в одну группу, ни один не теряется (даже если GigaChat недоступен или ответил мимо JSON -
    тогда все товары уходят в одну общую группу, а не пропадают молча).
    """
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole

    if not products:
        return []
    if len(products) == 1:
        return [{"niche": account_niche or products[0].get("title", "товар"), "indices": [0]}]

    titles_text = "\n".join(f"{i}: {p.get('title', '')}" for i, p in enumerate(products))
    context = f"Ниша бизнеса аккаунта: {account_niche}\n\n" if account_niche else ""
    prompt = (
        f"{context}Ниже список товаров с индексами. Сгруппируй их по НИШАМ/КАТЕГОРИЯМ Avito - "
        "то есть по тому, у каких товаров будут РАЗНЫЕ обязательные поля объявления "
        "(например 'дома из бруса' и 'штукатурка фасадов' - разные категории Avito, хотя обе "
        "могут продаваться одной строительной компанией). Не дели по мелким различиям внутри "
        "одной ниши (цвет, размер, модель - это ОДНА ниша, не разные группы).\n\n"
        "Товары:\n" + titles_text + "\n\n"
        'Верни ТОЛЬКО чистый JSON-массив групп: [{"niche": "название ниши", "indices": [0,1,2]}, ...]. '
        "Каждый индекс должен попасть ровно в одну группу, ни один индекс не пропускай."
    )
    try:
        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)], temperature=0.1, max_tokens=1500, account_id=account_id, operation="product_niche_grouping").strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        groups = json.loads(raw)
        if not isinstance(groups, list):
            raise ValueError("GigaChat вернул не список")
    except Exception as e:
        print(f"[group_products_by_niche] не удалось сгруппировать через GigaChat ({e}) - одна общая группа")
        return [{"niche": account_niche or "товары", "indices": list(range(len(products)))}]

    seen = set()
    cleaned = []
    for g in groups:
        idxs = [i for i in g.get("indices", []) if isinstance(i, int) and 0 <= i < len(products) and i not in seen]
        if not idxs:
            continue
        seen.update(idxs)
        cleaned.append({"niche": g.get("niche", "товары"), "indices": idxs})
    missing = [i for i in range(len(products)) if i not in seen]
    if missing:
        cleaned.append({"niche": account_niche or "прочее", "indices": missing})
    return cleaned


def resolve_required_fields(category_id: str, niche: str, characteristics: dict = None, template: dict = None, api_category: str = None, account_id: str = None) -> dict:
    """
    Общая точка входа для create_draft_listings (plan_items.py) и parsed_products_to_drafts
    (parser.py): резолвит поля категории (обязательные и необязательные - см. get_category_template)
    под конкретную нишу/товар. НЕ выдумывает значения - то, что ИИ не может определить однозначно
    для ОБЯЗАТЕЛЬНОГО поля, попадает в needs_clarification готовыми вопросами клиенту понятным языком.
    Для необязательных полей неопределённость не блокирует ничего - поле просто остаётся незаполненным.
    api_category - категория, уже определённая на уровне API/поиска Avito (detect_category_endpoint) -
    прокидывается в get_category_template как фильтр ветки дерева при поиске template_id.
    Возвращает {"status": "ok"|"not_found", "message"?, "resolved": {tag: value}, "needs_clarification": [...]}
    """
    template = template or get_category_template(category_id, niche, api_category=api_category, account_id=account_id)
    if template.get("status") != "ok":
        return {"status": "not_found", "message": template.get("message", "категория не определена"), "resolved": {}, "needs_clarification": []}

    niche_context = niche
    if characteristics:
        chars_text = ", ".join(f"{k}: {v}" for k, v in characteristics.items())
        niche_context = f"{niche} (характеристики конкретного товара: {chars_text})"

    resolved = {}
    needs_clarification = []

    all_fields = template.get("required_fields", [])
    independent_fields = [f for f in all_fields if not f.get("depends_on")]
    dependent_fields = [f for f in all_fields if f.get("depends_on")]

    # Первый проход - поля БЕЗ условной зависимости, сюда же попадает Availability (у него самого
    # условия нет) - его значение нужно знать ДО разбора зависимых полей ниже.
    for f in independent_fields:
        tag = f["tag"]
        allowed = f.get("allowed_values")
        picked = pick_value_via_gpt(f, niche_context, allowed, f.get("format_rule"), account_id=account_id)
        if picked.get("value") is None:
            if f.get("required"):
                needs_clarification.append({
                    "tag": tag, "name_ru": f.get("name_ru"),
                    "question": picked.get("clarify_question") or f"Уточните значение поля «{f.get('name_ru')}»",
                })
            # необязательное поле, ИИ не смог однозначно подобрать значение - просто пропускаем,
            # это не повод ни блокировать создание черновика, ни дёргать клиента вопросом
        else:
            resolved[tag] = picked["value"]

    # Второй проход - условно обязательные поля (Width/Height/Depth/Color и т.п. с dependency вида
    # "Обязательно, если в поле Доступность указано значение 'В наличии'"). Если товар оказался
    # "Под заказ" (изготавливается индивидуально, точного размера/цвета нет в принципе) - Avito сам
    # снимает обязательность этих полей, поэтому НЕ гадаем и НЕ усредняем значение - оставляем пустыми,
    # это честный и официально предусмотренный путь, а не наша самодеятельность.
    # Прочие условные зависимости, не связанные с Availability, оставляем как раньше без изменений -
    # не подставляем автоматически, не спрашиваем (не наш случай, отдельная задача).
    in_stock_dependency = re.compile(r"доступн\w*.{0,60}в\s+наличии", re.IGNORECASE)
    availability = resolved.get("Availability")
    for f in dependent_fields:
        dep_text = f.get("depends_on") or ""
        if not in_stock_dependency.search(dep_text):
            continue  # незнакомая нам условная зависимость - прежнее консервативное поведение
        if availability == "Под заказ":
            continue  # официально необязательно для товаров под заказ - не выдумываем значение
        if f.get("required"):
            needs_clarification.append({
                "tag": f["tag"], "name_ru": f.get("name_ru"),
                "question": f"Уточните значение поля «{f.get('name_ru')}» (обязательно для товара в наличии)",
            })

    return {"status": "ok", "resolved": resolved, "needs_clarification": needs_clarification}


async def _anon_search_category(niche: str, city: str = "moskva") -> dict | None:
    """Анонимный поиск по нише на Avito, клик на первое объявление, категория из URL пути."""
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    from urllib.parse import quote
    from proxy_pool import get_playwright_proxy, get_clean_playwright_proxy

    stealth = Stealth()
    url = f"https://www.avito.ru/{city}?q={quote(niche)}"

    async with async_playwright() as p:
        for attempt in range(3):
            proxy = await get_clean_playwright_proxy()
            browser = None
            try:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"], proxy=proxy)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 900}
                )
                await stealth.apply_stealth_async(context)
                page = await context.new_page()
                await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media") else route.continue_())
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                item = page.locator("[data-marker='item-title']").first
                await item.click(timeout=8000)
                await page.wait_for_timeout(2500)

                final_url = page.url
                await browser.close()

                # путь категории — сегменты URL между доменом/городом и /id_объявления
                # пример: avito.ru/moskva/mebel_i_interer/shkafy_i_bufety/... -> берём читаемые сегменты
                import re
                m = re.search(r"avito\.ru/[^/]+/([a-z0-9_]+(?:/[a-z0-9_]+)*)", final_url)
                if m:
                    path_slug = m.group(1)
                    return {"source": "anonymous_search", "url": final_url, "path_slug": path_slug}
                return None
            except Exception as e:
                if browser:
                    try:
                        await browser.close()
                    except Exception as _suppressed_exc:
                        observe_suppressed(__name__, _suppressed_exc, line=1288)
                continue
    return None


def resolve_category_for_account(account_id: str, niche: str, city: str = "moskva") -> dict:
    """
    Определяет категорию Avito для аккаунта.
    Приоритет: 1) реальные опубликованные объявления через API, 2) анонимный поиск по нише.
    Возвращает {"category_name": str, "source": "api"|"anonymous_search", ...} или {"error": ...}.
    """
    import httpx
    import asyncio
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.api.avito import get_avito_token

    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc:
            return {"error": f"аккаунт {account_id} не найден"}

        # CATEGORY_RESOLVER_SHARED_ACCOUNT_THROTTLE_V1: category detection must
        # not bypass a Retry-After observed by stats/CPX/autoload workers. Check
        # before token/provider I/O and publish any local items 429 tenant-wide.
        from app.services.avito_account_throttle import account_throttle_remaining as _detect_throttle_remaining, record_account_throttle as _detect_record_throttle
        if _detect_throttle_remaining(account_id) > 0:
            token = None
        else:
            token_data = get_avito_token(account_id)
            token = token_data.get("access_token") if isinstance(token_data, dict) else None

        # метод 1: реальные объявления
        try:
            items_resp = None
            if token and _detect_throttle_remaining(account_id) <= 0:
                items_resp = httpx.get(
                    "https://api.avito.ru/core/v1/items",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"per_page": 1, "page": 1, "status": "active"},
                    timeout=15
                )
            if items_resp is not None and items_resp.status_code == 429:
                # CATEGORY_RESOLVER_RETRY_AFTER_HTTPDATE_V1: honor both delta-seconds
                # and RFC/HTTP-date Retry-After values; never collapse a valid
                # provider cooldown to the default merely because it is not numeric.
                raw=str(items_resp.headers.get("Retry-After") or "").strip()
                retry=30
                if raw:
                    try:
                        retry=max(0,int(float(raw)))
                    except Exception:
                        try:
                            from email.utils import parsedate_to_datetime as _parse_ra
                            from datetime import datetime as _ra_dt, timezone as _ra_tz
                            _when=_parse_ra(raw)
                            if _when.tzinfo is None:
                                _when=_when.replace(tzinfo=_ra_tz.utc)
                            retry=max(0,int((_when.astimezone(_ra_tz.utc)-_ra_dt.now(_ra_tz.utc)).total_seconds()))
                        except Exception:
                            retry=30
                _detect_record_throttle(account_id,retry,source="category_resolver_items_429")
            if items_resp is not None and items_resp.status_code == 200:
                resources = items_resp.json().get("resources", [])
                if resources:
                    cat = resources[0].get("category", {})
                    cat_name = cat.get("name", "")
                    if cat_name:
                        return {"category_name": cat_name, "source": "api", "category_id": cat.get("id")}
        except Exception as _suppressed_exc:
            observe_suppressed(__name__, _suppressed_exc, line=1359)

        # метод 2: подбор из НАШЕГО справочника категорий (660 полных шаблонов) — без похода в Avito
        from app.models.category_template import CategoryTemplate as _CT
        nm = (niche or "").strip()
        tpl = None
        if nm:
            # сначала по category_id (короткий ключ ниши), потом по category_name (полный путь/имя)
            tpl = db.query(_CT).filter(_CT.category_id.ilike("%" + nm + "%")).first()
            if not tpl:
                tpl = db.query(_CT).filter(_CT.category_name.ilike("%" + nm + "%")).first()
        if tpl:
            # человекочитаемое имя категории — последний сегмент пути category_name
            cat_path = tpl.category_name or nm
            readable = cat_path.split(">")[-1].strip() if ">" in cat_path else cat_path
            return {
                "category_name": readable,
                "source": "local_catalog",
                "category_id": tpl.category_id,
                "template_id": tpl.template_id,
                "path": tpl.category_name
            }

        return {"error": f"категория для ниши '{niche}' не найдена в справочнике"}
    finally:
        db.close()
