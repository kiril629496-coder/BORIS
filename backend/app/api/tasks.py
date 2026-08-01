import os as _os_gc
_GC_MODEL = _os_gc.environ.get("GIGACHAT_MODEL", "GigaChat" + "-Pro")
from fastapi import APIRouter, Depends
from pydantic import BaseModel
import json
import threading
import time
import base64 as _b64
import os
import random
from app.db.session import SessionLocal, engine
from app.db.base import Base
from app.models.task import Task
from app.api.auth import get_current_user_or_internal

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

Base.metadata.create_all(bind=engine, tables=[Task.__table__])

class CreateTaskRequest(BaseModel):
    account_id: str = "default"
    task_type: str
    payload: dict
    run_at: str | None = None  # ISO datetime строка — если задано, задача ждёт этого момента

@router.post("/create")
def create_task(req: CreateTaskRequest):
    from datetime import datetime as _dt
    db = SessionLocal()
    try:
        full_payload = {**req.payload, "account_id": req.account_id}
        run_at_dt = None
        if req.run_at:
            try:
                run_at_dt = _dt.fromisoformat(req.run_at)
            except Exception:
                run_at_dt = None
        task = Task(account_id=req.account_id, task_type=req.task_type, status="queued",
                    payload=json.dumps(full_payload, ensure_ascii=False), run_at=run_at_dt)
        db.add(task)
        db.commit()
        db.refresh(task)
        return {"status": "ok", "task_id": task.id, "run_at": req.run_at}
    finally:
        db.close()

@router.get("/status/{task_id}")
def get_task_status(task_id: int):
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return {"status": "error", "message": "Задача не найдена"}
        return {
            "status": task.status,
            "result": json.loads(task.result) if task.result else None,
            "error_message": task.error_message
        }
    finally:
        db.close()

class ConfirmTaskRequest(BaseModel):
    action: str  # "proceed" | "cancel"
    answer: str = ""  # текстовый ответ владельца/сотрудника на вопрос конвейера (необязательно)

@router.post("/{task_id}/confirm")
def confirm_task(task_id: int, req: ConfirmTaskRequest, user=Depends(get_current_user_or_internal)):
    """Отвечает на needs_confirmation (конвейер направления упёрся в неоднозначность категории или
    проверку партии перед публикацией) - владелец ИЛИ любой сотрудник, ведущий этот аккаунт (не
    обязательно owner). "cancel" - отменяет задачу насовсем. "proceed" - возвращает ТУ ЖЕ задачу в
    очередь (не с начала всего конвейера - именно с этого шага): pipeline_texts продолжит с
    резолвнутыми полями (и текстовым ответом владельца, если он есть - см. user_answer),
    пропустив неоднозначные; pipeline_finalize опубликует партию без повторной проверки."""
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            return {"status": "error", "message": "Задача не найдена"}
        if user is not None and user.role != "owner" and task.account_id != user.account_id:
            return {"status": "error", "message": "Нет доступа к этой задаче"}
        if task.status != "needs_confirmation":
            return {"status": "error", "message": f"Задача не ждёт подтверждения (текущий статус: {task.status})"}

        if req.action == "cancel":
            task.status = "cancelled"
            task.error_message = "Отменено владельцем"
            db.commit()
            return {"status": "ok", "task_status": "cancelled"}

        if req.action != "proceed":
            return {"status": "error", "message": f"Неизвестное действие: {req.action}"}

        payload = json.loads(task.payload)
        if task.task_type == "pipeline_texts":
            payload["force_confirm"] = True
            if req.answer.strip():
                payload["user_answer"] = req.answer.strip()
        elif task.task_type == "pipeline_finalize":
            payload["auto_check"] = False
        task.payload = json.dumps(payload, ensure_ascii=False)
        task.status = "queued"
        task.error_message = None
        db.commit()
        return {"status": "ok", "task_status": "queued"}
    finally:
        db.close()


@router.get("/list")
def list_tasks(account_id: str = "default"):
    db = SessionLocal()
    try:
        tasks = db.query(Task).filter(Task.account_id == account_id).order_by(Task.created_at.desc()).limit(50).all()
        out = []
        for t in tasks:
            try:
                result = json.loads(t.result) if t.result else None
            except Exception:
                result = None
            try:
                payload = json.loads(t.payload) if t.payload else None
            except Exception:
                payload = None
            out.append({
                "id": t.id, "task_type": t.task_type, "status": t.status, "created_at": str(t.created_at),
                "depends_on_task_id": t.depends_on_task_id, "error_message": t.error_message,
                "result": result, "payload": payload,
            })
        return {"status": "ok", "tasks": out}
    finally:
        db.close()

_browser_task_semaphore = threading.Semaphore(2)  # не более 2 одновременных Chromium-задач на всех воркеров разом
_BROWSER_TASK_TYPES = {"parse_site", "city_analysis", "fetch_all_galleries"}

def _worker_loop():
    """Фоновый воркер: раз в 3 секунды проверяет очередь и выполняет задачи по одной (под тариф Freemium GigaChat)."""
    while True:
        time.sleep(3)
        db = SessionLocal()
        try:
            from datetime import datetime as _dt
            from sqlalchemy import or_
            # SELECT FOR UPDATE SKIP LOCKED — атомарно захватываем кандидатов, чтобы
            # несколько параллельных воркеров не схватили одну и ту же (race condition).
            # Берём НЕСКОЛЬКО кандидатов подряд по created_at и ищем первого, чья зависимость
            # (depends_on_task_id) уже выполнена - остальные (без зависимости) выполняются как раньше.
            candidates = db.query(Task).filter(
                Task.status == "queued",
                or_(Task.run_at == None, Task.run_at <= _dt.now())
            ).order_by(Task.created_at.asc()).with_for_update(skip_locked=True).limit(20).all()

            task = None
            for cand in candidates:
                if cand.depends_on_task_id is None:
                    task = cand
                    break
                pred = db.query(Task).filter(Task.id == cand.depends_on_task_id).first()
                if pred is None or pred.status == "done":
                    task = cand
                    break
                if pred.status in ("error", "cancelled"):
                    cand.status = "cancelled"
                    cand.error_message = f"Отменено: задача-предшественник #{pred.id} завершилась с ошибкой"
                    continue
                # предшественник ещё queued/running - ждём своей очереди, пробуем следующего кандидата
                continue

            if not task:
                db.commit()
                continue
            task.status = "running"
            db.commit()
            _needs_browser_slot = task.task_type in _BROWSER_TASK_TYPES
            if _needs_browser_slot:
                _browser_task_semaphore.acquire()
            try:
                payload = json.loads(task.payload)
                if task.task_type == "generate_images":
                    result = _run_generate_images(payload)
                elif task.task_type == "city_analysis":
                    result = _run_city_analysis(payload, task=task, db=db)
                elif task.task_type == "parse_site":
                    result = _run_parse_site(payload, task=task, db=db)
                elif task.task_type == "product_banner":
                    result = _run_product_banner(payload)
                elif task.task_type == "product_description":
                    result = _run_product_description(payload)
                elif task.task_type == "fetch_all_galleries":
                    result = _run_fetch_all_galleries(payload)
                elif task.task_type == "enrich_descriptions":
                    result = _run_enrich_parsed_descriptions(payload, task=task, db=db)
                elif task.task_type == "report_back":
                    result = _run_report_back(payload)
                elif task.task_type == "ab_test":
                    result = _run_ab_test(payload)
                elif task.task_type == "avito_categories_sync":
                    result = _run_avito_categories_sync(payload, task=task, db=db)
                elif task.task_type == "execute_plan_subtask":
                    result = _run_execute_plan_subtask(payload)
                elif task.task_type == "pipeline_texts":
                    result = _run_pipeline_texts(payload, task=task, db=db)
                elif task.task_type == "pipeline_banners":
                    result = _run_pipeline_banners(payload, task=task, db=db)
                elif task.task_type == "pipeline_finalize":
                    result = _run_pipeline_finalize(payload, task=task, db=db)
                else:
                    raise Exception(f"Неизвестный тип задачи: {task.task_type}")
                task.result = json.dumps(result, ensure_ascii=False)
                # needs_confirmation - шаг конвейера упёрся в неоднозначность (категория требует
                # уточнения, или проверка партии перед публикацией не прошла) и ждёт явного решения
                # владельца ("Опубликовать всё равно"/"Отмена" в карточке истории), а не тихого "done".
                task.status = "needs_confirmation" if isinstance(result, dict) and result.get("status") == "needs_confirmation" else "done"
            except Exception as e:
                _RETRYABLE_TYPES = {"execute_plan_subtask", "fetch_all_galleries"}
                _MAX_RETRIES = 3
                _retry_count = payload.get("_retry_count", 0) if isinstance(payload, dict) else 0
                if task.task_type in _RETRYABLE_TYPES and _retry_count < _MAX_RETRIES:
                    _retry_count += 1
                    payload["_retry_count"] = _retry_count
                    task.payload = json.dumps(payload, ensure_ascii=False)
                    task.status = "queued"
                    task.error_message = f"попытка {_retry_count}/{_MAX_RETRIES} не удалась: {str(e)[:300]} - переставлено в очередь"
                    import datetime as _dt_retry
                    task.run_at = _dt_retry.datetime.utcnow() + _dt_retry.timedelta(seconds=20 * _retry_count)
                else:
                    task.status = "error"
                    task.error_message = str(e)[:500]
                    try:
                        _escalate_to_director(db, task.account_id, task.task_type, task.error_message)
                    except Exception:
                        pass
            finally:
                if _needs_browser_slot:
                    _browser_task_semaphore.release()
            db.commit()
        finally:
            db.close()

def _escalate_to_director(db, account_id, task_type, error_message):
    """Записывает неудачную задачу в глобальный список для директор-Бориса,
    чтобы он мог сразу сообщить владельцу об ошибке в конкретном аккаунте."""
    from app.models.storage import Storage
    import datetime as _dt2

    row = db.query(Storage).filter(Storage.account_id == "_global", Storage.key == "director_escalations").first()
    escalations = json.loads(row.value) if row else []
    escalations.insert(0, {
        "ts": _dt2.datetime.utcnow().isoformat(),
        "account_id": account_id,
        "task_type": task_type,
        "error_message": error_message,
        "read": False
    })
    escalations = escalations[:200]  # держим последние 200
    if row:
        row.value = json.dumps(escalations, ensure_ascii=False)
    else:
        row = Storage(account_id="_global", key="director_escalations", value=json.dumps(escalations, ensure_ascii=False))
        db.add(row)
    db.commit()

def _run_generate_images(payload):
    from gigachat_pool import RateLimitedGigaChat as GigaChat
    from gigachat.models import Chat, Messages, MessagesRole
    import re as _re

    prompt = payload.get("prompt", "")
    style = payload.get("style", "")
    count = payload.get("count", 1)
    folder = payload.get("folder", "общая")
    account_id = payload.get("account_id", "otdushi")
    IMAGES_DIR = "/root/BORIS/backend/images"
    # для otdushi — обратная совместимость со старыми плоскими папками, иначе изолированная папка клиента
    flat_path = f"{IMAGES_DIR}/{folder}"
    if account_id == "otdushi" and os.path.isdir(flat_path):
        folder_path = flat_path
        url_prefix = f"http://193.160.209.44:8000/images/{folder}"
    else:
        folder_path = f"{IMAGES_DIR}/{account_id}/{folder}"
        url_prefix = f"http://193.160.209.44:8000/images/{account_id}/{folder}"
    os.makedirs(folder_path, exist_ok=True)
    results = []
    for i in range(count):
        full_prompt = f"Нарисуй {prompt}. Стиль: {style}. Профессиональное качество. ВАЖНО: без текста, без надписей."
        try:
            with GigaChat(credentials=os.getenv("GIGACHAT_KEY"), scope=_os_gc.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"), model=_GC_MODEL, verify_ssl_certs=False, timeout=120) as client:
                response = client.chat(Chat(
                    messages=[
                        Messages(role=MessagesRole.SYSTEM, content="Ты — художник Kandinsky."),
                        Messages(role=MessagesRole.USER, content=full_prompt)
                    ],
                    function_call="auto"
                ))
                content = response.choices[0].message.content
                m = _re.search(r'src="([^"]+)"', content)
                if not m:
                    continue
                file_id = m.group(1)
                image = client.get_image(file_id)
                img_b64 = image.content
            fname = f"gen_{int(time.time())}_{random.randint(1000,9999)}.jpg"
            fpath = f"{folder_path}/{fname}"
            with open(fpath, "wb") as imgf:
                imgf.write(_b64.b64decode(img_b64))
            results.append(f"{url_prefix}/{fname}")
        except Exception:
            continue
    return {"images": results, "requested": count, "succeeded": len(results)}

# РЕАНИМАЦИЯ ЗАВИСШИХ ЗАДАЧ при старте: если бэкенд перезапустился, пока задача была
# в статусе running, её воркер умер — задача зависнет навсегда. Возвращаем такие задачи
# в очередь (queued), чтобы живые воркеры их переподхватили. Так ни одна задача не теряется
# при рестарте/обрыве — критично для ночного режима "поставил вечером, сделалось к утру".
def _revive_stuck_tasks():
    """Вызывается РОВНО ОДИН РАЗ при старте процесса (см. вызов сразу после определения),
    до того как какой-либо воркер этого процесса успел взять хоть одну задачу. Поэтому ЛЮБАЯ
    запись со status="running" на этот момент - гарантированно осиротевшая: её поток умер вместе
    с предыдущим процессом (рестарт/креш), не важно, сколько секунд назад она была создана.
    Раньше здесь был фильтр "created_at < 15 минут назад", из-за которого только что созданная
    задача, чей воркер погиб при рестарте В ПЕРВЫЕ секунды выполнения, вообще не считалась
    зависшей и оставалась в "running" навсегда (нашли при параллельном тесте конвейера - рестарт
    backend пришёлся ровно на момент выполнения, 3 задачи осиротели, реанимация их проигнорировала)."""
    db = SessionLocal()
    try:
        stuck = db.query(Task).filter(Task.status == "running").all()
        for t in stuck:
            t.status = "queued"
            print(f"[REVIVE] задача {t.id} ({t.task_type}) возвращена в очередь после зависания")
        if stuck:
            db.commit()
            print(f"[REVIVE] реанимировано зависших задач: {len(stuck)}")
    except Exception as e:
        print(f"[REVIVE] ошибка реанимации: {e}")
    finally:
        db.close()

_revive_stuck_tasks()

# Пул из нескольких параллельных воркеров вместо одного — с SKIP LOCKED
# несколько потоков безопасно разбирают очередь без гонки за одну задачу.
WORKER_POOL_SIZE = int(os.getenv("WORKER_POOL_SIZE", 4))
_worker_threads = []
for _i in range(WORKER_POOL_SIZE):
    _t = threading.Thread(target=_worker_loop, daemon=True, name=f"worker-{_i+1}")
    _t.start()
    _worker_threads.append(_t)


def _run_fetch_all_galleries(payload):
    """Обходит ВСЕ карточки аккаунта по очереди и собирает полную галерею фото каждой (оригинальный размер).
    Защищено общим таймаутом — если операция не укладывается в отведённое время (например, один товар
    завис на сетевом запросе), прерываем цикл и сохраняем то, что успели собрать, вместо зависания навечно."""
    import json as _json, re as _re, time as _time_mod
    from app.api.parser import _fetch_product_gallery, _download_product_photo
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    OVERALL_TIMEOUT_SEC = 720  # 12 минут максимум на всю операцию
    _start_ts = _time_mod.time()

    def _time_left():
        return OVERALL_TIMEOUT_SEC - (_time_mod.time() - _start_ts)

    account_id = payload.get("account_id", "default")
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "parsed_products").first()
        if not row:
            return {"done": 0, "message": "Нет карточек"}
        data = _json.loads(row.value)
        prods = data.get("products", [])
        done_count = 0
        pending_raw = {}

        for idx, prod in enumerate(prods):
            if _time_left() <= 0:
                print(f"[gallery_batch] общий таймаут {OVERALL_TIMEOUT_SEC}с исчерпан, остановка на товаре {idx}/{len(prods)}")
                break
            if prod.get("images") and len(prod["images"]) > 1:
                continue  # уже есть галерея, не тратим время повторно
            if not prod.get("product_url"):
                continue
            try:
                gallery_urls, _auth_price = _fetch_product_gallery(prod["product_url"], max_photos=10)
                if _auth_price:
                    prod["price"] = _auth_price
                # превью-карточка рисуется из prod["image"] — подставляем первое фото галереи
                if gallery_urls and not prod.get("image"):
                    prod["image"] = gallery_urls[0]
                # убираем трансформацию /-/contain/WxH/... чтобы скачать ОРИГИНАЛЬНЫЙ размер, не превью
                original_urls = []
                for u in gallery_urls:
                    orig = _re.sub(r"/-/contain/\d+x\d+/center/center/", "/", u)
                    orig = _re.sub(r"/-/format/webp/", "/", orig)
                    orig = orig.replace(".webp", "")
                    original_urls.append(orig)

                # ВАЖНО: страницы товара на Tilda подмешивают в галерею 1-2 фото из блока
                # "похожие товары" (getrelevantproducts, sort=random) — эти фото повторяются
                # у МНОГИХ разных товаров каталога. Настоящее уникальное фото товара
                # никогда не встретится у другого, не связанного товара.
                # Копим сырые данные в pending_raw, отфильтруем по частоте ПОСЛЕ сбора всех.
                pending_raw[idx] = original_urls
            except Exception as _e:
                print(f"[gallery_batch] ошибка на товаре {idx}: {_e}")
                continue

        # --- Фаза 2: считаем частоту каждого фото (по storage-префиксу Tilda) по ВСЕМ товарам ---
        def _stor_id(u: str):
            m = _re.search(r"(stor[\w-]+)/", u)
            return m.group(1) if m else u

        freq = {}
        for idx, urls in pending_raw.items():
            for u in urls:
                sid = _stor_id(u)
                freq[sid] = freq.get(sid, 0) + 1

        # --- Фаза 3: для каждого товара оставляем только фото, уникальные для него (freq == 1) ---
        for idx, urls in pending_raw.items():
            unique_urls = [u for u in urls if freq.get(_stor_id(u), 0) == 1]
            if not unique_urls:
                # ничего уникального не нашлось (редкий случай) — оставляем как было, лучше что-то чем пусто
                unique_urls = urls[:1]

            downloaded = []
            for g_idx, g_url in enumerate(unique_urls):
                local_path = _download_product_photo(account_id, g_url, f"{idx}_g{g_idx}")
                downloaded.append(local_path)
            if downloaded:
                prods[idx]["images"] = downloaded
                done_count += 1

        data["products"] = prods
        row.value = _json.dumps(data, ensure_ascii=False)
        db.commit()

        return {"done": done_count, "total": len(prods)}
    finally:
        db.close()


def _run_product_description(payload):
    """Генерирует продающее описание товара и сохраняет обратно в parsed_products."""
    import json as _json
    from app.api.parser import _generate_product_description
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    account_id = payload.get("account_id", "default")
    product_idx = payload.get("product_idx")
    title = payload.get("title", "")
    price = payload.get("price", "")
    characteristics = payload.get("characteristics", {})

    description = _generate_product_description(title, price, characteristics)

    if product_idx is not None:
        db = SessionLocal()
        try:
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "parsed_products").first()
            if row:
                data = _json.loads(row.value)
                prods = data.get("products", [])
                if 0 <= product_idx < len(prods):
                    prods[product_idx]["ai_description"] = description
                    data["products"] = prods
                    row.value = _json.dumps(data, ensure_ascii=False)
                    db.commit()
        finally:
            db.close()

    return {"description": description, "product_idx": product_idx}


def _run_enrich_parsed_descriptions(payload, task=None, db=None):
    """Обходит выгруженные с сайта карточки (все или выбранные indices) и генерирует
    продающее ИИ-описание с уникализацией (спинтакс) для тех, у кого его ещё нет -
    те же правила, что и для одиночной карточки (_run_product_description), но пачкой.

    Пишет живой прогресс в task.result (как _run_city_analysis), ретраит каждую карточку
    до 3 раз с экспоненциальной паузой перед тем как пропустить её как неудачную, а при
    исчерпании OVERALL_TIMEOUT_SEC не бросает партию - ставит задачу-продолжение с оставшимися
    индексами (воркер подхватит её сам, клиенту не нужно нажимать кнопку заново). По реальному
    завершению всей цепочки (не отдельного сегмента) шлёт уведомление владельцу через
    _push_notification - раньше клиент видел "2 из 196" и не понимал, что случилось и что делать."""
    import json as _json, time as _time_mod
    from app.api.parser import _generate_product_description
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.models.task import Task as TaskModel

    OVERALL_TIMEOUT_SEC = 2400  # 40 минут максимум на один сегмент партии
    _start_ts = _time_mod.time()

    account_id = payload.get("account_id", "default")
    indices = payload.get("indices")  # None = все карточки без готового ai_description
    force = bool(payload.get("force", False))
    sample = (payload.get("sample") or "").strip()
    failed_so_far = list(payload.get("failed_so_far") or [])

    own_db = db is None
    _db = db if db is not None else SessionLocal()
    try:
        row = _db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "parsed_products").first()
        if not row:
            return {"done": 0, "message": "Нет карточек"}
        data = _json.loads(row.value)
        prods = data.get("products", [])
        targets = indices if indices is not None else list(range(len(prods)))
        original_total = payload.get("original_total") or len(targets)

        def _write_progress(done_count, current_item=None, finished=False):
            if task is not None:
                task.result = _json.dumps({
                    "total": original_total, "done": done_count, "failed": len(failed_so_far),
                    "finished": finished, "current_item": current_item,
                }, ensure_ascii=False)
                _db.commit()

        done_count = 0
        remaining_after_timeout = None
        for pos, idx in enumerate(targets):
            if _time_mod.time() - _start_ts >= OVERALL_TIMEOUT_SEC:
                remaining_after_timeout = targets[pos:]
                print(f"[enrich_descriptions] общий таймаут {OVERALL_TIMEOUT_SEC}с исчерпан, остановка на {idx}", flush=True)
                break
            if idx < 0 or idx >= len(prods):
                continue
            p = prods[idx]
            if p.get("ai_description") and not force:
                done_count += 1
                continue

            _write_progress(done_count, current_item=p.get("title") or f"#{idx}")

            ok = False
            last_err = None
            for attempt in range(3):
                try:
                    p["ai_description"] = _generate_product_description(
                        p.get("title", ""), p.get("price", ""), p.get("characteristics") or {}, sample=sample
                    )
                    ok = True
                    break
                except Exception as e:
                    last_err = e
                    print(f"[enrich_descriptions] карточка {idx} попытка {attempt+1}/3 не удалась: {e}", flush=True)
                    if attempt < 2:
                        _time_mod.sleep(5 * (2 ** attempt))  # 5с, потом 10с перед следующей попыткой

            if ok:
                done_count += 1
                _time_mod.sleep(4)  # антифлуд: OpenAI отбивает 429 при пачке длинных запросов
            else:
                failed_so_far.append({"index": idx, "title": p.get("title", ""), "reason": str(last_err)[:200]})

            data["products"] = prods
            row.value = _json.dumps(data, ensure_ascii=False)
            _db.commit()

        if remaining_after_timeout:
            continuation_payload = {
                "account_id": account_id, "indices": remaining_after_timeout, "force": force,
                "sample": sample, "failed_so_far": failed_so_far, "original_total": original_total,
            }
            cont_task = TaskModel(account_id=account_id, task_type="enrich_descriptions", status="queued",
                                   payload=_json.dumps(continuation_payload, ensure_ascii=False))
            _db.add(cont_task)
            _db.commit()
            _write_progress(done_count, finished=False)
            return {
                "done": done_count, "total": original_total, "failed": len(failed_so_far),
                "finished": False, "continued_as_task_id": cont_task.id,
            }

        _write_progress(done_count, finished=True)

        if failed_so_far:
            titles = ", ".join(f"«{f['title']}»" for f in failed_so_far[:10] if f.get("title"))
            more = " и другие" if len(failed_so_far) > 10 else ""
            text = (f"✅ Обогащено {done_count} из {original_total} товаров. "
                    f"{len(failed_so_far)} пропущено: {titles}{more}. Причина: ошибка ИИ после 3 попыток.")
        else:
            text = f"✅ Обогащено {done_count} из {original_total} товаров. 0 ошибок."
        _push_notification(_db, account_id, text)

        return {"done": done_count, "total": original_total, "failed": len(failed_so_far), "finished": True}
    finally:
        if own_db:
            _db.close()


def _run_product_banner(payload):
    """Генерирует баннер по товару из карточки и сохраняет URL обратно в parsed_products."""
    import json as _json
    from app.api.banners import create_full_ai_banner, FullAiRequest
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    account_id = payload.get("account_id", "default")
    product_idx = payload.get("product_idx")
    title = payload.get("title", "")
    price = payload.get("price", "")
    characteristics = payload.get("characteristics", {})
    image = payload.get("image", "")

    # формируем описание товара для баннера
    chars_text = ", ".join([f"{k}: {v}" for k, v in list(characteristics.items())[:5]]) if characteristics else ""
    desc = f"{title}. Цена: {price}. {chars_text}".strip()

    req = FullAiRequest(
        account_id=account_id,
        raw_description=desc,
        format="infographic",
        reference_image_urls=[image] if image else []
    )
    res = create_full_ai_banner(req)
    banner_url = res.get("url") or (res.get("urls", [None])[0] if res.get("urls") else None)

    # сохраняем banner_url обратно в товар
    if banner_url and product_idx is not None:
        db = SessionLocal()
        try:
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "parsed_products").first()
            if row:
                data = _json.loads(row.value)
                prods = data.get("products", [])
                if 0 <= product_idx < len(prods):
                    prods[product_idx]["banner_url"] = banner_url
                    data["products"] = prods
                    row.value = _json.dumps(data, ensure_ascii=False)
                    db.commit()
        finally:
            db.close()

    return {"banner_url": banner_url, "product_idx": product_idx}


def _run_parse_site(payload, task=None, db=None):
    """Выгрузка карточек с сайта через очередь задач. Принимает либо одну ссылку (url),
    либо список ссылок (urls) - обрабатывает их по очереди и складывает карточки в ОДИН
    общий список (дедуп по title), а не перезаписывает результат каждой ссылкой по отдельности.
    Пишет прогресс в task.result после КАЖДОЙ ссылки (ровно как _run_city_analysis) - на каталоге
    из нескольких десятков товарных ссылок парсер часто ходит headed-браузером на каждую страницу
    (когда статический разбор не находит карточек) и без прогресса это выглядит как зависание."""
    import json as _json
    from app.api.parser import parse_site, ParseRequest, _save_parsed_products, _parse_via_generic
    urls = [u.strip() for u in payload.get("urls", []) if u and u.strip()]
    if not urls and payload.get("url"):
        urls = [payload["url"].strip()]
    account_id = payload.get("account_id", "default")
    total = len(urls)

    all_products = []
    seen_titles = set()
    errors = []

    def _write_progress(done, current_url, finished=False):
        if task is not None and db is not None:
            task.result = _json.dumps({
                "url": current_url, "total": total, "done": done,
                "finished": finished, "results": all_products[:200],
            }, ensure_ascii=False)
            db.commit()

    for idx, url in enumerate(urls):
        try:
            res = parse_site(ParseRequest(url=url, account_id=account_id, limit=payload.get("limit", 0)))

            # ФОЛБЭК: сайт вне Tilda/Schema.org (старый табличный каталог, битый TLS)
            _prods = (res or {}).get("products") or []
            if not _prods:
                try:
                    _gp = _parse_via_generic(url)
                    if isinstance(_gp, list):
                        _gp = [x for x in _gp if x and x.get('title')]
                        if _gp:
                            res = {'count': len(_gp), 'products': _gp}
                            print('[parse_site] generic-katalog: tovarov', len(_gp))
                    elif _gp and _gp.get('title'):
                        res = {'count': 1, 'products': [_gp]}
                        print('[parse_site] generic-tovar:', _gp['title'][:60])
                except Exception as _ge:
                    print(f"[parse_site] generic-фолбэк упал: {_ge}")
            if res.get("error"):
                errors.append(f"{url}: {res['error']}")
            else:
                for p in res.get("products", []):
                    if p.get("title") not in seen_titles:
                        seen_titles.add(p.get("title"))
                        all_products.append(p)
        except Exception as e:
            errors.append(f"{url}: {str(e)[:200]}")
        _write_progress(idx + 1, url)

    _save_parsed_products(account_id, urls, all_products[:200])
    _write_progress(total, urls[-1] if urls else "", finished=True)
    return {
        "url": urls[-1] if urls else "", "total": total, "done": total, "finished": True,
        "results": all_products[:200], "found": len(all_products), "urls": urls,
        "saved": True, "errors": errors,
    }


def _run_city_analysis(payload, task=None, db=None):
    """Фоновый парсинг конкурентов по городам с записью прогресса в task.result."""
    import asyncio
    import json as _json
    from app.api.parser import (
        _fetch_avito_page_headed, _extract_listing_items,
        _fetch_item_detail_headed, _summarize_advantages,
        _normalize_city, _save_city_analysis, _load_city_analysis_if_fresh,
    )

    query = payload.get("query", "")
    cities = payload.get("cities", [])
    total = len(cities)

    def _write_progress(done, results, finished=False):
        if task is not None and db is not None:
            task.result = _json.dumps({
                "query": query, "total": total, "done": done,
                "finished": finished, "results": results,
            }, ensure_ascii=False)
            db.commit()

    async def run():
        results = []
        for idx, city in enumerate(cities):
            cached = _load_city_analysis_if_fresh(query, city)
            if cached:
                cached["from_cache"] = True
                results.append(cached)
                _write_progress(idx + 1, results)
                continue
            try:
                search_query = query.replace(" ", "+")
                city_slug = _normalize_city(city)
                url = f"https://www.avito.ru/{city_slug}?q={search_query}"

                items = []
                for _list_attempt in range(4):
                    try:
                        _page_data = await _fetch_avito_page_headed(url)
                        html = _page_data["html"]
                    except Exception:
                        continue
                    if "firewallCaptcha" in html or "geetest_captcha" in html:
                        continue
                    items = _extract_listing_items(html)
                    if items:
                        break

                raw_prices = [it["price"] for it in items if it["price"]]
                # Фильтр выбросов: отбрасываем цены, отклоняющиеся от медианы более чем в 20 раз
                # (склеенный мусор вроде 6237729 руб. для тротуарной плитки)
                prices = raw_prices
                if len(raw_prices) >= 3:
                    srt = sorted(raw_prices)
                    median = srt[len(srt) // 2]
                    if median > 0:
                        prices = [p for p in raw_prices if median / 20 <= p <= median * 20]
                    if not prices:
                        prices = raw_prices
                top5_base = sorted([it for it in items if it["price"]], key=lambda x: x["price"])[:5]

                top5 = []
                for it in top5_base:
                    detail = await _fetch_item_detail_headed(it["url"]) if it["url"] else {}
                    advantages = _summarize_advantages(it["title"], detail.get("description"))
                    top5.append({
                        "title": it["title"], "price": it["price"], "url": it["url"],
                        "photos_count": detail.get("images_count"), "advantages": advantages,
                    })

                city_result = {
                    "city": city, "query": query, "count_found": len(items),
                    "min_price": min(prices) if prices else None,
                    "max_price": max(prices) if prices else None,
                    "avg_price": sum(prices) // len(prices) if prices else None,
                    "top5": top5, "from_cache": False,
                }
                _save_city_analysis(query, city, city_result)
                results.append(city_result)
            except Exception as e:
                results.append({"city": city, "error": str(e)[:200]})

            _write_progress(idx + 1, results)

        _write_progress(total, results, finished=True)
        return {"query": query, "total": total, "done": total, "finished": True, "results": results}

    return asyncio.run(run())


def _push_notification(db, account_id: str, text: str, related_results: list = None) -> dict:
    """Кладёт запись в Storage 'notifications' (список, последние 50) под account_id - чтобы чат
    Бориса на фронте мог показать её бейджем/карточкой при следующем открытии (поллинг там уже
    есть, GET /api/chat/notifications). Общий хелпер - раньше эта логика была только внутри
    _run_report_back, теперь её же используют и длинные фоновые задачи (enrich_descriptions)."""
    from app.models.storage import Storage
    import json as _json
    from datetime import datetime

    report = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "text": text,
        "related_results": related_results or [],
        "read": False,
    }

    key = "notifications"
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
    notifications = []
    if row:
        try:
            notifications = _json.loads(row.value)
        except Exception:
            notifications = []
    notifications.append(report)
    notifications = notifications[-50:]
    raw = _json.dumps(notifications, ensure_ascii=False)
    if row:
        row.value = raw
    else:
        row = Storage(account_id=account_id, key=key, value=raw)
        db.add(row)
    db.commit()
    return report


def _run_report_back(payload):
    """Собирает результаты связанных задач (например А/Б-теста) и формирует отчёт.
    Отчёт сохраняется и в task.result (обычным образом), и в отдельное хранилище
    'notifications' под account_id — чтобы чат Бориса мог показать его при следующем открытии."""
    from app.db.session import SessionLocal
    from app.models.task import Task as TaskModel
    import json as _json

    account_id = payload.get("account_id", "otdushi")
    task_ids = payload.get("task_ids", [])
    note = payload.get("note", "")

    db = SessionLocal()
    try:
        related_results = []
        for tid in task_ids:
            t = db.query(TaskModel).filter(TaskModel.id == tid).first()
            if t and t.result:
                try:
                    related_results.append({"task_id": tid, "type": t.task_type, "result": _json.loads(t.result)})
                except Exception:
                    related_results.append({"task_id": tid, "type": t.task_type, "result": t.result})

        report_text = note or "Отчёт по поставленной задаче готов."
        report = _push_notification(db, account_id, report_text, related_results)
        return {"status": "ok", "report": report}
    finally:
        db.close()


def _run_ab_test(payload):
    """Сравнивает несколько вариантов объявлений (А/Б-тест) по накопленной статистике daily_stats
    за период между start_date и сегодня. Обогащает содержимым (текст/фото/цена) и получает
    мнение Бориса через GigaChat о том, почему один вариант сработал лучше другого."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import date as _date, timedelta as _timedelta

    account_id = payload.get("account_id", "otdushi")
    variants = payload.get("variants", [])  # [{"label": "Шаблон 1", "item_ids": [...]}, ...]
    start_date = payload.get("start_date")

    if not start_date:
        start_date = _date.today().isoformat()

    start = _date.fromisoformat(start_date)
    today = _date.today()
    date_range = []
    d = start
    while d <= today:
        date_range.append(d.isoformat())
        d += _timedelta(days=1)

    db = SessionLocal()
    try:
        # Статистика по дням
        item_totals = {}
        for date_str in date_range:
            key = f"daily_stats:{date_str}"
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
            if not row:
                continue
            try:
                snapshot = _json.loads(row.value)
            except Exception:
                continue
            for it in snapshot.get("items", []):
                iid = it.get("id")
                if iid not in item_totals:
                    item_totals[iid] = {"views": 0, "contacts": 0}
                item_totals[iid]["views"] += it.get("views", 0)
                item_totals[iid]["contacts"] += it.get("contacts", 0)

        # Содержимое объявлений (город/текст/фото) — парсим напрямую с Avito по item_id,
        # работает для ЛЮБОГО объявления, не только опубликованных через Бориса
        import asyncio
        from app.api.parser import _fetch_item_full_info, city_analysis as _city_analysis_endpoint, CityAnalysisRequest

        async def _gather_all():
            variant_results = []
            for v in variants:
                label = v.get("label", "?")
                ids = v.get("item_ids", [])
                total_views = sum(item_totals.get(i, {}).get("views", 0) for i in ids)
                total_contacts = sum(item_totals.get(i, {}).get("contacts", 0) for i in ids)
                conversion = round(total_contacts / total_views * 100, 1) if total_views > 0 else 0

                content_samples = []
                variant_city = None
                for i in ids:
                    info = await _fetch_item_full_info(i)
                    if info and not info.get("error"):
                        content_samples.append({
                            "title": info.get("title"),
                            "description": (info.get("description") or "")[:300],
                            "city": info.get("city"),
                            "photos_count": info.get("photos_count"),
                        })
                        if not variant_city and info.get("city"):
                            variant_city = info.get("city")

                # Конкуренция в городе варианта (опционально, если удалось определить город и тему)
                competitors_count = None
                if variant_city and content_samples and content_samples[0].get("title"):
                    try:
                        query_guess = content_samples[0]["title"]
                        city_req = CityAnalysisRequest(query=query_guess, cities=[variant_city])
                        # city_analysis синхронная и сама вызывает asyncio.run() внутри —
                        # нельзя вызвать её напрямую из уже работающего event loop,
                        # поэтому выполняем в отдельном потоке через to_thread
                        analysis = await asyncio.to_thread(_city_analysis_endpoint, city_req)
                        for r in analysis.get("results", []):
                            if r.get("count_found") is not None:
                                competitors_count = r["count_found"]
                                break
                    except Exception as e:
                        competitors_count = None
                        print(f"CITY_ANALYSIS_ERROR: {repr(e)}")

                variant_results.append({
                    "label": label, "item_ids": ids,
                    "views": total_views, "contacts": total_contacts, "conversion": conversion,
                    "city": variant_city,
                    "competitors_in_city": competitors_count,
                    "content_samples": content_samples,
                })
            return variant_results

        variant_results = asyncio.run(_gather_all())

        with_data = [v for v in variant_results if v["views"] > 0]
        winner = max(with_data, key=lambda v: v["conversion"]) if with_data else None

        summary_lines = [f"{v['label']}: {v['views']} просмотров, {v['contacts']} контактов, конверсия {v['conversion']}%" for v in variant_results]
        summary = "Результаты А/Б-теста:\n" + "\n".join(summary_lines)
        if winner:
            summary += f"\n\nПобедитель: {winner['label']} (конверсия {winner['conversion']}%)"

        # Мнение Бориса через GigaChat: почему один вариант сработал лучше, с учётом содержания
        boris_opinion = None
        if with_data and len(with_data) >= 1:
            try:
                from gigachat_pool import chat_with_fallback
                from gigachat.models import Messages, MessagesRole
                from app.api.chat import GIGACHAT_KEY

                variants_desc = []
                for v in variant_results:
                    samples_text = "; ".join(
                        f"«{s['title']}» фото {s['photos_count']}, описание: {s['description'][:150]}"
                        for s in v["content_samples"]
                    ) or "содержимое недоступно"
                    city_info = f"город {v['city']}" if v.get("city") else "город неизвестен"
                    comp_info = f", конкурентов в городе по этой теме: {v['competitors_in_city']}" if v.get("competitors_in_city") is not None else ""
                    variants_desc.append(
                        f"{v['label']}: просмотров {v['views']}, контактов {v['contacts']}, конверсия {v['conversion']}%, "
                        f"{city_info}{comp_info}. Объявления: {samples_text}"
                    )

                prompt = (
                    "Ты анализируешь результаты А/Б-теста объявлений на Авито. Вот данные по вариантам:\n\n"
                    + "\n\n".join(variants_desc)
                    + "\n\nВ 2-4 предложениях объясни, какой вариант сработал лучше и, самое главное, "
                    "ПОЧЕМУ — учитывая текст, фото, ГОРОД и уровень конкуренции в нём (например, победа в городе "
                    "с высокой конкуренцией показательнее, чем в городе с низкой). "
                    "Дай конкретный практический вывод для следующих объявлений."
                )
                boris_opinion = chat_with_fallback(
                    [Messages(role=MessagesRole.USER, content=prompt)],
                    model=_GC_MODEL, credentials=GIGACHAT_KEY
                ).strip()
            except Exception as e:
                boris_opinion = None

        if boris_opinion:
            summary += f"\n\n🤖 Мнение Бориса: {boris_opinion}"

        return {
            "status": "ok",
            "period": {"from": start_date, "to": today.isoformat()},
            "variants": variant_results,
            "winner": winner["label"] if winner else None,
            "boris_opinion": boris_opinion,
            "summary": summary,
        }
    finally:
        db.close()


def _run_avito_categories_sync(payload, task=None, db=None):
    """Полный обход дерева категорий Avito + параметров каждого шаблона.
    Верхний уровень и список template_id — обычный requests (без браузера).
    Параметры конкретного шаблона — headed-Chromium (JS SPA, но без антибота/прокси).
    """
    import asyncio as _asyncio
    import json as _json
    from app.api.avito_categories import (
        fetch_top_level_categories, fetch_subcategory_template_ids,
        fetch_template_params_headed,
    )
    from app.models.avito_category import AvitoCategory, AvitoCategoryParam

    def _write_progress(done, total, current_label, finished=False):
        if task is not None and db is not None:
            task.result = _json.dumps({
                "done": done, "total": total, "current": current_label,
                "finished": finished,
            }, ensure_ascii=False)
            db.commit()

    top_categories = fetch_top_level_categories()

    # Собираем полный список (category, template) пар сначала — чтобы знать total для прогресса
    all_pairs = []
    for cat in top_categories:
        templates = fetch_subcategory_template_ids(cat["slug"])
        for t in templates:
            all_pairs.append({"category": cat, "template": t})

    total = len(all_pairs)
    _write_progress(0, total, "старт")

    async def run_all():
        done = 0
        for pair in all_pairs:
            cat = pair["category"]
            tmpl = pair["template"]
            label = f"{cat['name']} / {tmpl['label']}"
            try:
                params = await fetch_template_params_headed(tmpl["template_id"])
            except Exception as e:
                params = []

            # Сохраняем категорию (upsert по template_id)
            existing = db.query(AvitoCategory).filter(
                AvitoCategory.template_id == tmpl["template_id"]
            ).first()
            if existing:
                existing.name = cat["name"]
                existing.slug = cat["slug"]
                existing.template_label = tmpl["label"]
            else:
                existing = AvitoCategory(
                    slug=cat["slug"], name=cat["name"],
                    template_id=tmpl["template_id"], template_label=tmpl["label"],
                )
                db.add(existing)
            db.commit()

            # Перезаписываем параметры этого template_id
            db.query(AvitoCategoryParam).filter(
                AvitoCategoryParam.template_id == tmpl["template_id"]
            ).delete()
            for p in params:
                db.add(AvitoCategoryParam(
                    template_id=tmpl["template_id"],
                    name=p["name"], required=p["required"], description=p["description"],
                ))
            db.commit()

            done += 1
            _write_progress(done, total, label)

    _asyncio.run(run_all())
    _write_progress(total, total, "готово", finished=True)
    return {"total": total, "status": "ok"}


def _run_execute_plan_subtask(payload):
    """Выполняет одну подзадачу PlanItem (созданную авторазбивкой) через фоновый воркер,
    вместо синхронного блокирующего запроса внутри HTTP-ответа кнопки.
    Если сам запрос не долетел содержательно (перегрузка пула БД, разрыв
    соединения при рестарте, не-200 статус) - НЕ считаем это финальной ошибкой
    подзадачи, а поднимаем исключение, чтобы верхний уровень очереди переставил
    задачу обратно в queued для повтора вместо безвозвратной потери."""
    import requests as _requests
    plan_item_id = payload.get("plan_item_id")
    resp = _requests.post(f"http://127.0.0.1:8000/api/plan_items/{plan_item_id}/execute", timeout=900)
    if resp.status_code != 200:
        raise Exception(f"plan_items/execute вернул {resp.status_code}: {resp.text[:200]} - ставим в очередь на повтор")
    try:
        return resp.json()
    except Exception as _e:
        raise Exception(f"plan_items/execute вернул нечитаемый ответ (возможно, обрыв/перегрузка БД): {_e} - ставим в очередь на повтор")


# ==================== КОНВЕЙЕР НАПРАВЛЕНИЯ ====================
# Явная форма с конкретными параметрами (НЕ свободный промт-оркестратор - см. обсуждение).
# 3 связанные задачи (depends_on_task_id): pipeline_texts -> pipeline_banners -> pipeline_finalize.
# Каждая пишет живой прогресс в task.result вида {stage, stage_label, done, total, finished,
# stopped, reason, next_task_id} - как в _run_enrich_parsed_descriptions.

_TAG_TO_SNAKE_PIPELINE = {
    "ServiceType": "service_type", "ServiceSubtype": "service_subtype",
    "WorkExperience": "work_experience", "Guarantee": "guarantee",
    "GoodsType": "goods_type", "GoodsSubType": "goods_subtype",
    "Condition": "condition", "KitchenType": "kitchen_type",
    "PriceType": "price_type", "Color": "color", "AdType": "ad_type",
}


def _pipeline_resolve_spintax(text: str, randomize: bool) -> str:
    """Раскрывает спинтакс {а|б|в} - рандомно (randomize=True) или детерминированно первым
    вариантом (если владелец снял галочку "уникализировать"). Скобки резолвятся ВСЕГДА -
    оставлять {а|б} в тексте нельзя ни при каком раскладе (см. инцидент со спинтаксом в фиде)."""
    from app.api.avito import spin
    if not text:
        return text
    if randomize:
        return spin(text)
    import re as _re
    prev = None
    result = text
    while prev != result:
        prev = result
        result = _re.sub(r"\{([^{}]*)\}", lambda m: m.group(1).split("|")[0], result)
    return result


def _pipeline_percent(step: int, done: int, total: int) -> int:
    """5 именованных шагов конвейера (категория, тексты, баннеры, проверка, публикация) -
    процент для прогресс-бара считается по номеру текущего шага + доле выполнения внутри него."""
    frac = (done / total) if total else 1.0
    return max(0, min(100, round(((step - 1) + frac) / 5 * 100)))


def _run_pipeline_texts(payload, task=None, db=None):
    """Шаги 1-2 из 5 (категория, тексты) конвейера направления: определяет категорию +
    обязательные поля, генерирует N текстов объявлений (пачками по 12, та же защита от обрезки
    JSON, что в create_draft_listings), уникализирует спинтаксом, сохраняет как черновики одной
    партией. При needs_clarification (категория требует уточнения от владельца) - задача уходит
    в needs_confirmation и ждёт решения владельца (см. POST /api/tasks/{id}/confirm), а не рвёт
    конвейер безвозвратно."""
    import requests as _requests
    from app.api.avito import spin, _load_drafts, _save_drafts

    account_id = payload["account_id"]
    direction = payload["direction"]
    count = int(payload.get("count", 10))
    banner_count = int(payload.get("banner_count", 5))
    uniquify_titles = bool(payload.get("uniquify_titles", True))
    uniquify_descriptions = bool(payload.get("uniquify_descriptions", True))
    price_from = int(payload.get("price_from", 5000))
    price_to = int(payload.get("price_to", 50000))
    force_confirm = bool(payload.get("force_confirm", False))
    root_task_id = payload.get("root_task_id") or (task.id if task else None)
    batch_id = payload.get("batch_id")
    batch_label = f"Конвейер: {direction}"

    def _write_progress(step, done, total, label, finished=False, next_task_id=None):
        progress = {
            "root_task_id": root_task_id, "direction": direction, "batch_id": batch_id,
            "step": step, "total_steps": 5, "step_label": label,
            "percent": _pipeline_percent(step, done, total),
            "done": done, "total": total, "finished": finished, "next_task_id": next_task_id,
        }
        if task is not None and db is not None:
            task.result = json.dumps(progress, ensure_ascii=False)
            db.commit()
        return progress

    _write_progress(1, 0, count, "Шаг 1 из 5: определяю категорию")

    cat_data = {}
    for _attempt in range(1, 4):
        try:
            r = _requests.post("http://127.0.0.1:8000/api/avito/detect_category",
                                json={"account_id": account_id, "niche": direction}, timeout=60)
            cat_data = r.json()
            if cat_data.get("status") == "ok":
                break
        except Exception as e:
            print(f"[pipeline_texts] detect_category попытка {_attempt}: {e}", flush=True)
        if _attempt < 3:
            time.sleep(2)

    if cat_data.get("status") != "ok":
        try:
            from app.services.category_resolver import resolve_category_for_account
            fb = resolve_category_for_account(account_id, direction)
            if fb.get("category_name"):
                cat_data = {"status": "ok", "category": fb["category_name"]}
        except Exception as e:
            print(f"[pipeline_texts] resolve_category_for_account упал: {e}", flush=True)

    if cat_data.get("status") != "ok":
        reason = f"Не удалось определить категорию Avito для направления «{direction}»"
        _write_progress(1, 0, count, reason, finished=True)
        _push_notification(db, account_id, f"⛔ Конвейер «{direction}» остановлен: {reason}")
        return {"status": "stopped", "reason": reason}

    category = cat_data["category"]

    # Ответ владельца/сотрудника на предыдущий вопрос конвейера (см. POST /{id}/confirm) - передаём
    # его И в резолв обязательных полей категории (chars-контекст для pick_value_via_gpt - должен
    # реально снять неоднозначность вместо повторного вопроса/дефолта), И в промт генерации текстов
    # ниже (extra), чтобы собственное производство/цена за штуку/цвет и т.п. отразились в текстах.
    user_answer = (payload.get("user_answer") or "").strip()
    _answer_characteristics = {"Уточнение владельца": user_answer} if user_answer else None

    from app.services.category_resolver import resolve_required_fields
    req_fields = resolve_required_fields(category_id=direction, niche=direction, api_category=category,
                                          characteristics=_answer_characteristics)
    if req_fields.get("status") != "ok":
        reason = f"Не удалось получить обязательные поля категории: {req_fields.get('message', 'нет причины')}"
        _write_progress(1, 0, count, reason, finished=True)
        _push_notification(db, account_id, f"⛔ Конвейер «{direction}» остановлен: {reason}")
        return {"status": "stopped", "reason": reason}
    if req_fields.get("needs_clarification") and not force_confirm:
        questions = "; ".join(q["question"] for q in req_fields["needs_clarification"])
        question_text = f"Категория «{category}» требует уточнений: {questions}"
        progress = _write_progress(1, 0, count, question_text, finished=True)
        return {**progress, "status": "needs_confirmation", "question": question_text}

    draft_extra_fields = {}
    draft_extra_params = {}
    for tag, val in req_fields["resolved"].items():
        snake = _TAG_TO_SNAKE_PIPELINE.get(tag)
        if snake:
            draft_extra_fields[snake] = val
        else:
            draft_extra_params[tag] = val

    # Генерация текстов пачками по 12 (см. create_draft_listings - один большой запрос на
    # генерацию обрезается по лимиту токенов GPT и теряет объявления молча)
    ads = []
    chunk_size = 12
    remaining = count
    stall_count = 0
    while remaining > 0:
        this_chunk = min(chunk_size, remaining)
        got = 0
        for _attempt in range(1, 4):
            try:
                r = _requests.post("http://127.0.0.1:8000/api/avito/generate_ads", json={
                    "topic": direction, "count": this_chunk,
                    "price_from": price_from, "price_to": price_to,
                    "extra": f"Владелец уточнил: {user_answer}" if user_answer else "",
                    "goal": "message", "length": "medium",
                    "use_my_ads": False, "account_id": account_id,
                }, timeout=400)
                chunk_ads = r.json().get("ads", [])
                if chunk_ads:
                    ads.extend(chunk_ads)
                    got = len(chunk_ads)
                    break
            except Exception as e:
                print(f"[pipeline_texts] generate_ads попытка {_attempt}: {e}", flush=True)
        if got == 0:
            stall_count += 1
            if stall_count >= 3:
                print(f"[pipeline_texts] застряло на {remaining} из {count} - продолжаю с тем, что получено ({len(ads)})", flush=True)
                break
            continue
        stall_count = 0
        remaining -= got
        _write_progress(2, len(ads), count, f"Шаг 2 из 5: генерирую тексты ({len(ads)} из {count})")

    new_drafts = []
    ts = int(time.time())
    for i, ad in enumerate(ads):
        title = _pipeline_resolve_spintax(ad.get("title", ""), uniquify_titles)[:100]
        description = _pipeline_resolve_spintax(ad.get("description", ""), uniquify_descriptions)
        new_drafts.append({
            "id": f"pipeline-{task.id if task else ts}-{i}-{ts}",
            "batch_id": f"pipeline-{task.id if task else ts}",
            "batch_label": batch_label,
            "variant": "pipeline",
            "title": title,
            "description": description,
            "price": ad.get("price") or random.randint(price_from, price_to),
            "category": category,
            "category_id": direction,
            **draft_extra_fields,
            "address": "Москва",
            "images": [],
            "params": dict(draft_extra_params),
            "created_at": __import__("datetime").datetime.utcnow().isoformat(),
        })

    existing_drafts = _load_drafts(account_id)
    _save_drafts(account_id, existing_drafts + new_drafts)

    next_payload = {
        "account_id": account_id, "direction": direction, "batch_label": batch_label,
        "requested_count": count, "requested_banner_count": banner_count,
        "auto_check": payload.get("auto_check", True), "auto_publish": payload.get("auto_publish", True),
        "root_task_id": root_task_id, "batch_id": batch_id,
    }
    next_task = Task(account_id=account_id, task_type="pipeline_banners", status="queued",
                      depends_on_task_id=task.id if task else None,
                      payload=json.dumps(next_payload, ensure_ascii=False))
    db.add(next_task)
    db.commit()
    db.refresh(next_task)

    _write_progress(2, len(new_drafts), count, f"Шаг 2 из 5 завершён: создано {len(new_drafts)} черновиков",
                     finished=True, next_task_id=next_task.id)
    return {"status": "ok", "drafts_created": len(new_drafts), "requested": count, "next_task_id": next_task.id}


def _run_pipeline_banners(payload, task=None, db=None):
    """Этапы 3-4 конвейера: генерирует M баннеров через apply_banner_to_batch_impl (баннер
    первым/единственным фото каждого черновика партии - без отдельной папки обычных фото,
    форма конвейера её не запрашивает) и ставит следующую задачу-финализацию."""
    from app.api.avito import apply_banner_to_batch_impl

    account_id = payload["account_id"]
    direction = payload["direction"]
    batch_label = payload["batch_label"]
    banner_count = int(payload.get("requested_banner_count", 5))
    root_task_id = payload.get("root_task_id") or (task.id if task else None)
    batch_id = payload.get("batch_id")

    def _write_progress(done, total, label, finished=False, next_task_id=None):
        if task is not None and db is not None:
            task.result = json.dumps({
                "root_task_id": root_task_id, "direction": direction, "batch_id": batch_id,
                "step": 3, "total_steps": 5, "step_label": label,
                "percent": _pipeline_percent(3, done, total),
                "done": done, "total": total, "finished": finished, "next_task_id": next_task_id,
            }, ensure_ascii=False)
            db.commit()

    _write_progress(0, banner_count, f"Шаг 3 из 5: генерирую баннеры (0 из {banner_count})")

    def _on_progress(done, total):
        _write_progress(done, total, f"Шаг 3 из 5: генерирую баннеры ({done} из {total})")

    result = apply_banner_to_batch_impl(account_id, batch_label, banner_count=banner_count,
                                         photos_per_ad=1, folder="", on_progress=_on_progress)

    banners_generated = result.get("banners_generated", 0) if result.get("status") == "ok" else 0

    next_payload = {
        "account_id": account_id, "direction": direction, "batch_label": batch_label,
        "requested_count": payload.get("requested_count"), "requested_banner_count": banner_count,
        "banners_generated": banners_generated,
        "auto_check": payload.get("auto_check", True), "auto_publish": payload.get("auto_publish", True),
        "root_task_id": root_task_id, "batch_id": batch_id,
    }
    next_task = Task(account_id=account_id, task_type="pipeline_finalize", status="queued",
                      depends_on_task_id=task.id if task else None,
                      payload=json.dumps(next_payload, ensure_ascii=False))
    db.add(next_task)
    db.commit()
    db.refresh(next_task)

    _write_progress(banner_count, banner_count,
                     f"Шаг 3 из 5 завершён: {banners_generated} баннеров привязано к партии",
                     finished=True, next_task_id=next_task.id)
    result["next_task_id"] = next_task.id
    return result


def _validate_batch_before_publish(account_id, batch_label, requested_count, requested_banner_count, banners_generated):
    """Этап 5: стоп-условия перед публикацией. Возвращает {ok, problems, draft_ids}."""
    from app.api.avito import _load_drafts
    import re as _re

    drafts = [d for d in _load_drafts(account_id) if d.get("batch_label") == batch_label]
    problems = []

    if len(drafts) < requested_count:
        problems.append(f"Сгенерировано объявлений меньше запрошенного: {len(drafts)} из {requested_count}")
    if banners_generated < requested_banner_count:
        problems.append(f"Сгенерировано баннеров меньше запрошенного: {banners_generated} из {requested_banner_count}")

    junk_patterns = [
        (r"(?i)вариант[ыа]?\s+назв", "остались служебные пометки вида «Варианты названия:»"),
        (r"[*#]{2,}", "остались markdown-символы (**, ##)"),
        (r"\{\{|\}\}", "остались двойные фигурные скобки {{ }}"),
        (r"\{[^{}]*\|[^{}]*\}", "остался нераскрытый спинтакс {вариант|вариант}"),
    ]

    seen_descriptions = {}
    short_titles = []
    no_photo_titles = []
    junk_items = []
    dup_items = []

    for d in drafts:
        desc = d.get("description") or ""
        title = d.get("title") or "без названия"
        if len(desc) < 200:
            short_titles.append(title)
        if not d.get("images"):
            no_photo_titles.append(title)
        for pattern, label in junk_patterns:
            if _re.search(pattern, desc) or _re.search(pattern, title):
                junk_items.append(f"«{title}» — {label}")
                break
        key = desc.strip()
        if key:
            if key in seen_descriptions:
                dup_items.append(f"«{title}» дублирует «{seen_descriptions[key]}»")
            else:
                seen_descriptions[key] = title

    if short_titles:
        problems.append(f"{len(short_titles)} объявлени(й) с пустым/коротким (<200 симв.) описанием: " + ", ".join(f"«{t}»" for t in short_titles[:5]))
    if no_photo_titles:
        problems.append(f"{len(no_photo_titles)} объявлени(й) без фото: " + ", ".join(f"«{t}»" for t in no_photo_titles[:5]))
    if junk_items:
        problems.append(f"Служебный мусор в тексте: " + "; ".join(junk_items[:5]))
    if dup_items:
        problems.append(f"Дублирующиеся описания: " + "; ".join(dup_items[:5]))

    return {"ok": len(problems) == 0, "problems": problems, "draft_ids": [d["id"] for d in drafts]}


def _run_pipeline_finalize(payload, task=None, db=None):
    """Шаги 4-5 из 5 (проверка, публикация): если проверка партии не прошла - задача уходит в
    needs_confirmation (владелец решает "Опубликовать всё равно"/"Отмена" в карточке истории,
    см. POST /api/tasks/{id}/confirm), а не рвёт конвейер безвозвратно. По "Опубликовать всё
    равно" эта же задача возвращается в очередь с auto_check=False и публикует партию как есть."""
    from app.api.avito import PublishDraftsRequest, _publish_drafts_impl

    account_id = payload["account_id"]
    direction = payload["direction"]
    batch_label = payload["batch_label"]
    requested_count = payload.get("requested_count") or 0
    requested_banner_count = payload.get("requested_banner_count") or 0
    banners_generated = payload.get("banners_generated") or 0
    auto_check = payload.get("auto_check", True)
    auto_publish = payload.get("auto_publish", True)
    root_task_id = payload.get("root_task_id") or (task.id if task else None)
    batch_id = payload.get("batch_id")

    def _write_progress(step, label, done=0, total=0, finished=False):
        progress = {
            "root_task_id": root_task_id, "direction": direction, "batch_id": batch_id,
            "step": step, "total_steps": 5, "step_label": label,
            "percent": _pipeline_percent(step, done, total),
            "done": done, "total": total, "finished": finished,
        }
        if task is not None and db is not None:
            task.result = json.dumps(progress, ensure_ascii=False)
            db.commit()
        return progress

    _write_progress(4, "Шаг 4 из 5: проверяю партию")

    if auto_check:
        check = _validate_batch_before_publish(account_id, batch_label, requested_count, requested_banner_count, banners_generated)
    else:
        from app.api.avito import _load_drafts
        draft_ids = [d["id"] for d in _load_drafts(account_id) if d.get("batch_label") == batch_label]
        check = {"ok": True, "problems": [], "draft_ids": draft_ids}

    if not check["ok"]:
        reason = "; ".join(check["problems"])
        question_text = f"Проверка партии «{batch_label}» не пройдена: {reason}"
        progress = _write_progress(4, question_text, finished=True)
        return {**progress, "status": "needs_confirmation", "question": question_text, "draft_ids": check["draft_ids"]}

    if not auto_publish:
        _write_progress(5, f"Готово: {len(check['draft_ids'])} черновиков прошли проверку, ждут ручной публикации", finished=True)
        _push_notification(
            db, account_id,
            f"✅ Конвейер «{direction}»: {len(check['draft_ids'])} объявлений готовы и прошли проверку. "
            f"Автопубликация была выключена — опубликуйте партию «{batch_label}» вручную во вкладке Объявления."
        )
        return {"status": "ready", "draft_ids": check["draft_ids"]}

    _write_progress(5, "Шаг 5 из 5: публикую")
    pub_req = PublishDraftsRequest(account_id=account_id, draft_ids=check["draft_ids"], confirmed=True)
    pub_result = _publish_drafts_impl(pub_req, db)

    if pub_result.get("status") == "ok":
        _write_progress(5, f"Готово: опубликовано {pub_result.get('published', 0)} объявлений", finished=True)
        _push_notification(db, account_id, f"✅ Конвейер «{direction}» завершён: опубликовано {pub_result.get('published', 0)} объявлений.")
    else:
        reason = pub_result.get("message", "публикация не удалась")
        _write_progress(5, f"Остановлено на публикации: {reason}", finished=True)
        _push_notification(
            db, account_id,
            f"⛔ Конвейер «{direction}»: проверка партии пройдена, но публикация не удалась — {reason}. "
            f"Черновики партии «{batch_label}» остались во вкладке Объявления."
        )
    return {"status": pub_result.get("status"), **pub_result}


class PipelineStartRequest(BaseModel):
    account_id: str
    direction: str
    count: int = 10
    banner_count: int = 5
    uniquify_titles: bool = True
    uniquify_descriptions: bool = True
    auto_check: bool = True
    auto_publish: bool = True
    price_from: int = 5000
    price_to: int = 50000


def _create_pipeline_chain(db, account_id: str, direction: str, count=10, banner_count=5,
                            uniquify_titles=True, uniquify_descriptions=True,
                            auto_check=True, auto_publish=True,
                            price_from=5000, price_to=50000, batch_id=None) -> Task:
    """Создаёт первую задачу цепочки конвейера (pipeline_texts) - остальные создаются
    автоматически по мере выполнения (см. depends_on_task_id). Общий хелпер для одиночного
    запуска (/pipeline/start) и пакетного (/pipeline/start_batch) - логика конвейера ОДНА,
    не дублируется, разница только в том, кто и сколько раз её вызывает."""
    payload = {
        "account_id": account_id, "direction": direction, "count": count,
        "banner_count": banner_count, "uniquify_titles": uniquify_titles,
        "uniquify_descriptions": uniquify_descriptions, "auto_check": auto_check,
        "auto_publish": auto_publish, "price_from": price_from, "price_to": price_to,
        "batch_id": batch_id,
    }
    task = Task(account_id=account_id, task_type="pipeline_texts", status="queued",
                payload=json.dumps(payload, ensure_ascii=False))
    db.add(task)
    db.commit()
    db.refresh(task)
    # root_task_id - чтобы фронт группировал все 3 связанные задачи (тексты/баннеры/финализация)
    # цепочки в ОДНУ карточку истории конвейера
    payload["root_task_id"] = task.id
    task.payload = json.dumps(payload, ensure_ascii=False)
    db.commit()
    return task


@router.post("/pipeline/start")
def start_pipeline(req: PipelineStartRequest):
    """Запускает ОДИН конвейер направления."""
    db = SessionLocal()
    try:
        task = _create_pipeline_chain(
            db, req.account_id, req.direction, req.count, req.banner_count,
            req.uniquify_titles, req.uniquify_descriptions, req.auto_check, req.auto_publish,
            req.price_from, req.price_to,
        )
        return {"status": "ok", "task_id": task.id}
    finally:
        db.close()


# ==================== ПАКЕТНЫЙ ЗАПУСК НАПРАВЛЕНИЙ (один промт -> N конвейеров) ====================

_BATCH_DIRECTION_PARSER_PROMPT = """Ты - модуль планирования внутри Бориса, ИИ-ассистента по Avito.
Владелец бизнеса одним текстом перечисляет НЕСКОЛЬКО товарных направлений, для каждого из которых
нужно запустить отдельный конвейер публикации объявлений. Направления могут разделяться переносом
строки, точкой с запятой или явной нумерацией (1., 2) и т.п.).

Для КАЖДОГО направления разбери:
- direction: короткое название направления (например "Кухни под ключ")
- count: сколько объявлений (если не указано - 10)
- banner_count: сколько баннеров (если не указано - 5)
- price_from, price_to: диапазон цен в рублях (если указано "50-300к" - это 50000 и 300000; если не
  указано вообще - 5000 и 50000)
- uniquify_titles, uniquify_descriptions: true по умолчанию, false только если явно попросили НЕ
  уникализировать

Верни ТОЛЬКО чистый JSON без markdown:
{"directions": [{"direction": "...", "count": N, "banner_count": N, "price_from": N, "price_to": N,
"uniquify_titles": true, "uniquify_descriptions": true}, ...]}

Если текст вообще не похож на список направлений (пустой, один неясный вопрос) - верни {"directions": []}."""


class ParseBatchRequest(BaseModel):
    message: str


@router.post("/pipeline/parse_batch")
def parse_pipeline_batch(req: ParseBatchRequest):
    """Разбирает один свободный промт с несколькими направлениями в список параметров конвейеров.
    НЕ создаёт никаких задач - только превью для подтверждения владельцем (см. ВАЖНО в задаче:
    деньги на баннерах, нельзя запускать вслепую)."""
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole

    try:
        raw = chat_with_fallback(
            [Messages(role=MessagesRole.SYSTEM, content=_BATCH_DIRECTION_PARSER_PROMPT),
             Messages(role=MessagesRole.USER, content=req.message)],
            temperature=0.2, max_tokens=3000,
        ).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        parsed = json.loads(raw)
        directions = parsed.get("directions") or []
    except Exception as e:
        return {"status": "error", "message": f"Не удалось разобрать промт: {str(e)[:200]}"}

    if not directions:
        return {"status": "error", "message": "Не нашёл ни одного направления в тексте - переформулируйте, пожалуйста"}

    clean = []
    for d in directions:
        if not d.get("direction"):
            continue
        clean.append({
            "direction": str(d["direction"])[:200],
            "count": max(int(d.get("count") or 10), 1),
            "banner_count": max(int(d.get("banner_count") or 5), 1),
            "price_from": int(d.get("price_from") or 5000),
            "price_to": int(d.get("price_to") or 50000),
            "uniquify_titles": bool(d.get("uniquify_titles", True)),
            "uniquify_descriptions": bool(d.get("uniquify_descriptions", True)),
        })

    total_ads = sum(d["count"] for d in clean)
    total_banners = sum(d["banner_count"] for d in clean)
    # грубая оценка: баннер ~3.6₽ (та же оценка, что и в остальном коде), генерация текста - копейки;
    # время - конвейеры идут параллельно через пул воркеров, поэтому не суммируем, а делим на пул
    estimated_cost_rub = round(total_banners * 3.6, 1)
    estimated_minutes = max(1, round((len(clean) / max(WORKER_POOL_SIZE, 1)) * 2))

    big_batch = len(clean) > 15 or total_banners > 60

    return {
        "status": "ok",
        "directions": clean,
        "total_directions": len(clean),
        "total_ads": total_ads,
        "total_banners": total_banners,
        "estimated_cost_rub": estimated_cost_rub,
        "estimated_minutes": estimated_minutes,
        "big_batch": big_batch,
        "warning": (f"Большой объём: ~{estimated_cost_rub}₽, ~{estimated_minutes} мин. "
                    f"Подтвердите ещё раз, что это точно нужно.") if big_batch else None,
    }


class BatchDirectionItem(BaseModel):
    direction: str
    count: int = 10
    banner_count: int = 5
    price_from: int = 5000
    price_to: int = 50000
    uniquify_titles: bool = True
    uniquify_descriptions: bool = True


class StartBatchRequest(BaseModel):
    account_id: str
    directions: list[BatchDirectionItem]
    auto_check: bool = True
    auto_publish: bool = True


@router.post("/pipeline/start_batch")
def start_pipeline_batch(req: StartBatchRequest):
    """Запускает N конвейеров направлений (один на каждый элемент directions) - вызывается ТОЛЬКО
    после явного подтверждения владельцем превью от /pipeline/parse_batch. Все связаны общим
    batch_id для группировки в истории, каждый идёт своей независимой цепочкой (тот же
    _create_pipeline_chain/_run_pipeline_texts, что и одиночный запуск - логика не дублируется)."""
    if not req.directions:
        return {"status": "error", "message": "Список направлений пуст"}

    batch_id = f"batch-{int(time.time() * 1000)}"
    db = SessionLocal()
    try:
        task_ids = []
        for d in req.directions:
            task = _create_pipeline_chain(
                db, req.account_id, d.direction, d.count, d.banner_count,
                d.uniquify_titles, d.uniquify_descriptions, req.auto_check, req.auto_publish,
                d.price_from, d.price_to, batch_id=batch_id,
            )
            task_ids.append(task.id)
        return {"status": "ok", "batch_id": batch_id, "task_ids": task_ids, "total": len(task_ids)}
    finally:
        db.close()
