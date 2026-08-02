import os as _os_gc
_GC_MODEL = _os_gc.environ.get("GIGACHAT_MODEL", "GigaChat" + "-Pro")
from fastapi import UploadFile, File, Form, APIRouter
from fastapi.responses import Response
from pydantic import BaseModel
import httpx
import os
import random
import html

# импорт на уровне модуля - чтобы create_all() увидел таблицу category_templates при
# старте (avito.py импортируется в main.py до create_all(); импорт внутри функции таблицу
# не зарегистрирует - та же грабля, что и с моделью User)
from app.models.category_template import CategoryTemplate, CategoryTreeLeaf

router = APIRouter(prefix="/api/avito", tags=["avito"])
# Отдельный роутер БЕЗ авторизации - только для двух эндпоинтов, которые дёргает не наш фронт,
# а сам Avito (XML-фид) и его официальный валидатор xmlcheck (feed_preview) - у них не может быть
# нашего JWT. Регистрируется в main.py отдельно, без dependencies=[Depends(get_current_user_or_internal)].
public_router = APIRouter(prefix="/api/avito", tags=["avito-public"])

AVITO_CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
AVITO_CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")

FEEDS = {}

_ACTION_RU = {
    "set_autopilot": "изменил(а) режим работы Бориса",
    "set_kpi_settings": "задал(а) цель по лидам",
    "delete_kpi_settings": "сбросил(а) цель по лидам",
    "set_republish_settings": "настроил(а) авто-перезапуск объявлений",
    "delete_republish_settings": "сбросил(а) настройки перезапуска",
    "cpx_apply_archive": "снял(а) продвижение с объявления",
    "cpx_apply_bid": "изменил(а) ставку продвижения",
    "republish_apply": "перезапустил(а) неэффективные объявления",
    "edit_item": "изменил(а) объявление",
    "kpi_plan_execute": "усилил(а) тексты объявлений",
    "kpi_autopilot_run": "выполнил(а) план по лидам",
    "publish_validation_ok": "проверил(а) объявления перед публикацией",
    "publish_validation_failed": "остановил(а) публикацию — объявления не прошли проверку",
    "feed_blocked": "приостановил(а) выгрузку — часть объявлений с ошибками",
    "accept_terms": "принял(а) условия использования",
    "set_payment": "отметил(а) оплату",
}


def _humanize_entry(e):
    """Переводит запись журнала в человекочитаемый вид: кто и что сделал."""
    if not isinstance(e, dict):
        return e
    actor = str(e.get("actor", "boris"))
    is_boris = actor.startswith("boris")
    who = "🤖 Борис" if is_boris else "👤 Вы"
    action = str(e.get("action", ""))
    phrase = _ACTION_RU.get(action)
    details = str(e.get("details") or e.get("text") or "")
    if phrase:
        # для Бориса — "Борис снял продвижение...", для клиента — "Вы задали цель..."
        verb = phrase if is_boris else phrase.replace("(а)", "")
        human = who + " " + verb
        if details:
            human += " — " + details
    else:
        # незнакомый код: показываем понятные details, без сырого имени
        human = (who + " — " + details) if details else (who + " · " + action)
    out = dict(e)
    out["who"] = who
    out["human"] = human
    return out


def _audit_log(account_id: str, action: str, details: str = "", actor: str = "boris"):
    """Журнал действий: записывает каждое изменяющее действие в Storage.
    Позволяет ответить 'кто/что/когда/почему' при любом инциденте.
    actor: 'boris' | 'director' | 'user' — кто инициировал действие."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import datetime

    db = SessionLocal()
    try:
        key = "audit_log"
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        log = []
        if row:
            try:
                log = _json.loads(row.value)
            except Exception:
                log = []
        log.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "actor": actor,
            "action": action,
            "details": details[:500],
        })
        # Храним последние 500 записей, чтобы не раздувать
        log = log[-500:]
        raw = _json.dumps(log, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            row = Storage(account_id=account_id, key=key, value=raw)
            db.add(row)
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


def _autopilot_allows(account_id: str) -> bool:
    """Проверяет, разрешено ли Борису действовать без подтверждения прямо сейчас.
    Настройки в Storage (ключ autopilot_settings):
      {"mode": "always_ask" | "always_auto" | "dates",
       "date_from": "YYYY-MM-DD", "date_to": "YYYY-MM-DD"}
    По умолчанию (нет настроек) — always_ask (безопасно: всегда спрашивать)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import date as _date

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "autopilot_settings").first()
        if not row:
            return False  # по умолчанию всегда спрашиваем
        cfg = _json.loads(row.value)
        mode = cfg.get("mode", "always_ask")
        if mode == "always_auto":
            return True
        if mode == "always_ask":
            return False
        if mode == "dates":
            today = _date.today().isoformat()
            df = cfg.get("date_from", "")
            dt = cfg.get("date_to", "")
            return bool(df and dt and df <= today <= dt)
        return False
    except Exception:
        return False
    finally:
        db.close()


def _get_avito_credentials(account_id: str):
    """Ключи конкретного клиента из базы (расшифровываются на лету, см. app/crypto_utils.py -
    в БД они хранятся зашифрованными Fernet). Для otdushi — fallback на .env, если в базе пусто
    (глобальные AVITO_CLIENT_ID/SECRET в .env исторически не шифруются)."""
    from app.db.session import SessionLocal
    from app.models.account import Account
    from app.crypto_utils import decrypt_secret
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if acc and acc.avito_client_id and acc.avito_client_secret:
            return decrypt_secret(acc.avito_client_id), decrypt_secret(acc.avito_client_secret), acc.avito_user_id
    finally:
        db.close()
    if account_id == "otdushi":
        return AVITO_CLIENT_ID, AVITO_CLIENT_SECRET, None
    return None, None, None

def get_avito_token(account_id: str = "otdushi"):
    client_id, client_secret, _ = _get_avito_credentials(account_id)
    if not client_id or not client_secret:
        return {"error": f"У клиента {account_id} не настроен Avito API-ключ"}
    response = httpx.post(
        "https://api.avito.ru/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret
        }
    )
    return response.json()


def _extract_token(token_data):
    """Безопасно достаёт access_token. Если ключей нет/Avito не подключён -
    кидаем понятную 400 вместо KeyError/500."""
    if not isinstance(token_data, dict) or "access_token" not in token_data:
        msg = token_data.get("error") if isinstance(token_data, dict) else "Avito не подключён"
        raise HTTPException(status_code=400, detail=msg or "Avito не подключён к этому аккаунту")
    return token_data["access_token"]

from fastapi import Depends as _DepSec
from app.api.auth import require_owner as _ReqOwner, get_current_user as _CurUser
@router.get("/check")
def check_avito(account_id: str = "otdushi"):
    token_data = get_avito_token(account_id)
    if "access_token" in token_data:
        if not isinstance(token_data, dict) or "access_token" not in token_data:
            return {"status": "not_connected", "avito_connected": False,
                    "message": (token_data.get("error") if isinstance(token_data, dict) else None) or "Avito не подключён"}
        return {"status": "connected", "token": token_data["access_token"][:20] + "..."}
    return {"status": "error", "details": token_data}

@router.get("/me")
def get_me(account_id: str = "otdushi"):
    token_data = get_avito_token(account_id)
    token = _extract_token(token_data)
    response = httpx.get(
        "https://api.avito.ru/core/v1/accounts/self",
        headers={"Authorization": f"Bearer {token}"}
    )
    return response.json()

class DuplicateItemRequest(BaseModel):
    account_id: str
    original_item_id: str
    title: str
    description: str = ""
    price: int = 0
    images: list[str] = []

@router.post("/duplicate_item")
def duplicate_item(req: DuplicateItemRequest):
    """Создаёт черновик-дубль объявления для дальнейшего редактирования (текст, фото, баннеры, шаблон)."""
    import json as _json, time as _time
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    draft_id = f"{req.original_item_id}_{int(_time.time())}"
    draft = {
        "draft_id": draft_id,
        "original_item_id": req.original_item_id,
        "title": req.title,
        "description": req.description,
        "price": req.price,
        "images": req.images,
        "banners": [],
        "is_template": False,
        "created_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    db = SessionLocal()
    try:
        row = Storage(account_id=req.account_id, key=f"duplicate_draft:{draft_id}", value=_json.dumps(draft, ensure_ascii=False))
        db.add(row)
        db.commit()
    finally:
        db.close()
    return {"status": "ok", "draft": draft}

@router.get("/duplicate_drafts")
def get_duplicate_drafts(account_id: str = "otdushi"):
    """Список черновиков-дублей для фильтра «Новые дубли»."""
    import json as _json
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.account_id == account_id, Storage.key.like("duplicate_draft:%")).all()
        drafts = []
        for r in rows:
            try:
                drafts.append(_json.loads(r.value))
            except Exception:
                pass
        drafts.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return {"status": "ok", "drafts": drafts}
    finally:
        db.close()

class UpdateDuplicateDraftRequest(BaseModel):
    account_id: str
    draft_id: str
    title: str = None
    description: str = None
    price: int = None
    images: list[str] = None
    banners: list[str] = None
    is_template: bool = None

@router.post("/duplicate_draft/update")
def update_duplicate_draft(req: UpdateDuplicateDraftRequest):
    """Сохраняет правки черновика-дубля (текст/фото/баннеры/флаг шаблона)."""
    import json as _json
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        key = f"duplicate_draft:{req.draft_id}"
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == key).first()
        if not row:
            return {"status": "error", "message": "Черновик не найден"}
        draft = _json.loads(row.value)
        for field in ["title", "description", "price", "images", "banners", "is_template"]:
            val = getattr(req, field)
            if val is not None:
                draft[field] = val
        row.value = _json.dumps(draft, ensure_ascii=False)
        db.commit()
        return {"status": "ok", "draft": draft}
    finally:
        db.close()

class DeleteDuplicateDraftRequest(BaseModel):
    account_id: str
    draft_id: str

@router.post("/duplicate_draft/delete")
def delete_duplicate_draft(req: DeleteDuplicateDraftRequest):
    """Удаляет черновик-дубль (после публикации или отмены)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    db = SessionLocal()
    try:
        key = f"duplicate_draft:{req.draft_id}"
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == key).first()
        if row:
            db.delete(row)
            db.commit()
        return {"status": "ok"}
    finally:
        db.close()

class RewriteTextRequest(BaseModel):
    account_id: str = ""
    title: str
    description: str = ""
    include_variants: bool = None
    engine: str = "gigachat"

@router.post("/rewrite_text")
def rewrite_text(req: RewriteTextRequest):
    """Переписывает заголовок и описание объявления через GigaChat для уникализации при дублировании.
    Заголовок дополняется спинтакс-группой из синонимичных поисковых формулировок (SEO).
    Описание может дополняться спинтакс-блоками характеристик/цвета (RAL) — если это релевантно товару."""
    style_reference = """БРУСЧАТКА 200×100×60 ОТ ПРОИЗВОДИТЕЛЯ — ОТ 650 ₽/м². Звоните, пишите — доставим куда нужно.

Цены и наличие уточняйте по телефону или в сообщениях.

Надёжная, прочная и долговечная брусчатка напрямую от производителя.

Отлично подходит для укладки дворов, парковок, тротуаров, дорожек, въездных зон и коммерческих объектов.

✔ Размер: 200×100×60
✔ Высокая прочность и устойчивость к нагрузкам
✔ Морозостойкость и долгий срок службы
✔ Аккуратная геометрия и качественный прокрас
✔ Подходит для частных и коммерческих объектов

В наличии разные цвета и варианты исполнения.

Доставка по Москве и Московской области:
Москва, Зеленоград, Апрелевка, Андреевка, Волоколамск, Высоковск, Глебовский, Горки-10, Деденево, Дедовск, Звенигород, Истра, Клин, Красногорск, Кубинка, Лобня, Наро-Фоминск, Новопетровское, Одинцово, Павловская Слобода, Рогачёво, Руза, Солнечногорск, Химки и другие города области.

Поможем подобрать объём, цвет и рассчитать необходимое количество.

Звоните или пишите — быстро ответим и организуем доставку.

Размер: {{200×100×60|200.02×100.01×60.03|200.01×100.03×60.02}}
Цвет: {{RAL 1001 Бежевый|RAL 8004 Терракотовый|RAL 8017 Коричневый|RAL 7024 Серый}}"""

    if req.include_variants is True:
        variants_instruction = "ОБЯЗАТЕЛЬНО добавь оба спинтакс-блока (характеристики и цвет RAL) в конце описания, даже если для этого товара они не кажутся строго необходимыми — придумай разумные варианты на основе типа товара."
    elif req.include_variants is False:
        variants_instruction = "НЕ добавляй никакие спинтакс-блоки характеристик или цвета в конце описания — только сам текст."
    else:
        variants_instruction = "Сам реши, уместны ли для этого товара блоки характеристик/цвета (RAL) — добавляй ТОЛЬКО если товар физический и у него реально бывают варианты размера/цвета (стройматериалы, мебель, техника и т.п.); для услуг, текстов, нематериальных товаров — не добавляй вообще."

    prompt = f"""Ты — опытный копирайтер продающих объявлений на Avito. Перепиши объявление ЗАНОВО: сохрани все факты и цену, но полностью пересобери текст так, чтобы он реально продавал лучше исходного.

ИСХОДНЫЙ ЗАГОЛОВОК: {req.title}
ИСХОДНОЕ ОПИСАНИЕ: {req.description}

ОБРАЗЕЦ СТИЛЯ И СТРУКТУРЫ ОПИСАНИЯ (ориентируйся на подачу, эмодзи-разделители и ритм — НЕ копируй тематику или конкретные факты, только манеру подачи):
{style_reference}

ТРЕБОВАНИЯ К ЗАГОЛОВКУ:
1. Конкретный, без воды, содержит ключевую выгоду или характеристику (не просто название товара). Можно один эмодзи в начале, если уместно.
2. Первая буква первого слова заголовка (после эмодзи, если он есть) — ОБЯЗАТЕЛЬНО заглавная.
3. В САМОМ КОНЦЕ заголовка (через пробел или дефис) добавь ОДНУ спинтакс-группу из РОВНО 10 синонимичных высоко- и среднечастотных поисковых формулировок этого же товара/услуги (как реально ищут в Avito/Яндексе), разделённых символом | внутри фигурных скобок. Каждый вариант внутри группы ТОЖЕ с заглавной буквы. КРИТИЧНО ВАЖНО: группа ОБЯЗАТЕЛЬНО должна начинаться символом {{ и заканчиваться символом }} — без этих скобок вся конструкция не будет работать как уникализация, а превратится в мусорный текст. НИКОГДА не забывай открывающую {{ и закрывающую }} скобки. Пример формата (тема другая, только для понимания структуры): {{Тротуарная плитка|Плитка тротуарная|Плитка для дорожек|Брусчатка тротуарная|Плитка садовая|Плитка для двора|Уличная плитка|Плитка для мощения|Плитка для укладки|Тротуарная плитка недорого}}.

ТРЕБОВАНИЯ К ОПИСАНИЮ (СТРОГО КОПИРУЙ СТРУКТУРУ И ТОН ОБРАЗЦА ВЫШЕ, только под конкретный товар):
1. ПЕРВАЯ СТРОКА ЗАГЛАВНЫМИ: НАЗВАНИЕ + размер + "ОТ ПРОИЗВОДИТЕЛЯ" + "— ОТ (цена) Р". Сразу призыв: "Звоните, пишите — доставим куда нужно."
2. "Цены и наличие уточняйте по телефону или в сообщениях."
3. Одно-два предложения: надёжный, прочный, долговечный товар напрямую от производителя.
4. "Отлично подходит для укладки дворов, парковок, тротуаров, дорожек, въездных зон и коммерческих объектов."
5. РОВНО пять буллетов с галочкой, каждый с новой строки: размер / прочность / морозостойкость и срок службы / геометрия и прокрас / для частных и коммерческих.
6. "В наличии разные цвета и варианты исполнения."
7. Блок доставки со ВСЕМ списком городов из образца (НЕ сокращай).
8. "Поможем подобрать объём, цвет и рассчитать необходимое количество."
9. "Звоните или пишите — быстро ответим и организуем доставку."
10. ОБЯЗАТЕЛЬНО в САМОМ КОНЦЕ описания добавь ДВА спинтакс-блока для уникализации (точно как в образце):
Размер: {{вариант1|вариант2|вариант3}} — реальные микро-вариации размера (например 200×100×60|200.02×100.01×60.03)
Цвет: {{RAL код1|RAL код2|RAL код3|RAL код4}} — реальные коды RAL, подходящие товару
Спинтакс ОБЯЗАТЕЛЕН — без него объявления будут дублями и Avito их заблокирует.
ЗАПРЕЩЕНО: эмодзи кроме галочки, фразы-вода ("вы ищете", "воплотить"), структура БОЛЬ-РЕШЕНИЕ. НЕ выдумывай факты.
11. {variants_instruction}8. Если добавляешь блоки характеристик/цвета — размести их В САМОМ КОНЦЕ описания, каждый на отдельной строке, в формате:
   ХАРАКТЕРИСТИКИ: {{значение_вариант1|значение_вариант2|...до 10 вариантов}} (например для размеров товара — слегка отличающиеся числа, как реальные близкие вариации, а не грубо разные товары)
   ЦВЕТ: {{RAL 1015|RAL 3020|...до 10 реальных кодов RAL Classic, подходящих по смыслу этому товару}}

Формулировки и порядок подачи должны полностью отличаться от исходника (нужно для уникализации, не косметическая правка).

Ответь СТРОГО в формате (ОПИСАНИЕ может занимать много строк — это нормально, включай туда всё, включая блоки ХАРАКТЕРИСТИКИ/ЦВЕТ если они есть):
ЗАГОЛОВОК: <новый заголовок с группой спинтакса в конце>
ОПИСАНИЕ:
<новое описание, возможно на нескольких строках>"""

    raw = None
    if req.engine == "chatgpt":
        try:
            import requests as _requests_local
            from proxy_pool import get_intl_requests_proxies
            api_key = os.environ.get("OPENAI_API_KEY")
            proxies = get_intl_requests_proxies()
            resp = _requests_local.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                json={
                    "model": "gpt-5.4",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_completion_tokens": 1536,
                },
                proxies=proxies,
                timeout=60,
            )
            if resp.status_code != 200:
                return {"status": "error", "message": f"Ошибка ChatGPT: {resp.status_code} {resp.text[:200]}"}
            raw = resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return {"status": "error", "message": f"Ошибка ChatGPT: {e}"}
    else:
        try:
            raw = chat_with_fallback(
                [Messages(role=MessagesRole.USER, content=prompt)],
                temperature=0.9, max_tokens=1536,
                account_id=req.account_id or None, operation="переписать текст"
            )
        except Exception as e:
            return {"status": "error", "message": f"Ошибка GigaChat: {e}"}

    new_title = req.title
    new_description = req.description
    lines = raw.split("\n")
    desc_lines = []
    in_description = False
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("ЗАГОЛОВОК:"):
            new_title = stripped.split(":", 1)[1].strip()
            in_description = False
        elif stripped.upper().startswith("ОПИСАНИЕ:"):
            in_description = True
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                desc_lines.append(rest)
        elif in_description:
            desc_lines.append(line)

    if desc_lines:
        new_description = "\n".join(desc_lines).strip()

    # Подстраховка: если GigaChat забыл фигурные скобки вокруг спинтакс-группы в заголовке —
    # находим хвост с 2+ вертикальными чертами без скобок и оборачиваем сами
    import re as _re_local
    if "|" in new_title and "{" not in new_title:
        m = _re_local.search(r"([^{}]+\|[^{}]+(?:\|[^{}]+)+)$", new_title)
        if m:
            start = m.start(1)
            new_title = new_title[:start] + "{" + new_title[start:] + "}"

    return {"status": "ok", "title": new_title, "description": new_description}

@router.get("/items2")
def get_items2(account_id: str = "otdushi"):
    token_data = get_avito_token(account_id)
    if not isinstance(token_data, dict) or "access_token" not in token_data:
        # у аккаунта нет/невалидны ключи Avito - отдаём пусто, а не 500
        return {"resources": [], "items": [], "avito_connected": False,
                "message": "Avito не подключён к этому аккаунту"}
    token = _extract_token(token_data)
    all_items = []
    page = 1
    while page <= 20:  # защита от бесконечного цикла (до 2000 объявлений)
        response = httpx.get(
            "https://api.avito.ru/core/v1/items",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100, "page": page}
        )
        data = response.json()
        items = data.get("resources", [])
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
        page += 1

    # Обогащаем метриками (просмотры/контакты/избранное/конверсия) с Avito Statistics API
    stats_by_id = {}
    try:
        _, _, avito_user_id = _get_avito_credentials(account_id)
        if not avito_user_id:
            me_resp = httpx.get("https://api.avito.ru/core/v1/accounts/self", headers={"Authorization": f"Bearer {token}"})
            avito_user_id = str(me_resp.json().get("id", ""))
        if avito_user_id:
            from datetime import date as _date, timedelta as _timedelta
            date_to = _date.today().isoformat()
            date_from = (_date.today() - _timedelta(days=30)).isoformat()
            all_ids = [it["id"] for it in all_items]
            for i in range(0, len(all_ids), 200):
                batch = all_ids[i:i+200]
                stats_resp = httpx.post(
                    f"https://api.avito.ru/stats/v1/accounts/{avito_user_id}/items",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"dateFrom": date_from, "dateTo": date_to, "fields": ["uniqViews", "uniqContacts", "uniqFavorites"], "itemIds": batch}
                )
                if stats_resp.status_code == 200:
                    for it in stats_resp.json().get("result", {}).get("items", []):
                        views = contacts = favorites = 0
                        for s in it.get("stats", []):
                            views += s.get("uniqViews", 0)
                            contacts += s.get("uniqContacts", 0)
                            favorites += s.get("uniqFavorites", 0)
                        stats_by_id[it["itemId"]] = {"views": views, "contacts": contacts, "favorites": favorites}
    except Exception:
        pass

    for it in all_items:
        s = stats_by_id.get(it["id"], {"views": 0, "contacts": 0, "favorites": 0})
        it["views"] = s["views"]
        it["contacts"] = s["contacts"]
        it["favorites"] = s["favorites"]
        it["conversion"] = round(s["contacts"] / s["views"] * 100, 1) if s["views"] > 0 else 0

    return {"resources": all_items, "total": len(all_items)}

def spin(text: str) -> str:
    if not text:
        return ""
    result = text
    while "{" in result and "}" in result:
        start = result.rfind("{")
        end = result.find("}", start)
        if start == -1 or end == -1:
            break
        options = result[start+1:end].split("|")
        chosen = random.choice(options)
        result = result[:start] + chosen + result[end+1:]
    # чистим мусор от спинтакса: висячие | и двойные пробелы
    result = result.replace("|", " ")
    import re as _re
    result = _re.sub(r"\s+", " ", result).strip()
    return result

class FeedItem(BaseModel):
    id: str
    title: str
    description: str
    price: int = 0
    category: str = "Предложение услуг"
    service_type: str = ""
    service_subtype: str = ""
    work_experience: str = ""
    guarantee: str = ""
    goods_type: str = ""
    condition: str = ""
    kitchen_type: str = ""
    price_type: str = ""
    color: str = ""
    goods_subtype: str = ""
    ad_type: str = ""
    category_id: str = ""  # ключ базы знаний категории (CategoryTemplate.category_id) - на уровне ТОВАРА,
                            # не аккаунта: в одном аккаунте могут быть товары разных категорий
    address: str
    phone: str = ""
    manager: str = ""
    images: list[str] = []
    params: dict = {}
    date_begin: str = ""
    date_end: str = ""
    timezone: str = ""
    source_batch_id: int | None = None

class GenerateFeedRequest(BaseModel):
    account_id: str
    items: list[FeedItem]
    merge: bool = False

FEED_ITEMS_STORE: dict = {}

def _load_feed_items(account_id: str) -> list:
    """Читает items фида из постоянного хранилища (PostgreSQL), переживает рестарт бэкенда.
    ВАЖНО: каждый item валидируется ОТДЕЛЬНО - раньше один битый item (например без обязательных
    description/address, попавший в хранилище в обход обычного пути создания черновиков) валил
    исключением весь список comprehension, и bare except тихо возвращал [] для ВСЕГО аккаунта,
    хотя остальные сотни объявлений были в полном порядке (инцидент с фидом dinara)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not row:
            return []
        raw = _json.loads(row.value)
        items = []
        for it in raw:
            try:
                items.append(FeedItem(**it))
            except Exception as e:
                print(f"[_load_feed_items] пропущен битый item {it.get('id')} у {account_id}: {str(e)[:200]}", flush=True)
        return items
    except Exception:
        return []
    finally:
        db.close()

async def _detect_category_anonymous(niche_query: str, city: str = "moskva"):
    """Анонимный поиск объявления по нише клиента, определение категории из URL после клика.
    Проверенный рабочий метод (без капчи, без логина)."""
    from proxy_pool import get_playwright_proxy, get_clean_playwright_proxy
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    import urllib.parse

    proxy = await get_clean_playwright_proxy()
    stealth = Stealth()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"], proxy=proxy)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 1200}
        )
        await stealth.apply_stealth_async(context)
        page = await context.new_page()

        # Блокируем картинки/стили/шрифты/медиа/скрипты - нужен только HTML с текстом
        async def _block_heavy_resources(route):
            if route.request.resource_type in ("image", "stylesheet", "font", "media", "script"):
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", _block_heavy_resources)

        # === ЗАМЕР ТРАФИКА (для расчёта себестоимости парсинга) ===
        _traffic = {"bytes": 0, "requests": 0}
        async def _count_traffic(response):
            try:
                body = await response.body()
                _traffic["bytes"] += len(body)
                _traffic["requests"] += 1
            except Exception:
                pass
        page.on("response", lambda r: __import__("asyncio").ensure_future(_count_traffic(r)))

        query_encoded = urllib.parse.quote(niche_query)
        url = f"https://www.avito.ru/{city}?q={query_encoded}"
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(5000)
        _mb = _traffic["bytes"] / 1024 / 1024
        _rub_ru = _mb / 1024 * 4 * 95
        print(f"[ТРАФИК] Категория Avito: {_mb:.2f} МБ, {_traffic['requests']} запросов, ~{_rub_ru:.2f} руб (RU прокси)", flush=True)

        try:
            first_item = page.locator('[data-marker="item-title"]').first
            await first_item.click(timeout=10000)
            await page.wait_for_timeout(4000)
            final_url = page.url
        except Exception:
            await browser.close()
            return None

        await browser.close()

        # Разбираем путь категории из URL: avito.ru/{city}/{category_slug}/{item_slug}_{id}
        try:
            path_parts = final_url.split("avito.ru/")[1].split("/")
            category_slug = path_parts[1] if len(path_parts) > 1 else None
            return {"url": final_url, "category_slug": category_slug}
        except Exception:
            return {"url": final_url, "category_slug": None}


@router.get("/category_templates")
def get_category_templates():
    """Смотрим, что уже накоплено в базе знаний обязательных полей категорий Avito."""
    import json as _json_ct
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(CategoryTemplate).all()
        return {
            "status": "ok",
            "count": len(rows),
            "templates": [
                {
                    "category_id": r.category_id,
                    "category_name": r.category_name,
                    "template_id": r.template_id,
                    "required_fields": _json_ct.loads(r.required_fields) if r.required_fields else [],
                    "field_rules": _json_ct.loads(r.field_rules) if r.field_rules else {},
                    "enum_values": _json_ct.loads(r.enum_values) if r.enum_values else {},
                    "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.post("/category_tree/crawl")
def crawl_category_tree_endpoint():
    """Разовая админ-операция: обходит всё дерево документации Автозагрузки Avito headed-браузером
    и сохраняет карту {путь -> template_id} в CategoryTreeLeaf. Синхронно (без очереди задач) -
    вызывается вручную, не на каждый запрос."""
    from app.services.category_resolver import crawl_category_tree
    try:
        return crawl_category_tree()
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/category_tree")
def get_category_tree_endpoint():
    """Смотрим, что уже накоплено обходом дерева категорий (crawl_category_tree)."""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(CategoryTreeLeaf).all()
        return {
            "status": "ok",
            "count": len(rows),
            "leaves": [
                {
                    "top_level": r.top_level,
                    "path": r.path,
                    "leaf_name": r.leaf_name,
                    "template_id": r.template_id,
                    "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
                }
                for r in rows
            ],
        }
    finally:
        db.close()


@router.post("/detect_category")
def detect_category_endpoint(payload: dict):
    """Определяет категорию для нового клиента: сначала пробует реальные объявления через API,
    если их нет — анонимный поиск по нише. Сохраняет результат в Storage account_id/detected_category."""
    account_id = payload.get("account_id")
    niche = payload.get("niche", "")
    if not account_id:
        return {"status": "error", "message": "account_id обязателен"}

    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json2
    import asyncio as _asyncio2

    db = SessionLocal()
    try:
        detected_category_name = None
        source = None

        # Метод 1: реальные объявления через API (если ключи уже есть)
        try:
            token_data = get_avito_token(account_id)
            token = token_data.get("access_token")
            if token:
                r = httpx.get(
                    "https://api.avito.ru/core/v1/items",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"per_page": 5, "page": 1, "status": "active"},
                    timeout=10
                )
                if r.status_code == 200:
                    items = r.json().get("resources", [])
                    if items:
                        detected_category_name = items[0].get("category", {}).get("name")
                        source = "real_items_api"
        except Exception:
            pass

        # Метод 2: анонимный поиск по нише
        if not detected_category_name and niche:
            try:
                result = _asyncio2.run(_detect_category_anonymous(niche))
                if result and result.get("category_slug"):
                    detected_category_name = result["category_slug"]
                    source = "anonymous_search"
            except Exception:
                pass

        if detected_category_name:
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "detected_category").first()
            value = _json2.dumps({"category": detected_category_name, "source": source}, ensure_ascii=False)
            if row:
                row.value = value
            else:
                row = Storage(account_id=account_id, key="detected_category", value=value)
                db.add(row)
            db.commit()
            return {"status": "ok", "category": detected_category_name, "source": source}
        else:
            return {"status": "not_found", "message": "Не удалось определить категорию, нужен ручной выбор"}
    finally:
        db.close()


@router.get("/detected_category")
def get_detected_category(account_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json3

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "detected_category").first()
        if row:
            return {"status": "ok", **_json3.loads(row.value)}
        return {"status": "not_found"}
    finally:
        db.close()


class FeedItemUpdateRequest(BaseModel):
    account_id: str
    item_id: str
    title: str = None
    price: int = None
    description: str = None
    images: list = None
    template_id: str = None


@router.post("/feed_item/update")
def update_feed_item(req: FeedItemUpdateRequest):
    """Правит одно объявление в фиде (title/price/description). Категорийные поля не трогает."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "feed_items").first()
        if not row:
            return {"status": "error", "message": "фид не найден"}
        items = _json.loads(row.value)
        found = False
        for it in items:
            if str(it.get("id")) == str(req.item_id):
                if req.title is not None: it["title"] = req.title
                if req.price is not None: it["price"] = req.price
                if req.description is not None: it["description"] = req.description
                if req.images is not None: it["images"] = req.images
                if req.template_id is not None: it["template_id"] = req.template_id
                found = True
                break
        if not found:
            return {"status": "error", "message": "объявление не найдено в фиде"}
        row.value = _json.dumps(items, ensure_ascii=False)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


class AutoDuplicateRequest(BaseModel):
    account_id: str
    item_id: str
    match_title: str = ""      # запасной ключ связи, когда id из статистики не совпадает с id фида
    dry_run: bool = True


@router.post("/auto_duplicate")
def auto_duplicate(req: AutoDuplicateRequest):
    """Конвейер размножения: берёт объявление из фида, уникализирует текст (спинтакс размеров/цветов),
    создаёт НОВЫЙ item в фиде с уникальным id. Копирует категорию и фото 1:1.
    dry_run=True — только показать результат, в фид НЕ пишет. Публикация — через фид при следующей выгрузке."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json, time as _time
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "feed_items").first()
        if not row:
            return {"status": "error", "message": "фид не найден"}
        items = _json.loads(row.value)
        src = None
        for it in items:
            if str(it.get("id")) == str(req.item_id):
                src = it
                break
        # мост статистика↔фид: в daily_stats Avito-id + РАСКРЫТЫЙ заголовок,
        # в фиде наш id + заголовок-СПИНТАКС "{вар1|вар2|...}". Связываем: раскрытый title
        # статистики должен быть одним из вариантов внутри спинтакса фида.
        if not src and req.match_title:
            import re as _re
            want = str(req.match_title).strip().lower()
            def _spin_variants(t):
                t = str(t or "")
                m = _re.search(r"\{([^{}]*)\}", t)
                if m:
                    head = t[:m.start()].strip()
                    return [(head + " " + v.strip()).strip().lower() if head else v.strip().lower()
                            for v in m.group(1).split("|")]
                return [t.strip().lower()]
            for it in items:
                vars_ = _spin_variants(it.get("title", ""))
                if want in vars_ or any(want == v or want in v for v in vars_):
                    src = it
                    break
        if not src:
            return {"status": "error", "message": "объявление не найдено в фиде"}

        # уникализация текста через готовый rewrite_text (спинтакс вариантов включён)
        rw = rewrite_text(RewriteTextRequest(
            account_id=req.account_id,
            title=src.get("title", ""),
            description=src.get("description", ""),
            include_variants=True,
        ))
        new_title = rw.get("title") or src.get("title", "")
        new_desc = rw.get("description") or src.get("description", "")

        # новый item: копия оригинала (категория, фото, все поля) + уникализированный текст + новый id
        dup = dict(src)
        dup["id"] = f"{src.get('id')}-d{int(_time.time())}"
        dup["title"] = new_title
        dup["description"] = new_desc
        dup["_dup_of"] = str(src.get("id"))

        if req.dry_run:
            return {"status": "ok", "dry_run": True, "preview": {
                "new_id": dup["id"], "title": new_title,
                "description": new_desc[:600], "images_count": len(src.get("images") or []),
                "note": "Это предпросмотр. В фид НЕ добавлено. Повторите с dry_run=false для публикации через фид."}}

        items.append(dup)
        row.value = _json.dumps(items, ensure_ascii=False)
        db.commit()
        try:
            _audit_log(req.account_id, "auto_duplicate",
                       f"создал дубль объявления «{src.get('title','')[:40]}» (новый id {dup['id']}) — попадёт в выдачу при следующей выгрузке", "boris")
        except Exception:
            pass
        return {"status": "ok", "dry_run": False, "new_id": dup["id"], "feed_size": len(items)}
    finally:
        db.close()


class AutoDuplicateRunRequest(BaseModel):
    account_id: str
    max_per_item: int = 2       # сколько дублей допустимо на одно объявление всего
    daily_limit: int = 5        # сколько новых дублей в день на аккаунт (защита от спам-бана Avito)
    min_contacts: int = 1       # дублируем только ХОДОВЫЕ (контактов не меньше)
    dry_run: bool = True


def _dup_count(db, account_id, item_id):
    from app.models.storage import Storage
    import json as _j
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == f"dup_count:{item_id}").first()
    if not row:
        return 0
    try:
        return int(_j.loads(row.value).get("n", 0))
    except Exception:
        return 0


def _dup_count_inc(db, account_id, item_id):
    from app.models.storage import Storage
    import json as _j
    k = f"dup_count:{item_id}"
    row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == k).first()
    n = _dup_count(db, account_id, item_id) + 1
    val = _j.dumps({"n": n}, ensure_ascii=False)
    if row:
        row.value = val
    else:
        db.add(Storage(account_id=account_id, key=k, value=val))
    db.commit()


@router.post("/auto_duplicate_run")
def auto_duplicate_run(req: AutoDuplicateRunRequest):
    """Автопилот размножения: выбирает ХОДОВЫЕ объявления (contacts >= min_contacts),
    делает дубли с потолком max_per_item на объявление и daily_limit в день на аккаунт.
    Публикация через фид. dry_run=True — только план, ничего не пишет."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json, datetime as _dt
    db = SessionLocal()
    try:
        today = _dt.date.today().isoformat()
        # дневной счётчик
        drow = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == f"dup_today:{today}").first()
        done_today = 0
        if drow:
            try:
                done_today = int(_json.loads(drow.value).get("n", 0))
            except Exception:
                done_today = 0
        room = max(0, req.daily_limit - done_today)
        if room <= 0:
            return {"status": "ok", "done_today": done_today, "created": 0, "reason": "дневной лимит дублей исчерпан"}

        # ходовые объявления из свежей статистики; если за сегодня нет — берём последнюю доступную
        srow = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == f"daily_stats:{today}").first()
        if not srow:
            srows = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key.like("daily_stats:%")).all()
            srow = max(srows, key=lambda r: r.key, default=None)
        if not srow:
            return {"status": "ok", "created": 0, "reason": "нет статистики (daily_stats) ни за один день"}
        items_stats = (_json.loads(srow.value) or {}).get("items", [])
        hot = [it for it in items_stats
               if it.get("status") == "active" and it.get("contacts", 0) >= req.min_contacts]
        hot.sort(key=lambda it: it.get("contacts", 0), reverse=True)

        plan, created = [], 0
        for it in hot:
            if created >= room:
                break
            iid = str(it.get("id"))
            if _dup_count(db, req.account_id, iid) >= req.max_per_item:
                continue
            entry = {"item_id": iid, "title": it.get("title", "")[:50], "contacts": it.get("contacts", 0)}
            if req.dry_run:
                entry["will"] = "будет создан дубль"
                plan.append(entry)
                created += 1
                continue
            res = auto_duplicate(AutoDuplicateRequest(account_id=req.account_id, item_id=iid, match_title=it.get("title", ""), dry_run=False))
            if res.get("status") == "ok":
                _dup_count_inc(db, req.account_id, iid)
                entry["new_id"] = res.get("new_id")
                plan.append(entry)
                created += 1

        if not req.dry_run and created > 0:
            val = _json.dumps({"n": done_today + created}, ensure_ascii=False)
            if drow:
                drow.value = val
            else:
                db.add(Storage(account_id=req.account_id, key=f"dup_today:{today}", value=val))
            db.commit()
            try:
                _audit_log(req.account_id, "auto_duplicate_run",
                           f"размножил ходовые объявления: создано дублей {created} (дневной лимit {req.daily_limit})", "boris")
            except Exception:
                pass

        return {"status": "ok", "dry_run": req.dry_run, "done_today": done_today,
                "created": created, "candidates": len(hot), "plan": plan[:20]}
    finally:
        db.close()


class FeedItemDeleteRequest(BaseModel):
    account_id: str
    item_id: str


@router.post("/feed_item/delete")
def delete_feed_item(req: FeedItemDeleteRequest):
    """Удаляет объявление из фида по id."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "feed_items").first()
        if not row:
            return {"status": "error", "message": "фид не найден"}
        items = _json.loads(row.value)
        before = len(items)
        items = [it for it in items if str(it.get("id")) != str(req.item_id)]
        row.value = _json.dumps(items, ensure_ascii=False)
        db.commit()
        return {"status": "ok", "removed": before - len(items), "remaining": len(items)}
    finally:
        db.close()


@router.get("/feed_export_xlsx")
def feed_export_xlsx(account_id: str):
    """Выгружает объявления фида в Excel для массовой правки. ID не трогать!"""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from fastapi.responses import StreamingResponse
    import json as _json, io
    import openpyxl
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        items = _json.loads(row.value) if row else []
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Feed"
        ws.append(["ID (НЕ МЕНЯТЬ)", "Заголовок", "Цена", "Город", "Описание"])
        for c in range(1, 6):
            ws.cell(row=1, column=c).font = openpyxl.styles.Font(bold=True)
        for it in items:
            ws.append([
                it.get("id", ""),
                it.get("title", ""),
                it.get("price", 0),
                it.get("address", ""),
                it.get("description", ""),
            ])
        ws.column_dimensions["A"].width = 30
        ws.column_dimensions["B"].width = 40
        ws.column_dimensions["E"].width = 80
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        fname = f"feed_{account_id}.xlsx"
        return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                 headers={"Content-Disposition": f"attachment; filename={fname}"})
    finally:
        db.close()


@router.post("/feed_import_xlsx")
async def feed_import_xlsx(account_id: str = Form(...), file: UploadFile = File(...)):
    """Загружает исправленный Excel обратно в фид. Обновляет title/price/description по ID.
    Категорийные поля и фото не трогает - остаются как были."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json, io
    import openpyxl
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not row:
            return {"status": "error", "message": "фид не найден"}
        items = _json.loads(row.value)
        by_id = {str(it.get("id")): it for it in items}
        content = await file.read()
        wb = openpyxl.load_workbook(io.BytesIO(content))
        ws = wb.active
        updated = 0
        for r in ws.iter_rows(min_row=2, values_only=True):
            if not r or not r[0]:
                continue
            iid = str(r[0]).strip()
            if iid in by_id:
                it = by_id[iid]
                if r[1] is not None: it["title"] = str(r[1])
                if r[2] is not None:
                    try: it["price"] = int(r[2])
                    except: pass
                if len(r) > 4 and r[4] is not None: it["description"] = str(r[4])
                updated += 1
        row.value = _json.dumps(items, ensure_ascii=False)
        db.commit()
        return {"status": "ok", "updated": updated}
    finally:
        db.close()


@router.get("/active_items_full")
def get_active_items_full(account_id: str):
    """Список активных объявлений, подтянутых с Avito (отдельно от фида)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "active_avito_items").first()
        items = _json.loads(row.value) if row else []
        return {"status": "ok", "items": items, "count": len(items)}
    finally:
        db.close()


@router.get("/feed_items_full")
def get_feed_items_full(account_id: str):
    """Полный список объявлений фида для редактора (id, title, price, city)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not row:
            return {"status": "ok", "items": []}
        items = _json.loads(row.value)
        out = [{
            "id": it.get("id"),
            "title": it.get("title", ""),
            "price": it.get("price", 0),
            "address": it.get("address", ""),
            "description": it.get("description", ""),
            "images": it.get("images", []),
        } for it in items]
        return {"status": "ok", "items": out, "count": len(out)}
    finally:
        db.close()


@router.post("/feed_send_to_avito")
def feed_send_to_avito(account_id: str = Form(...)):
    """Запускает автозагрузку на Avito немедленно (не ждёт часового расписания)."""
    from app.db.session import SessionLocal
    from app.models.account import Account
    import httpx as _httpx
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc or not acc.avito_client_id:
            return {"status": "error", "message": "У аккаунта нет ключей Avito"}
        from app.crypto_utils import decrypt_secret
        tok = _httpx.post("https://api.avito.ru/token/", data={
            "grant_type": "client_credentials",
            "client_id": decrypt_secret(acc.avito_client_id),
            "client_secret": decrypt_secret(acc.avito_client_secret),
        }, timeout=20).json().get("access_token")
        if not tok:
            return {"status": "error", "message": "Не удалось получить токен Avito"}
        r = _httpx.post("https://api.avito.ru/autoload/v1/upload",
                        headers={"Authorization": f"Bearer {tok}"}, timeout=25)
        if r.status_code == 200:
            return {"status": "ok", "message": "Выгрузка на Avito запущена. Объявления обновятся в ближайшее время."}
        return {"status": "error", "message": f"Avito ответил {r.status_code}: {r.text[:150]}"}
    finally:
        db.close()


@router.post("/feed_import_active")
def feed_import_active(account_id: str = Form(...)):
    """Тянет живые активные объявления аккаунта с Avito и добавляет их в фид
    (с названием, ценой, городом, категорией) для редактирования."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.models.account import Account
    import httpx as _httpx, json as _json, time as _time
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.account_id == account_id).first()
        if not acc or not acc.avito_client_id:
            return {"status": "error", "message": "У аккаунта нет ключей Avito"}
        from app.crypto_utils import decrypt_secret
        tok = _httpx.post("https://api.avito.ru/token/", data={
            "grant_type": "client_credentials",
            "client_id": decrypt_secret(acc.avito_client_id),
            "client_secret": decrypt_secret(acc.avito_client_secret),
        }, timeout=20).json().get("access_token")
        if not tok:
            return {"status": "error", "message": "Не удалось получить токен"}
        # собираем все активные объявления постранично
        active = []
        page = 1
        while page <= 20:
            r = _httpx.get("https://api.avito.ru/core/v1/items",
                           headers={"Authorization": f"Bearer {tok}"},
                           params={"per_page": 100, "page": page, "status": "active"}, timeout=25)
            batch = r.json().get("resources", [])
            if not batch:
                break
            active.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        # пишем в ОТДЕЛЬНОЕ хранилище active_avito_items (не в фид!)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "active_avito_items").first()
        items = _json.loads(row.value) if row else []
        existing = {it.get("avito_id") for it in items}
        added = 0
        for a in active:
            if a.get("id") in existing:
                continue
            cat = a.get("category", {})
            items.append({
                "id": f"avito-import-{a.get('id')}",
                "title": a.get("title", ""),
                "description": "",
                "price": a.get("price", 0),
                "category": cat.get("name", ""),
                "address": a.get("address", ""),
                "avito_id": a.get("id"),
                "avito_url": a.get("url", ""),
                "images": [],
                "imported_from_avito": True,
            })
            existing.add(a.get("id"))
            added += 1
        raw = _json.dumps(items, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            db.add(Storage(account_id=account_id, key="active_avito_items", value=raw))
        db.commit()
        return {"status": "ok", "found_active": len(active), "added": added, "total_active": len(items)}
    finally:
        db.close()


class TextTemplateRequest(BaseModel):
    account_id: str
    name: str
    title_template: str
    description_template: str


@router.get("/text_templates")
def get_text_templates(account_id: str):
    """Библиотека текстовых шаблонов объявлений (готовые тексты для переиспользования)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "text_templates").first()
        templates = _json.loads(row.value) if row else []
        return {"status": "ok", "templates": templates}
    finally:
        db.close()


@router.post("/text_templates/create")
def create_text_template(req: TextTemplateRequest):
    """Создаёт новый текстовый шаблон в библиотеке."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json, time as _time
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "text_templates").first()
        templates = _json.loads(row.value) if row else []
        new_id = f"tpl-{int(_time.time()*1000)}"
        templates.append({
            "id": new_id,
            "name": req.name,
            "title_template": req.title_template,
            "description_template": req.description_template,
            "created_at": _time.strftime("%Y-%m-%d"),
        })
        raw = _json.dumps(templates, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            row = Storage(account_id=req.account_id, key="text_templates", value=raw)
            db.add(row)
        db.commit()
        return {"status": "ok", "id": new_id}
    finally:
        db.close()


class TextTemplateDeleteRequest(BaseModel):
    account_id: str
    template_id: str


@router.post("/text_templates/delete")
def delete_text_template(req: TextTemplateDeleteRequest):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "text_templates").first()
        if not row:
            return {"status": "error", "message": "нет шаблонов"}
        templates = _json.loads(row.value)
        templates = [t for t in templates if t["id"] != req.template_id]
        row.value = _json.dumps(templates, ensure_ascii=False)
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/text_templates/effectiveness")
def get_template_effectiveness(account_id: str):
    """Для каждого шаблона считает эффективность по объявлениям, которые его используют:
    суммарные просмотры/контакты (за 30 дней, из /items2) и статус 🟢/🟡/🔴."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "text_templates").first()
        templates = _json.loads(row.value) if row else []

        feed_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        feed_items = _json.loads(feed_row.value) if feed_row else []
        template_by_avito_id = {}
        for it in feed_items:
            tpl_id = it.get("template_id")
            avito_id = it.get("avito_id")
            if tpl_id and avito_id:
                template_by_avito_id[avito_id] = tpl_id
    finally:
        db.close()

    if not template_by_avito_id:
        return {"status": "ok", "templates": [
            {**t, "views": 0, "contacts": 0, "conversion": 0, "items_count": 0, "status": "no_data"} for t in templates
        ]}

    import requests as _requests
    from app.db.session import SessionLocal as _SL
    from app.models.account import Account
    db2 = _SL()
    try:
        acc = db2.query(Account).filter(Account.account_id == account_id).first()
        if not acc or not acc.avito_client_id:
            return {"status": "ok", "templates": [
                {**t, "views": 0, "contacts": 0, "conversion": 0, "items_count": 0, "status": "no_data"} for t in templates
            ]}
        from app.crypto_utils import decrypt_secret
        tok = _requests.post("https://api.avito.ru/token/", data={
            "grant_type": "client_credentials", "client_id": decrypt_secret(acc.avito_client_id), "client_secret": decrypt_secret(acc.avito_client_secret),
        }, timeout=20).json().get("access_token")
    finally:
        db2.close()

    from datetime import date as _date, timedelta as _timedelta
    date_to = _date.today().isoformat()
    date_from = (_date.today() - _timedelta(days=30)).isoformat()
    stats_by_avito_id = {}
    avito_ids = list(template_by_avito_id.keys())
    for i in range(0, len(avito_ids), 200):
        batch = avito_ids[i:i+200]
        try:
            r = _requests.post(
                f"https://api.avito.ru/stats/v1/accounts/{acc.avito_user_id}/items",
                headers={"Authorization": f"Bearer {tok}"},
                json={"dateFrom": date_from, "dateTo": date_to, "fields": ["uniqViews", "uniqContacts"], "itemIds": batch},
                timeout=20,
            )
            if r.status_code == 200:
                for it in r.json().get("result", {}).get("items", []):
                    v = c = 0
                    for s in it.get("stats", []):
                        v += s.get("uniqViews", 0)
                        c += s.get("uniqContacts", 0)
                    stats_by_avito_id[it["itemId"]] = {"views": v, "contacts": c}
        except Exception:
            pass

    tpl_agg = {t["id"]: {"views": 0, "contacts": 0, "items_count": 0} for t in templates}
    for avito_id, tpl_id in template_by_avito_id.items():
        s = stats_by_avito_id.get(avito_id, {"views": 0, "contacts": 0})
        if tpl_id in tpl_agg:
            tpl_agg[tpl_id]["views"] += s["views"]
            tpl_agg[tpl_id]["contacts"] += s["contacts"]
            tpl_agg[tpl_id]["items_count"] += 1

    result = []
    for t in templates:
        agg = tpl_agg.get(t["id"], {"views": 0, "contacts": 0, "items_count": 0})
        views, contacts = agg["views"], agg["contacts"]
        conversion = round(contacts / views * 100, 1) if views > 0 else 0
        if views < 50:
            status = "in_progress"
        elif views >= 100 and conversion < 1:
            status = "ineffective"
        elif conversion >= 3:
            status = "effective"
        else:
            status = "in_progress"
        result.append({**t, "views": views, "contacts": contacts, "conversion": conversion,
                       "items_count": agg["items_count"], "status": status})

    return {"status": "ok", "templates": result}


@router.get("/all_feeds")
def get_all_feeds(account_id: str = ""):
    """Сводка по фидам. Если account_id задан - только фиды этого аккаунта.
    Пустой account_id - все фиды (режим директора)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.models.account import Account
    import json as _json
    from collections import Counter
    db = SessionLocal()
    result = []
    try:
        if account_id:
            accounts = db.query(Account).filter(Account.account_id == account_id).all()
        else:
            accounts = db.query(Account).all()
        base = os.getenv("PUBLIC_BASE_URL", "https://boris-ai.pro")
        for acc in accounts:
            row = db.query(Storage).filter(Storage.account_id == acc.account_id, Storage.key == "feed_items").first()
            if not row:
                continue
            try:
                items = _json.loads(row.value)
            except Exception:
                items = []
            if not items:
                continue
            cities = Counter(it.get("address", "") for it in items if it.get("address"))
            cats = Counter(it.get("category", "") for it in items if it.get("category"))
            result.append({
                "account_id": acc.account_id,
                "name": acc.name or acc.account_id,
                "feed_url": f"{base}/api/avito/feed/{acc.account_id}.xml",
                "count": len(items),
                "cities": [c for c, _ in cities.most_common(8)],
                "cities_count": len(cities),
                "categories": [c for c, _ in cats.most_common(3)],
            })
        result.sort(key=lambda x: -x["count"])
        return {"status": "ok", "feeds": result}
    finally:
        db.close()


@router.get("/feed_items_list")
def get_feed_items_list(account_id: str = "otdushi"):
    """Возвращает сохранённые объявления Бориса (фид) для отображения в интерфейсе,
    независимо от того, обработал ли их уже Avito."""
    items = _load_feed_items(account_id)
    return {"status": "ok", "count": len(items), "items": items}


def _save_feed_items(account_id: str, items: list):
    """Сохраняет items фида в постоянное хранилище."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        raw = _json.dumps([it.model_dump() for it in items], ensure_ascii=False)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if row:
            row.value = raw
        else:
            row = Storage(account_id=account_id, key="feed_items", value=raw)
            db.add(row)
        db.commit()
    finally:
        db.close()

def _load_drafts(account_id: str) -> list:
    """Читает черновики объявлений (созданные, но ещё не опубликованные) из хранилища."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "drafts").first()
        if row:
            return _json.loads(row.value)
    except Exception:
        pass
    finally:
        db.close()
    return []

def _save_drafts(account_id: str, drafts: list):
    """Сохраняет черновики объявлений в хранилище."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        raw = _json.dumps(drafts, ensure_ascii=False)
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "drafts").first()
        if row:
            row.value = raw
        else:
            row = Storage(account_id=account_id, key="drafts", value=raw)
            db.add(row)
        db.commit()
    finally:
        db.close()

class SaveDraftsRequest(BaseModel):
    account_id: str
    drafts: list

@router.post("/drafts")
def save_drafts_endpoint(req: SaveDraftsRequest):
    """Сохраняет (перезаписывает) весь список черновиков аккаунта — используется оркестратором
    после генерации новой партии черновиков."""
    _save_drafts(req.account_id, req.drafts)
    return {"status": "ok", "total": len(req.drafts)}

@router.get("/drafts")
def get_drafts(account_id: str, batch_id: str = None):
    """Список черновиков объявлений. Если указан batch_id — только из этой партии
    (например, все 70 черновиков одной задачи «Книга жизни»)."""
    drafts = _load_drafts(account_id)
    if batch_id:
        drafts = [d for d in drafts if d.get("batch_id") == batch_id]
    return {"status": "ok", "drafts": drafts, "total": len(drafts)}

class DraftUpdateRequest(BaseModel):
    account_id: str
    title: str = None
    description: str = None
    price: float = None

@router.put("/drafts/{draft_id}")
def update_draft(draft_id: str, req: DraftUpdateRequest):
    """Редактирование черновика перед публикацией."""
    drafts = _load_drafts(req.account_id)
    found = False
    for d in drafts:
        if d.get("id") == draft_id:
            if req.title is not None:
                d["title"] = req.title
            if req.description is not None:
                d["description"] = req.description
            if req.price is not None:
                d["price"] = req.price
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="Черновик не найден")
    _save_drafts(req.account_id, drafts)
    return {"status": "ok"}

@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str, account_id: str):
    """Удаление одного черновика."""
    drafts = _load_drafts(account_id)
    new_drafts = [d for d in drafts if d.get("id") != draft_id]
    _save_drafts(account_id, new_drafts)
    return {"status": "ok", "deleted": len(drafts) - len(new_drafts)}

class PublishDraftsRequest(BaseModel):
    account_id: str
    draft_ids: list[str] = []  # пусто = опубликовать все черновики этого аккаунта
    confirmed: bool = False  # владелец уже видел предупреждение о несоответствии категории и подтвердил публикацию
    city: str = ""  # если задан — проставляется в address всех публикуемых объявлений
    draft_addresses: dict = {}  # id черновика -> адрес (раздача по районам/городам), приоритетнее city
    date_begin: str = ""  # дата+время отложенной публикации (Avito придержит до неё через тег DateBegin)
    timezone: str = ""  # часовой пояс публикации

class UpdateDraftImagesRequest(BaseModel):
    account_id: str
    draft_id: str
    images: list[str] = []

@router.post("/drafts/update_images")
def update_draft_images(req: UpdateDraftImagesRequest):
    """Обновляет список фото у одного черновика (удаление/добавление/переупорядочивание)."""
    drafts = _load_drafts(req.account_id)
    found = False
    for d in drafts:
        if d.get("id") == req.draft_id:
            d["images"] = req.images
            found = True
            break
    if not found:
        return {"status": "error", "message": "Черновик не найден"}
    _save_drafts(req.account_id, drafts)
    return {"status": "ok", "images_count": len(req.images)}

@router.post("/drafts/publish")
def publish_drafts(req: PublishDraftsRequest):
    """Публикует выбранные (или все) черновики — переносит их в реальный фид."""
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        return _publish_drafts_impl(req, db)
    finally:
        db.close()

def _draft_to_feed_item(d: dict) -> "FeedItem":
    return FeedItem(
        id=d.get("id"),
        title=d.get("title", ""),
        description=d.get("description", ""),
        price=d.get("price", 0),
        category=d.get("category", ""),
        service_type=d.get("service_type", ""),
        service_subtype=d.get("service_subtype", ""),
        work_experience=d.get("work_experience", ""),
        guarantee=d.get("guarantee", ""),
        goods_type=d.get("goods_type", ""),
        condition=d.get("condition", ""),
        kitchen_type=d.get("kitchen_type", ""),
        price_type=d.get("price_type", ""),
        color=d.get("color", ""),
        goods_subtype=d.get("goods_subtype", ""),
        ad_type=d.get("ad_type", ""),
        category_id=d.get("category_id", ""),
        address=d.get("address", "Москва"),
        images=d.get("images", []),
        params=d.get("params", {}),
        date_begin=d.get("date_begin", ""),
        date_end=d.get("date_end", ""),
        timezone=d.get("timezone", "")
    )


def _push_notification(account_id: str, text: str):
    """Кладёт запись в Storage 'notifications' - тот же формат, что и _run_report_back
    (tasks.py), чтобы существующий бейдж/карточка в чате Бориса на фронте подхватили её
    без каких-либо изменений на фронте (там уже общий поллинг GET /api/chat/notifications)."""
    from app.db.session import SessionLocal as _SL
    from app.models.storage import Storage as _StorageModel
    import json as _json_notif
    from datetime import datetime as _dt_notif

    db = _SL()
    try:
        row = db.query(_StorageModel).filter(_StorageModel.account_id == account_id, _StorageModel.key == "notifications").first()
        notifications = _json_notif.loads(row.value) if row else []
        notifications.append({
            "ts": _dt_notif.now().isoformat(timespec="seconds"),
            "text": text,
            "related_results": [],
            "read": False,
        })
        notifications = notifications[-50:]
        raw = _json_notif.dumps(notifications, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            db.add(_StorageModel(account_id=account_id, key="notifications", value=raw))
        db.commit()
    except Exception:
        pass  # уведомление необязательно для успешной публикации, не должно её ломать
    finally:
        db.close()


def check_category_content_match(drafts: list) -> list:
    """Один батч-запрос в GPT: для каждого объявления партии сверяет title/description с
    назначенной category - явные несоответствия (например 'Производство кухонь' в категории
    'Шкафы и буфеты') возвращаются списком, остальное считается нормой. НЕ блокирует публикацию
    само по себе - это мягкая подсказка владельцу, в отличие от жёсткого xmlcheck ниже.
    Возвращает [] если несоответствий не найдено или проверка не удалась (не блокируем публикацию
    из-за недоступности ИИ - это не критическая проверка формата)."""
    from gigachat_pool import chat_with_fallback
    from gigachat.models import Messages, MessagesRole
    import json as _json_cm
    import re as _re_cm

    if not drafts:
        return []

    lines = []
    for i, d in enumerate(drafts):
        desc_snippet = _re_cm.sub(r"<[^>]+>", " ", (d.get("description") or ""))[:200].strip()
        lines.append(f"{i}: категория=\"{d.get('category', '')}\" | заголовок=\"{d.get('title', '')}\" | описание=\"{desc_snippet}\"")

    prompt = (
        "Ниже список объявлений Avito с назначенной им категорией (индекс: категория | заголовок | описание).\n"
        "Проверь КАЖДОЕ: явно ли заголовок/описание НЕ соответствует назначенной категории "
        "(например категория 'Шкафы и буфеты', а речь о кухнях, или категория 'Услуги переезда', а "
        "текст про ремонт квартир). Не придирайся к мелочам и синонимам - только ЯВНЫЕ, однозначные "
        "несоответствия ниши товара/услуги категории.\n\n"
        + "\n".join(lines) +
        "\n\nВерни ТОЛЬКО чистый JSON-массив ТОЛЬКО спорных индексов (пустой массив, если всё в порядке):\n"
        '[{"index": 0, "reason": "коротко почему не подходит", "suggested_category": "предполагаемая правильная категория"}]'
    )

    try:
        raw = chat_with_fallback([Messages(role=MessagesRole.USER, content=prompt)], temperature=0.1, max_tokens=2000).strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            raw = raw[start:end + 1]
        mismatches = _json_cm.loads(raw)
        if not isinstance(mismatches, list):
            return []
    except Exception as e:
        print(f"[check_category_content_match] проверка не удалась (пропускаем, не блокируем публикацию): {e}", flush=True)
        return []

    result = []
    for m in mismatches:
        idx = m.get("index")
        if not isinstance(idx, int) or idx < 0 or idx >= len(drafts):
            continue
        d = drafts[idx]
        result.append({
            "id": d.get("id"),
            "title": d.get("title", ""),
            "category": d.get("category", ""),
            "reason": m.get("reason", ""),
            "suggested_category": m.get("suggested_category", ""),
        })
    return result


def _publish_drafts_impl(req: PublishDraftsRequest, db):
    drafts = _load_drafts(req.account_id)
    to_publish = [d for d in drafts if not req.draft_ids or d.get("id") in req.draft_ids]
    remaining = [d for d in drafts if req.draft_ids and d.get("id") not in req.draft_ids]

    if not to_publish:
        return {"status": "ok", "published": 0}

    # МЯГКАЯ ПРОВЕРКА СООТВЕТСТВИЯ КАТЕГОРИИ СОДЕРЖАНИЮ - до xmlcheck. Не критическая ошибка
    # формата (xmlcheck такое не ловит - категория технически валидна, просто не та по смыслу),
    # поэтому НЕ отменяет публикацию сама, а требует явного подтверждения владельца один раз
    # (инцидент: аккаунту Москва по аналогии с Пензой применили "Шкафы и буфеты", хотя там
    # "Производство кухонь" - категория была валидной строкой, но неверной по содержанию).
    if not req.confirmed:
        mismatches = check_category_content_match(to_publish)
        if mismatches:
            return {
                "status": "needs_confirmation",
                "message": f"Борис заметил возможное несоответствие категории содержанию у {len(mismatches)} из {len(to_publish)} объявлений - проверьте перед публикацией.",
                "mismatches": mismatches,
            }

    # ОБЯЗАТЕЛЬНАЯ ВАЛИДАЦИЯ ФИДА ПЕРЕД ПУБЛИКАЦИЕЙ: раньше ошибки формата (например,
    # отсутствующие обязательные ServiceType/ServiceSubtype) вылезали ПОСЛЕ того, как
    # объявления уже улетели на Avito - чинили постфактум вручную (инцидент "Книга жизни",
    # 22 черновика; аккаунт Георгия унаследовал чужие поля). Гоняем именно ЭТУ партию через
    # официальный валидатор Avito ДО того, как что-либо сохранится в фид - через
    # feed_preview, который ничего не публикует и не сохраняет.
    import asyncio as _asyncio_publish
    from app.api.parser import validate_feed_xmlcheck

    ids_param = ",".join(d.get("id") for d in to_publish if d.get("id"))
    preview_url = f"https://boris-ai.pro/api/avito/feed_preview/{req.account_id}.xml?ids={ids_param}"
    try:
        validation = _asyncio_publish.run(validate_feed_xmlcheck(preview_url))
    except Exception as e:
        validation = {"status": "error", "errors": [str(e)], "raw_text": ""}

    if validation.get("status") != "ok":
        titles = [d.get("title", "без названия") for d in to_publish]
        raw_lines = [l.strip() for l in (validation.get("raw_text") or "").split("\n") if l.strip()]
        noise = {"Отчёт о проверке", "Общий статус", "По ссылке", "Проверить", "XML-фид"}
        readable_errors = [l for l in raw_lines if l not in noise and len(l) > 3][:50]
        if validation.get("status") == "errors":
            message = f"Фид не прошёл проверку официального валидатора Avito - публикация {len(to_publish)} объявлений отменена."
        else:
            message = "Не удалось проверить фид валидатором Avito (валидатор недоступен или не ответил вовремя) - публикация отменена, чтобы не публиковать вслепую."
        _audit_log(req.account_id, "publish_validation_failed",
                   f"{message} Статус валидатора: {validation.get('status')}. Объявления: {', '.join(titles)[:300]}",
                   actor="boris")
        _push_notification(req.account_id, f"⚠️ {message}")
        return {
            "status": "validation_failed",
            "message": message,
            "validation_status": validation.get("status"),
            "items": titles,
            "errors": readable_errors or validation.get("errors") or ["Валидатор не вернул подробностей"],
        }

    _audit_log(req.account_id, "publish_validation_ok",
               f"Фид проверен валидатором Avito перед публикацией ({len(to_publish)} объявлений) - ошибок формата не найдено",
               actor="boris")

    existing_feed = _load_feed_items(req.account_id)
    # адреса публикации: draft_addresses (раздача по районам/городам) приоритетнее общего city
    _addr_map = getattr(req, "draft_addresses", {}) or {}
    _dbegin = getattr(req, "date_begin", "")
    for _d in to_publish:
        _aid = _d.get("id")
        if _aid in _addr_map and _addr_map[_aid]:
            _d["address"] = _addr_map[_aid]
        elif getattr(req, "city", ""):
            _d["address"] = req.city
        if _dbegin:
            _d["date_begin"] = _dbegin  # Avito отложит публикацию до этой даты
        if getattr(req, "timezone", ""):
            _d["timezone"] = req.timezone
    new_feed_items = [_draft_to_feed_item(d) for d in to_publish]

    _save_feed_items(req.account_id, existing_feed + new_feed_items)
    _save_drafts(req.account_id, remaining)

    # Сохраняем связку item_id -> A/B-вариант отдельно от фида (в XML/params это
    # попадать не должно - там только официальные теги Avito-шаблона). Нужно,
    # чтобы через несколько дней можно было сравнить конверсию authored vs ai_generated.
    try:
        from app.models.storage import Storage as _StorageModelAB
        import json as _json_ab
        import datetime as _dt_ab

        ab_row = db.query(_StorageModelAB).filter(
            _StorageModelAB.account_id == req.account_id, _StorageModelAB.key == "ab_test_links"
        ).first()
        ab_links = _json_ab.loads(ab_row.value) if ab_row else {}

        for d in to_publish:
            if d.get("variant") and d.get("batch_id"):
                ab_links[d.get("id")] = {
                    "variant": d.get("variant"),
                    "batch_id": d.get("batch_id"),
                    "batch_label": d.get("batch_label", ""),
                    "published_at": _dt_ab.datetime.utcnow().isoformat()
                }

        if ab_links:
            if ab_row:
                ab_row.value = _json_ab.dumps(ab_links, ensure_ascii=False)
            else:
                ab_row = _StorageModelAB(account_id=req.account_id, key="ab_test_links", value=_json_ab.dumps(ab_links, ensure_ascii=False))
                db.add(ab_row)
            db.commit()
    except Exception:
        pass  # A/B-связка необязательна для успешной публикации, не должна её ломать

    _push_notification(req.account_id, f"✅ Фид опубликован: {len(to_publish)} объявлений, 0 ошибок.")
    return {"status": "ok", "published": len(to_publish)}


def _client_money(account_id: str, db) -> dict:
    """Сколько клиент заплатил всего и когда последний раз."""
    import json as _json
    from app.models.storage import Storage
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id,
                                       Storage.key == "payments_history").first()
        hist = _json.loads(row.value) if row else []
    except Exception:
        hist = []
    total = sum(h.get("amount_rub", 0) for h in hist)
    last = hist[-1] if hist else None
    return {"total_rub": total, "payments_count": len(hist),
            "last_at": (last or {}).get("at"), "last_title": (last or {}).get("title")}


def apply_banner_to_batch_impl(account_id: str, batch_label: str, banner_count: int = 5, photos_per_ad: int = 10, folder: str = "", on_progress=None) -> dict:
    """Генерирует N рекламных баннеров под партию черновиков (batch_label) и расставляет фото
    у каждого черновика партии: баннер первым, дальше обычные фото по кругу из указанной папки.
    Общая реализация - используется и прямым эндпоинтом /apply_banner_to_batch, и GPT-экшеном
    apply_banner_to_batch в plan_items.py, чтобы не дублировать логику в двух местах."""
    import random as _rnd_banner_batch
    from app.api.banners import create_full_ai_banner, FullAiRequest, get_banner_showcase

    banner_count = max(int(banner_count), 1)
    photos_per_ad = max(int(photos_per_ad), 1)

    all_drafts = _load_drafts(account_id)
    target = [d for d in all_drafts if d.get("batch_label") == batch_label]
    other = [d for d in all_drafts if d.get("batch_label") != batch_label]

    if not target:
        return {"status": "error", "message": f"Партия '{batch_label}' не найдена среди черновиков"}

    raw_description = (target[0].get("title", "") + ". " + target[0].get("description", ""))[:400]

    showcase_urls = []
    try:
        sc = get_banner_showcase(account_id)
        showcase_urls = [it.get("url") for it in sc.get("showcase", []) if it.get("url")]
    except Exception:
        pass

    accent_palette = ["#F79009", "#2F6FED", "#7C5CFC", "#F04438", "#12B76A", "#EE46BC", "#0EA5E9", "#EAB308"]

    # БИЛЛИНГ: ограничиваем число баннеров остатком лимита тарифа
    banner_notice = None
    try:
        from app.api.billing import get_status as _bill_status_b
        stb = _bill_status_b(account_id)
        if not stb.get("unlimited"):
            rem_banners = stb["usage"].get("banners", {}).get("remaining", 0)
            if banner_count > rem_banners:
                if rem_banners <= 0:
                    banner_notice = "Лимит баннеров на тарифе исчерпан (0 осталось) — баннеры не создавались."
                    banner_count = 0
                else:
                    banner_notice = f"Лимит баннеров почти исчерпан: создаём {rem_banners} из {banner_count} запрошенных."
                    banner_count = rem_banners
    except Exception as e:
        print(f"[billing] banners check failed (пропускаем): {e}", flush=True)

    banner_urls = []
    for bi in range(banner_count):
        try:
            refs = _rnd_banner_batch.sample(showcase_urls, min(3, len(showcase_urls))) if showcase_urls else []
            color = accent_palette[bi % len(accent_palette)]
            b_data = create_full_ai_banner(FullAiRequest(
                account_id=account_id, raw_description=raw_description, format="infographic",
                accent_color=color, reference_image_urls=refs
            ))
            if b_data.get("status") == "ok" and b_data.get("url"):
                banner_urls.append(b_data.get("url"))
        except Exception:
            pass
        if on_progress:
            try:
                on_progress(bi + 1, banner_count)
            except Exception:
                pass

    if banner_urls:
        try:
            from app.api.billing import check_and_consume as _bill_consume_b
            _bill_consume_b(account_id, "banners", len(banner_urls))
        except Exception:
            pass

    if not banner_urls:
        return {"status": "error", "message": f"Не удалось сгенерировать ни одного баннера для партии '{batch_label}'", "notice": banner_notice}

    photo_urls = []
    if folder:
        photo_urls = images_list(account_id).get("folders", {}).get(folder, [])

    updated = []
    for di, d in enumerate(target):
        banner_url = banner_urls[di % len(banner_urls)]
        regular_needed = max(photos_per_ad - 1, 0)
        if photo_urls:
            pool = list(photo_urls)
            _rnd_banner_batch.shuffle(pool)
            regulars = [pool[j % len(pool)] for j in range(regular_needed)]
        else:
            regulars = d.get("images", [])[:regular_needed]
        d = dict(d)
        d["images"] = [banner_url] + regulars
        updated.append(d)

    _save_drafts(account_id, other + updated)
    return {"status": "ok", "updated": len(updated), "banners_generated": len(banner_urls), "batch_label": batch_label, "notice": banner_notice}


class ApplyBannerToBatchRequest(BaseModel):
    account_id: str
    batch_label: str
    banner_count: int = 5
    photos_per_ad: int = 10
    folder: str = ""

@router.post("/apply_banner_to_batch")
def apply_banner_to_batch_endpoint(req: ApplyBannerToBatchRequest):
    """Прямой вызов - генерирует баннеры и расставляет фото по партии черновиков (см. apply_banner_to_batch_impl)."""
    return apply_banner_to_batch_impl(req.account_id, req.batch_label, req.banner_count, req.photos_per_ad, req.folder)


@router.post("/generate_feed")

def _normalize_category(raw_category, niche=None, template_id=None):
    """Возвращает валидное для Avito имя категории <Category> — 2-й сегмент пути из дерева.
    Avito <Category> = подкатегория уровня "Ремонт и строительство" (как у боевых объявлений).
    Источник истины — CategoryTreeLeaf.path, связь по template_id шаблона (надёжнее чем ilike)."""
    from app.db.session import SessionLocal as _SL
    from app.models.category_template import CategoryTreeLeaf as _Leaf, CategoryTemplate as _CT
    _db = _SL()
    try:
        leaf = None
        # 1) если знаем template_id — прямая связь с деревом (точно)
        if template_id:
            leaf = _db.query(_Leaf).filter(_Leaf.template_id == str(template_id)).first()
        # 2) иначе через шаблон по нише → его template_id → дерево
        if not leaf and niche:
            tpl = _db.query(_CT).filter(_CT.category_id.ilike("%" + str(niche).strip() + "%")).first()
            if not tpl:
                tpl = _db.query(_CT).filter(_CT.category_name.ilike("%" + str(niche).strip() + "%")).first()
            if tpl and tpl.template_id:
                leaf = _db.query(_Leaf).filter(_Leaf.template_id == str(tpl.template_id)).first()
                # если у самого шаблона путь полный — используем его
                if not leaf and tpl.category_name and ">" in tpl.category_name:
                    parts = [x.strip() for x in tpl.category_name.split(">")]
                    if len(parts) >= 2:
                        return parts[1]
        if leaf and leaf.path:
            parts = [x.strip() for x in leaf.path.split(">")]
            if len(parts) >= 2:
                return parts[1]
            return leaf.top_level
        # если сырая категория уже валидный путь — берём 2-й сегмент
        if raw_category and ">" in str(raw_category):
            parts = [x.strip() for x in str(raw_category).split(">")]
            if len(parts) >= 2:
                return parts[1]
        return raw_category
    finally:
        _db.close()


def generate_feed(req: GenerateFeedRequest):
    """Сохраняет items фида в БД. НЕ строит сам XML здесь - это делает отдельный
    GET /feed/{account_id}.xml, вызываемый только когда Avito реально запрашивает файл.
    Раньше здесь дублировался тяжёлый цикл с uniquify_image() для КАЖДОГО фото КАЖДОГО
    объявления впустую (результат просто отбрасывался) - на аккаунтах с 1000+ объявлениями
    это занимало почти 2 минуты на каждое сохранение, вызывая ложные таймауты в publish_listings."""
    if getattr(req, "merge", False):
        existing = _load_feed_items(req.account_id)
        existing_ids = {it.id for it in existing}
        combined = existing + [it for it in req.items if it.id not in existing_ids]
        req = type(req)(account_id=req.account_id, items=combined, merge=False)
    _save_feed_items(req.account_id, req.items)

    return {
        "status": "ok",
        "items_count": len(req.items),
        "feed_url": f"https://boris-ai.pro/api/avito/feed/{req.account_id}.xml"
    }

def _build_xml(items: list) -> str:
    xml_parts = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml_parts.append('<Ads formatVersion="3" target="Avito.ru">')
    for item in items:
        title = html.escape(spin(item.title))[:100]
        description = spin(item.description)
        address = html.escape(spin(item.address))[:256]
        xml_parts.append('  <Ad>')
        xml_parts.append(f'    <Id>{html.escape(item.id)}</Id>')
        xml_parts.append(f'    <Title>{title}</Title>')
        xml_parts.append(f'    <Description><![CDATA[{description}]]></Description>')
        if item.price > 0:
            xml_parts.append(f'    <Price>{item.price}</Price>')
        if item.date_begin:
            xml_parts.append(f'    <DateBegin>{html.escape(item.date_begin)}</DateBegin>')
        if getattr(item, "timezone", ""):
            xml_parts.append(f'    <Timezone>{html.escape(item.timezone)}</Timezone>')
        if item.date_end:
            xml_parts.append(f'    <DateEnd>{html.escape(item.date_end)}</DateEnd>')
        try:
            _cat_feed = _cat_for_feed(item) or item.category
        except Exception:
            _cat_feed = item.category
        xml_parts.append(f'    <Category>{html.escape(_cat_feed)}</Category>')
        if item.service_type:
            xml_parts.append(f'    <ServiceType>{html.escape(item.service_type)}</ServiceType>')
        if item.service_subtype:
            xml_parts.append(f'    <ServiceSubtype>{html.escape(item.service_subtype)}</ServiceSubtype>')
        if item.work_experience:
            xml_parts.append(f'    <WorkExperience>{html.escape(str(item.work_experience))}</WorkExperience>')
        if item.guarantee:
            xml_parts.append(f'    <Guarantee>{html.escape(item.guarantee)}</Guarantee>')
        if item.goods_type:
            xml_parts.append(f'    <GoodsType>{html.escape(item.goods_type)}</GoodsType>')
        if item.goods_subtype:
            xml_parts.append(f'    <GoodsSubType>{html.escape(item.goods_subtype)}</GoodsSubType>')
        if item.ad_type:
            xml_parts.append(f'    <AdType>{html.escape(item.ad_type)}</AdType>')
        if item.condition:
            xml_parts.append(f'    <Condition>{html.escape(item.condition)}</Condition>')
        if item.kitchen_type:
            xml_parts.append(f'    <KitchenType>{html.escape(item.kitchen_type)}</KitchenType>')
        if item.price_type:
            xml_parts.append(f'    <PriceType>{html.escape(item.price_type)}</PriceType>')
        if item.color:
            xml_parts.append(f'    <Color>{html.escape(item.color)}</Color>')
        if item.price_type:
            xml_parts.append(f'    <PriceType>{html.escape(item.price_type)}</PriceType>')
        xml_parts.append(f'    <Address>{address}</Address>')
        if item.phone:
            xml_parts.append(f'    <ContactPhone>{html.escape(item.phone)}</ContactPhone>')
        if item.manager:
            xml_parts.append(f'    <ManagerName>{html.escape(item.manager)}</ManagerName>')
        xml_parts.append('    <ListingFee>PackageSingle</ListingFee>')
        for k, v in item.params.items():
            xml_parts.append(f'    <{k}>{html.escape(str(v))}</{k}>')
        if item.images:
            xml_parts.append('    <Images>')
            for img in item.images:
                _final_img = img
                try:
                    _rel = img.split("/images/")[-1]
                    _local_path = os.path.join(IMAGES_DIR, _rel)
                    if os.path.isfile(_local_path):
                        _new_name = uniquify_image(_local_path)
                        _folder = os.path.dirname(_rel)
                        _final_img = f"https://boris-ai.pro/images/{_folder}/{_new_name}" if _folder else f"https://boris-ai.pro/images/{_new_name}"
                except Exception:
                    _final_img = img
                xml_parts.append(f'      <Image url="{html.escape(_final_img)}"/>')
            xml_parts.append('    </Images>')
        xml_parts.append('  </Ad>')
    xml_parts.append('</Ads>')
    return "\n".join(xml_parts)

def _check_feed_images(account_id: str) -> dict:
    """Проверяет, что каждый локальный файл (/images/...), на который ссылается фид, реально
    существует на диске - та же логика маппинга путей, что уже в _build_xml. Внешние URL
    (не наши /images/...) не проверяем - это не наши файлы, их существование не в нашей власти.
    Без побочных эффектов - используется и стоп-краном в get_feed, и /feed_health для кнопки на фронте."""
    items = _load_feed_items(account_id)
    total_images = 0
    broken = []
    for item in items:
        for img in (item.images or []):
            if "/images/" not in img:
                continue
            total_images += 1
            rel = img.split("/images/")[-1]
            local_path = os.path.join(IMAGES_DIR, rel)
            if not os.path.isfile(local_path):
                broken.append(img)
    return {"ok": len(broken) == 0, "total_images": total_images, "broken": broken}


@router.get("/feed_health/{account_id}")
def feed_health(account_id: str):
    """Только чтение, без побочных эффектов - для кнопки 'Проверить фид' на фронте."""
    return _check_feed_images(account_id)


@public_router.get("/feed/{account_id}.xml")
def get_feed(account_id: str):
    health = _check_feed_images(account_id)
    if not health["ok"]:
        import json as _json_fh
        broken = health["broken"]
        total_broken = len(broken)
        _audit_log(account_id, "feed_blocked", _json_fh.dumps({"broken": broken[:20], "total_broken": total_broken}, ensure_ascii=False))
        try:
            from app.telegram_bot import send_telegram_message
            chat_id = os.environ.get("DIRECTOR_CHAT_ID")
            if chat_id:
                send_telegram_message(chat_id,
                    f"⛔ Фид {account_id} заблокирован: {total_broken} битых фото. "
                    f"Объявления не пострадали, но выгрузка остановлена.")
        except Exception:
            pass
        return Response(
            content=f"Фид заблокирован: {total_broken} битых изображений на диске. Объявления не пострадали, выгрузка остановлена до починки.",
            status_code=503, media_type="text/plain"
        )
    items = _load_feed_items(account_id)
    xml = _build_xml(items) if items else '<?xml version="1.0" encoding="UTF-8"?>\n<Ads formatVersion="3" target="Avito.ru"></Ads>'
    return Response(content=xml, media_type="application/xml")

@public_router.get("/feed_preview/{account_id}.xml")
def get_feed_preview(account_id: str, batch_label: str = None, ids: str = None):
    """XML только партии черновиков (batch_label ИЛИ явный список id), БЕЗ публикации - чтобы
    xmlcheck можно было прогнать ДО реального добавления в фид, а не после. `ids` (через запятую)
    нужен для publish_drafts, где публикуется произвольный набор draft_ids, не всегда целая партия.
    Ничего не сохраняет и не публикует."""
    drafts = _load_drafts(account_id)
    if ids:
        id_set = set(ids.split(","))
        target = [d for d in drafts if d.get("id") in id_set]
    else:
        target = [d for d in drafts if d.get("batch_label") == batch_label]
    items = [_draft_to_feed_item(d) for d in target]
    xml = _build_xml(items) if items else '<?xml version="1.0" encoding="UTF-8"?>\n<Ads formatVersion="3" target="Avito.ru"></Ads>'
    return Response(content=xml, media_type="application/xml")

@router.post("/preview_spin")
def preview_spin(data: dict):
    text = data.get("text", "")
    return {"variants": [spin(text) for _ in range(3)]}

# ==== Генератор объявлений через ИИ (GigaChat) ====
from gigachat_pool import RateLimitedGigaChat as GigaChat, chat_with_fallback
from gigachat.models import Chat, Messages, MessagesRole
import json

class GenerateAdsRequest(BaseModel):
    topic: str
    sample: str = ""
    count: int = 10
    price_from: int = 0
    price_to: int = 0
    extra: str = ""
    goal: str = "call"
    use_my_ads: bool = False
    length: str = "medium"
    keywords: str = ""
    authored_texts: list[str] = []
    bold_level: str = "medium"
    account_id: str = "otdushi"

# новый промпт-строитель по структуре Кирилла
# промпт-строитель по структуре Кирилла (v2: жир, списки, чистые блоки)
# промпт-строитель по структуре Кирилла (v2: жир, списки, чистые блоки)
def _fix_title_len(title: str, limit: int = 45) -> str:
    """Выкидывает из спинтакс-группы заголовка варианты длиннее limit символов."""
    import re as _r
    t = str(title or "").strip()
    m = _r.search(r"\{([^{}]+)\}", t)
    if not m:
        return t if len(t) <= limit else (t[:limit].rsplit(" ", 1)[0] or t[:limit])
    variants = [v.strip() for v in m.group(1).split("|") if v.strip()]
    good, seen = [], set()
    for v in variants:
        if len(v) <= limit and v.lower() not in seen:
            good.append(v)
            seen.add(v.lower())
    if not good:
        short = min(variants, key=len)
        good = [short[:limit].rsplit(" ", 1)[0] or short[:limit]]
    return t[:m.start()] + "{" + "|".join(good) + "}" + t[m.end():]


def build_prompt(req, price_hint, length_text, goal_text, my_ads_text):
    _smp = (getattr(req, "sample", "") or "").strip()
    sample_block = ""
    if _smp:
        sample_block = (
            "ОБРАЗЕЦ ОТ КЛИЕНТА — бери отсюда ОБЩУЮ информацию о компании (условия, доставка, гарантии, "
            "преимущества), а также манеру письма и структуру. КОНКРЕТИКУ ПО ТОВАРУ (название, характеристики, "
            "ключевые слова) — придумывай СВОЮ под каждое объявление, НЕ копируй образец дословно:\n"
            + _smp[:4000] + "\n"
        )

    bl = getattr(req, "bold_level", "medium")
    bold_block = {
        "low": "ЖИРНЫЙ <strong>: МИНИМУМ — только цену и ОДИН главный призыв. 2 вставки на объявление.",
        "medium": "ЖИРНЫЙ <strong>: ОБЯЗАТЕЛЬНО выделяй заголовки смысловых блоков, призывы к действию и цену. Примерно 5-7 вставок по 2-4 слова.",
        "high": "ЖИРНЫЙ <strong>: МНОГО — заголовки блоков, ВСЕ призывы, цену И ключевые преимущества/выгоды. 9-12 вставок по 2-4 слова.",
    }.get(bl, "ЖИРНЫЙ <strong>: заголовки блоков, призывы и цену, 5-7 вставок.")

    if req.keywords.strip():
        kw_block = f"КЛЮЧЕВЫЕ СЛОВА КЛИЕНТА (вставь дословно И добавь 40-60 своих релевантных): {req.keywords}"
    else:
        kw_block = "Собери 50-80 релевантных ключевых фраз по теме (что ищут в поиске Авито)."

    authored = [t.strip() for t in (req.authored_texts or []) if t and t.strip()]
    if authored:
        texts_joined = "\n\n---СЛЕДУЮЩИЙ ТЕКСТ---\n\n".join(authored)
        mode_block = f"""РЕЖИМ: У КЛИЕНТА ЕСТЬ ГОТОВЫЕ АВТОРСКИЕ ТЕКСТЫ. Их РОВНО {len(authored)} — сделай РОВНО {len(authored)} объявлений, по одному на текст, по порядку.
КРИТИЧЕСКИ: авторский текст вставь в блок с эмодзи 📝 ДОСЛОВНО, символ в символ. НЕ меняй, НЕ сокращай, НЕ добавляй внутрь него спинтакс или <strong>. Обёртку строй ВОКРУГ.
АВТОРСКИЕ ТЕКСТЫ (разделены ---СЛЕДУЮЩИЙ ТЕКСТ---):
{texts_joined}"""
        count_line = f"Сделай РОВНО {len(authored)} объявлений."
    else:
        mode_block = "РЕЖИМ: авторских текстов нет — напиши сам. В блок 📝 дай короткий убедительный пример под тему."
        count_line = f"Сгенерируй {req.count} объявлений."

    prompt = f"""Ты — сильный копирайтер Авито. {count_line}
Тема: "{req.topic}".
{price_hint}
О компании/услугах/акциях (вплети в каждое): {req.extra}

{goal_text}

{my_ads_text}

{mode_block}

СТРУКТУРА (держись каркаса):
1. Абзац-обещание: что делаешь + поводы + срок + призыв.
2. Три вопроса-крючка подряд в боль клиента.
3. Обещание результата.
4. Строка "📝 Пример моей работы:" и ниже текст (авторский дословно либо пример).
5. Поводы/ситуации — ДЛИННЫЙ конкретный список, ОБЯЗАТЕЛЬНО тегами <ul><li>повод</li><li>повод</li></ul>.
6. Строка "✅ Что учитываю:" и список <ul><li>...</li></ul>.
7. Строка "🎁 Вы получите:" и список <ul><li>...</li></ul>.
8. Цена: <strong>от N ₽</strong> + короткий эмоциональный абзац.
9. Финальный призыв с мягкой срочностью 🔥.
10. Последний абзац — просто перечень ключевых фраз через запятую (БЕЗ слов "SEO" или "хвост", это обычный текст).
11. САМАЯ последняя строка блока уникализации: сначала слово-метка (без кавычек, с двоеточием), затем от 3 до 5 групп {{в1|в2|...|в10}} — В КАЖДОЙ ГРУППЕ МИНИМУМ 10 вариантов через |. Никогда не делай меньше 10 вариантов в группе и не пропускай ни одну группу.
ПРИМЕР готовой строки целиком (структуру именно такую и делай, просто со своими значениями): "Пример товара: Цвет: {{РАЛ 5010|РАЛ 9016|РАЛ 3020|РАЛ 6005|РАЛ 7016|РАЛ 1015|РАЛ 8017|РАЛ 9005|РАЛ 5015|РАЛ 3005}} Размер: {{300х300х30|400х400х40|200х100х60|500х500х50|350х350х35|250х250х25|450х450х45|600х300х40|150х150х20|100х100х15}}"
ВАЖНО: перед КАЖДОЙ группой {{}} ставь короткую подпись с двоеточием, что это за параметр (Цвет:, Размер:, Материал:, Повод:, Для кого:, Объём: и т.п. — подбери по смыслу).
Слово-метку выбери по смыслу темы (пиши ИМЕННО его, не слово "НАЗВАНИЕ"):
- услуга/работа руками (стихи, ремонт, уборка, репетиторство) → "Пример услуги:"
- физический товар для продажи (одежда, мебель, техника) → "Пример товара:"
- транспорт (машина, мото) → "Пример авто:"
- квартира → "Пример квартиры:"
- дом/дача → "Пример дома:"
- вакансия/работа → "Пример вакансии:"
ПАРАМЕТРЫ подбирай КОНКРЕТНО под тему, реалистично, как в настоящей смете/спецификации:
- для услуг: повод/для кого/объём (символы, предложения) — и, если уместно, срок/материал.
- для товаров/стройматериалов: ЦВЕТ (реальные коды RAL, например РАЛ 5010, РАЛ 9016 — минимум 10 разных), РАЗМЕР (реальные размеры в мм/см, например "300х300х30", "400х400х40" — минимум 10 вариантов), материал, комплектация.
- для авто/мото/велотехники: год, комплектация, цвет, пробег — НИКОГДА не варьируй марку/модель/бренд.
- для недвижимости: площадь, этаж, тип ремонта, район.
Каждый параметр — минимум 10 РЕАЛЬНЫХ, разных, правдоподобных значений, не выдумывай абстрактные плейсхолдеры типа "вариант1".

КРИТИЧЕСКИ ВАЖНЫЙ ЗАПРЕТ: если товар — конкретная модель конкретного производителя (например "LIMING Monster", "iPhone 15", "Toyota Camry"), НИКОГДА не создавай спинтакс-группу для марки/бренда/модели с ДРУГИМИ реальными производителями/брендами (нельзя {{LIMING|Yamaha|Xiaomi|KTM...}}) — это фактически недостоверная реклама (товар выдаётся за чужой бренд). Марка и модель у конкретного товара ФИКСИРОВАНЫ и не варьируются спинтаксом вообще. Варьировать спинтаксом можно только объективно переменные характеристики самого товара (цвет, размер, комплектация, год выпуска, состояние) — никогда не саму identity/бренд товара.

{kw_block}

ЗАГОЛОВОК: спинтакс МИНИМУМ из 10 РАЗНЫХ вариантов {{в1|в2|в3|в4|в5|в6|в7|в8|в9|в10}}, каждый 3-5 слов, БЕЗ знаков препинания.
Варианты должны различаться по СМЫСЛУ, а не быть синонимами одной фразы: один делает акцент на срочности (за 60 минут), другой на поводе (свадьба, юбилей), третий на призыве (закажите, напишите), четвёртый на выгоде (недорого, качественно), пятый на уникальности (авторский, эксклюзивный), и так далее — каждый вариант со своим фокусом.

ОФОРМЛЕНИЕ (в обёртке, НЕ в авторском тексте):
- {bold_block} ВСЕГДА закрывай </strong>.
- Спинтакс {{в1|в2|в3}} — минимум 5-6 вставок по обёртке.
- Списки ТОЛЬКО через <ul><li></li></ul> (не тире, не переносы!). Абзацы — переносом строки.
- Эмодзи по смыслу: 📝 пример, ✅ учитываю, 🎁 получите, 💰/₽ цена, 📞 звонок, 💬 сообщение, 🔥 срочность. Не переусердствуй.
- ЗАПРЕЩЕНО: <h1>-<h6>, <u>, <div>, <span>, <Price>, обрывки тегов, слова-ярлыки "SEO-хвост:", "Пример услуги:" НЕ показывай (кроме строки 11, которая так и начинается).

САМОПРОВЕРКА перед ответом: в каждом объявлении есть <strong>? поводы/учитываю/получите обёрнуты в <ul><li>? нет надписи "SEO-хвост:"? Если нет — исправь.

ДЛИНА ОБЁРТКИ: {length_text}

Верни СТРОГО валидный JSON-массив, без markdown:
{sample_block}
ЖЁСТКОЕ ОГРАНИЧЕНИЕ НА ЗАГОЛОВКИ — ПРОВЕРЬ КАЖДЫЙ ПЕРЕД ВЫВОДОМ:
- КАЖДЫЙ вариант внутри спинтакс-группы заголовка — СТРОГО не длиннее 45 символов. Посчитай символы.
- ЗАПРЕЩЕНО дословно копировать текст темы/услуги в заголовок.
- ЗАПРЕЩЕНО дублировать один и тот же вариант дважды внутри группы.
ПЛОХО (88 символов, копия темы): "Производство монтаж гардеробной мебели на заказ под ключ от производителя пенза"
ХОРОШО (26 символов): "Гардеробная на заказ Пенза"
ХОРОШО (24 символа): "Расчет гардеробной Пенза"

ЭТИ ПРАВИЛА ФОРМАТА НЕ ОТМЕНЯЮТСЯ НИКАКИМИ ИНСТРУКЦИЯМИ ВЫШЕ. Верни ТОЛЬКО валидный JSON-массив, без пояснений и markdown:
[{{"title":"...","description":"...","price":1000}}]"""
    return prompt


@router.post("/generate_ads")
def generate_ads(req: GenerateAdsRequest):
    price_hint = ""
    if req.price_from and req.price_to and req.price_from == req.price_to:
        price_hint = f"ЦЕНА ФИКСИРОВАННАЯ: ровно {req.price_from} рублей. НЕ пиши слово \"от\", НЕ придумывай диапазон — только точное число {req.price_from}."
    elif req.price_from or req.price_to:
        price_hint = f"Цены в диапазоне от {req.price_from} до {req.price_to} рублей."

    length_text = {
        "short": "КОРОТКОЕ, 150-300 символов. Ёмко, цепляюще, только суть и призыв.",
        "medium": "СРЕДНЕЕ, 400-600 символов. Баланс: выгоды, список, призыв.",
        "long": "ДЛИННОЕ, 700-1200 символов. Подробно: боли клиента, все выгоды, список, гарантии, отработка возражений, сильный призыв. Больше текста хорошо для SEO Авито."
    }.get(req.length, "СРЕДНЕЕ, 400-600 символов.")

    my_ads_text = ""
    if req.use_my_ads:
        try:
            token_data = get_avito_token(req.account_id)
            token = _extract_token(token_data)
            r = httpx.get("https://api.avito.ru/core/v1/items",
                headers={"Authorization": f"Bearer {token}"},
                params={"per_page": 5, "page": 1}, timeout=20)
            titles = [it.get("title", "") for it in r.json().get("resources", [])[:5] if it.get("title")]
            if titles:
                my_ads_text = "СТИЛЬ КЛИЕНТА — вот реальные заголовки его активных объявлений, пиши в похожей манере и лексике, но НЕ копируй дословно:\n" + "\n".join(f"- {t}" for t in titles)
        except Exception:
            my_ads_text = ""

    goal_text = {
        "call": "ГЛАВНАЯ ЦЕЛЬ — чтобы клиент ПОЗВОНИЛ. Призыв: позвоните прямо сейчас, звоните, наберите. Упор на срочность и живое общение.",
        "message": "ГЛАВНАЯ ЦЕЛЬ — чтобы клиент НАПИСАЛ в чат. Призыв: напишите нам, задайте вопрос в сообщении, ответим за 5 минут. Лёгкий первый шаг.",
        "order": "ГЛАВНАЯ ЦЕЛЬ — чтобы клиент СРАЗУ ЗАКАЗАЛ. Призыв: оформите заказ, закажите сейчас. Упор на выгоду, гарантии, простоту.",
        "visit": "ГЛАВНАЯ ЦЕЛЬ — чтобы клиент ПРИШЁЛ или оставил заявку на замер. Призыв: приезжайте, запишитесь на бесплатный замер, оставьте заявку."
    }.get(req.goal, "ГЛАВНАЯ ЦЕЛЬ — побудить клиента к действию.")

    prompt = build_prompt(req, price_hint, length_text, goal_text, my_ads_text)

    try:
        _mt = 4000 + int(getattr(req, "count", 10) or 10) * 1400
        _mt = min(max(_mt, 6000), 32000)
        raw = chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.9, max_tokens=_mt,
            account_id=req.account_id, operation="генерация объявлений"
        )
    except Exception as e:
        return {"status": "error", "message": f"Борис недоступен (GigaChat и OpenAI): {str(e)[:200]}"}

    # чистим от возможных markdown-обёрток
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    import re as _re

    def try_parse(text):
        text = text.strip()
        # вырезаем массив [...]
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start:end+1]
        # чиним типичные ошибки ИИ
        text = _re.sub(r",\s*]", "]", text)   # лишняя запятая перед ]
        text = _re.sub(r",\s*}", "}", text)   # лишняя запятая перед }
        # чиним битые escape от ИИ: оставляем только валидные JSON-escape (\" \\ \/ \b \f \n \r \t \uXXXX),
        # любой другой обратный слеш (напр. \l из "\li>") — экранируем в двойной, чтобы json.loads не падал
        text = _re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', text)
        out = []
        in_str = False
        prev = ""
        for ch in text:
            if ch == '"' and prev != "\\":
                in_str = not in_str
                out.append(ch)
            elif in_str and ch == "\n":
                out.append("\\n")
            elif in_str and ch == "\r":
                out.append("\\r")
            elif in_str and ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
            prev = ch
        text = "".join(out)
        return json.loads(text)

    ads = None
    try:
        ads = try_parse(raw)
    except Exception as _e:
        print("=== СЫРОЙ ОТВЕТ GIGACHAT (1я попытка) ===", flush=True)
        print(repr(raw[:2000]), flush=True)
        print("=== ошибка:", _e, "===", flush=True)
        # вторая попытка — просим Бориса перегенерировать
        try:
            raw2 = chat_with_fallback(
                [Messages(role=MessagesRole.USER, content=prompt + "\n\nВАЖНО: верни ТОЛЬКО валидный JSON-массив, без пояснений и markdown. Экранируй кавычки внутри строк.")],
                temperature=0.7, max_tokens=4096
            )
            ads = try_parse(raw2)
        except Exception:
            return {"status": "error", "message": "Борис не смог собрать корректный ответ. Попробуйте ещё раз или уменьшите количество."}

    if not isinstance(ads, list) or len(ads) == 0:
        return {"status": "error", "message": "Пустой результат, попробуйте ещё раз"}

    # чистим и валидируем каждое объявление
    clean = []
    for a in ads:
        if not isinstance(a, dict):
            continue
        title = _fix_title_len(str(a.get("title", "")).strip())
        desc = str(a.get("description", "")).strip()
        # уборщик битых тегов от ИИ
        import re as _re2
        # разрешённые теги Авито
        _allowed = ["strong", "em", "ul", "ol", "li", "p", "br"]
        # удаляем любые теги, кроме разрешённых (включая обрывки типа </h3>, <Price>, </u>)
        def _clean_tag(m):
            tag = m.group(0)
            name = _re2.sub(r"[<>/]", "", tag).split()[0].lower() if _re2.sub(r"[<>/]", "", tag).split() else ""
            return tag if name in _allowed else ""
        desc = _re2.sub(r"</?[a-zA-Zа-яА-Я][^>]*>", _clean_tag, desc)
        # убираем одиночные обрывки < или > и незакрытые скобки тегов
        desc = _re2.sub(r"</[^>]*$", "", desc)   # обрыв в конце: </...
        desc = _re2.sub(r"<[a-zA-Z]*$", "", desc) # обрыв <p в конце
        desc = desc.replace("<>", "")  # убрана опасная массовая вырезка "</" — она ломала валидные закрывающие теги
        pass  # старая хрупкая страховка удалена, жир расставляется ниже в УБОРЩИК v2
        title = _re2.sub(r"\*\*(.+?)\*\*", r"\1", title)  # в заголовке жир не нужен, просто убрать
        # === УБОРЩИК v2 ===
        for _junk in ["Ключевые фразы:", "Ключевые слова:", "SEO-хвост:", "SEO:", "Хвост:"]:
            desc = desc.replace(_junk, "")
        desc = desc.replace("}{", "} {")
        desc = _re2.sub(r"\*\*(.+?)\*\*", r"\1", desc)
        desc = _re2.sub(r"(?<!\*)\*([^\*\n]{4,}?)\*(?!\*)", r"\1", desc)  # одиночные *курсив* -> убрать звёздочки
        desc = desc.replace("<strong>", "").replace("</strong>", "")
        _lines = desc.split("\n")
        _out = []
        for _ln in _lines:
            _st = _ln.strip()
            _low = _st.lower()
            is_head = any(h in _st[:6] for h in ["📝", "✅", "🎁", "📌"])
            is_price = ("₽" in _st and ("от " in _low[:6] or _low.startswith("цена") or _low.startswith("от")))
            if _st and (is_head or is_price):
                _lead = _ln[:len(_ln) - len(_ln.lstrip())]
                _out.append(_lead + "<strong>" + _st + "</strong>")
            else:
                _out.append(_ln)
        desc = "\n".join(_out)
        desc = _re2.sub(r"[ \t]+\n", "\n", desc).strip()
        # убираем выдуманное "бесплатно"
        desc = desc.replace(" или бесплатно", "").replace("или бесплатно", "").replace("бесплатно!", "").replace(" бесплатно", "")
        desc = _re2.sub(r">{2,}", "", desc)  # убираем мусор типа >>>>>>
        desc = _re2.sub(r"</(?![a-zA-Z])", "", desc)  # убираем висящие "</" без имени тега (обрывки типа "700 ₽!</ Ваш...")
        desc = _re2.sub(r"\\[а-яА-Яa-zA-Z]\b", "", desc)  # убираем битые escape-остатки типа \н, \d
        desc = _re2.sub(r"^\s*\*\s*$", "", desc, flags=_re2.MULTILINE)  # убираем пустые строки-буллеты "* "
        desc = _re2.sub(r"([,;:])\.", r"\1", desc)  # чиним двойные знаки типа ",."
        # автопростановка точек: если строка-предложение не кончается на знак — ставим точку
        _pl = []
        for _ln in desc.split("\n"):
            _s = _ln.rstrip()
            _bare = _re2.sub(r"</?strong>", "", _s).rstrip()
            # добавляем точку ТОЛЬКО если строка кончается на букву/цифру (не на теги, скобки, эмодзи)
            if _bare and (_bare[-1].isalnum()):
                _s = _s + "."
            _pl.append(_s)
        desc = "\n".join(_pl)
        # если "Пример услуги:" без фигурных скобок — оборачиваем перечисление в спинтакс вручную
        _pu_match = _re2.search(r"(Пример услуги:\s*)([^\n{}]+)$", desc.strip())
        if _pu_match and "{" not in _pu_match.group(2):
            _parts = [p.strip().rstrip(".") for p in _pu_match.group(2).split(",") if p.strip()]
            if len(_parts) >= 2:
                _wrapped = " ".join("{" + p + "}" for p in _parts)
                desc = desc[:desc.rfind(_pu_match.group(0))] + _pu_match.group(1) + _wrapped
        # разлепляем слипшиеся параметры в "Пример услуги": если в одной группе {} смешаны повод и объём — разносим на 2 группы
        def _split_pu_groups(m):
            line = m.group(0)
            groups = _re2.findall(r"\{[^{}]+\}", line)
            if not groups:
                return line
            prefix = line[:line.find("{")]
            vol_re = _re2.compile(r"от\s*\d+\s*(символов|предложений|четверостиш\w*)")
            new_groups = []
            for g in groups:
                parts = g[1:-1].split("|")
                vol_parts = [p for p in parts if vol_re.search(p)]
                other_parts = [p for p in parts if not vol_re.search(p)]
                if vol_parts and other_parts:
                    new_groups.append("{" + "|".join(other_parts) + "}")
                    new_groups.append("{" + "|".join(vol_parts) + "}")
                else:
                    new_groups.append(g)
            return prefix + " ".join(new_groups)
        desc = _re2.sub(r"Пример [а-яё]+:.*$", _split_pu_groups, desc.strip(), flags=_re2.MULTILINE | _re2.IGNORECASE)

        price = a.get("price", 0)
        try:
            price = int(price)
        except Exception:
            price = 0
        # чиним заголовок: убираем "лишний" | после закрытой скобки {...}|хвост -> вставляем хвост как ещё один вариант
        _m = _re2.match(r"^\{(.+)\}\|(.+)$", title.strip())
        if _m:
            title = "{" + _m.group(1) + "|" + _m.group(2) + "}"
        # балансируем фигурные скобки в заголовке (модель иногда теряет { или })
        title = title.strip()
        title = _re2.sub(r"\{[Вв]\d\|[Вв]\d\|[Вв]\d[^}]*\}", "", title).strip().rstrip(",").strip()
        _open, _close = title.count("{"), title.count("}")
        if _open == 0 and _close == 0 and "|" in title:
            title = "{" + title + "}"
        elif _open != _close:
            if _open < _close and "|" in title and title.endswith("}"):
                title = "{" + title
            elif _open > _close:
                title = title + "}" * (_open - _close)
            else:
                for _ in range(_close - _open):
                    _idx = title.rfind("}")
                    if _idx != -1:
                        title = title[:_idx] + title[_idx+1:]
        # чиним цену: 0 или отрицательная — берём разумный дефолт и правим текст в описании
        if not price or price <= 0:
            _fallback_price = req.price_from if getattr(req, "price_from", 0) else 700
            desc = _re2.sub(r"[Оо]т\s*0\s*₽", f"от {_fallback_price} ₽", desc)
            desc = desc.replace("0 ₽", f"{_fallback_price} ₽")
            price = _fallback_price
        # делаем первую букву каждого варианта спинтакса заголовка заглавной
        def _cap_variant(m):
            parts = m.group(1).split("|")
            parts = [p.strip()[:1].upper() + p.strip()[1:] if p.strip() else p for p in parts]
            return "{" + "|".join(parts) + "}"
        title = _re2.sub(r"\{([^{}]+)\}", _cap_variant, title)
        # цена в тексте описания — всегда подставляем реальную price, не доверяем модели её писать
        desc = _re2.sub(r"\d[\d\s]*\s*₽", f"{price} ₽", desc)
        # финальная страховка: если заголовок развалился (несколько блоков {} или слипшиеся слова) — безопасный дефолт
        _brace_pairs = title.count("{")
        _outside_glued = _re2.search(r"[А-Яа-яA-Za-z]{16,}", _re2.sub(r"\{[^}]*\}", "", title))
        _inside_glued = _re2.search(r"[А-Яа-яA-Za-z]{13,}", title)  # ловим слипшиеся слова и ВНУТРИ вариантов спинтакса

        # обрезаем каждый вариант спинтакса до 5 слов, если модель расписалась
        title = _re2.sub(r"\{([^{}]+)\}", lambda m: "{" + "|".join(" ".join(v.split()[:5]) for v in m.group(1).split("|")) + "}", title)

        if _brace_pairs > 1 or _outside_glued or _inside_glued:
            title = "{" + req.topic.strip().capitalize() + "|" + req.topic.strip().capitalize() + " на заказ}"
        # перемешиваем порядок пунктов в списках <li> и порядок ключевых фраз — против блокировки Авито за "повтор услуг"
        def _shuffle_li_block(m):
            items = _re2.findall(r"<li>.*?</li>", m.group(0))
            if len(items) > 1:
                random.shuffle(items)
            return m.group(0)[:m.group(0).find("<li>")] + "".join(items) + "</ul>"
        desc = _re2.sub(r"<ul>.*?</ul>", _shuffle_li_block, desc, flags=_re2.DOTALL)

        _lines2 = desc.split("\n")
        for _li_idx, _ln2 in enumerate(_lines2):
            if "," in _ln2 and len(_ln2) > 60 and "<" not in _ln2 and "₽" not in _ln2 and not _ln2.strip().startswith(("📝","✅","🎁","📌","🔥")):
                _parts2 = [p.strip() for p in _ln2.split(",") if p.strip()]
                if len(_parts2) >= 5:
                    random.shuffle(_parts2)
                    _lines2[_li_idx] = ", ".join(_parts2) + ("." if _ln2.rstrip().endswith(".") else "")
        desc = "\n".join(_lines2)

        if title:
            clean.append({"title": title, "description": desc, "price": price})

    return {"status": "ok", "count": len(clean), "ads": clean}

# ==== Генератор баннеров через Kandinsky (GigaChat) ====
# ==== Менеджер картинок: генерация (1-5), папки, удаление, скачивание ====
import base64 as _b64
import time as _time
import re as _re_img

IMAGES_DIR = "/root/BORIS/backend/images"

def uniquify_image(local_path: str) -> str:
    """Открывает картинку, слегка видоизменяет (поворот/обрезка/яркость) и сохраняет как новый файл. Возвращает имя нового файла (без пути)."""
    try:
        import hashlib
        # ВАЖНО: имя детерминировано по исходному пути — при повторном вызове на то же
        # фото (например, при каждом запросе Avito к фиду) переиспользуем уже готовый
        # файл вместо создания новой копии. Раньше это давало 167 000+ лишних файлов.
        _stable_hash = hashlib.md5(local_path.encode("utf-8")).hexdigest()[:16]
        new_name = f"uq_{_stable_hash}.jpg"
        new_path = os.path.join(os.path.dirname(local_path), new_name)

        if os.path.isfile(new_path):
            return new_name

        from PIL import Image, ImageEnhance
        img = Image.open(local_path).convert("RGB")
        w, h = img.size

        angle = random.uniform(-2.5, 2.5)
        img = img.rotate(angle, expand=False, fillcolor=(255, 255, 255))

        crop_pct = random.uniform(0.01, 0.03)
        cx, cy = int(w * crop_pct), int(h * crop_pct)
        img = img.crop((cx, cy, w - cx, h - cy)).resize((w, h))

        img = ImageEnhance.Brightness(img).enhance(random.uniform(0.96, 1.04))
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.96, 1.04))

        img.save(new_path, "JPEG", quality=90)
        return new_name
    except Exception:
        return os.path.basename(local_path)  # если что-то пошло не так — отдаём исходный файл как есть

def uniquify_image_variant(local_path: str, variant_idx: int) -> str:
    """Как uniquify_image, но variant_idx даёт НЕСКОЛЬКО разных уникализированных
    версий одного оригинала — нужно, когда фото не хватает и один и тот же снимок
    приходится использовать в объявлении несколько раз (чтобы клоны визуально отличались)."""
    try:
        import hashlib
        _stable_hash = hashlib.md5(f"{local_path}::v{variant_idx}".encode("utf-8")).hexdigest()[:16]
        new_name = f"uqv_{_stable_hash}.jpg"
        new_path = os.path.join(os.path.dirname(local_path), new_name)
        if os.path.isfile(new_path):
            return new_name

        from PIL import Image, ImageEnhance
        _rnd = random.Random(f"{local_path}{variant_idx}")
        img = Image.open(local_path).convert("RGB")
        w, h = img.size
        crop_pct = _rnd.uniform(0.01, 0.04)
        cx, cy = int(w * crop_pct), int(h * crop_pct)
        img = img.crop((cx, cy, w - cx, h - cy)).resize((w, h))
        img = ImageEnhance.Brightness(img).enhance(_rnd.uniform(0.94, 1.06))
        img = ImageEnhance.Contrast(img).enhance(_rnd.uniform(0.94, 1.06))
        img = ImageEnhance.Color(img).enhance(_rnd.uniform(0.94, 1.06))
        img.save(new_path, "JPEG", quality=90)
        return new_name
    except Exception:
        return os.path.basename(local_path)

def get_draft_image_set(photo_urls: list, want: int = 5, min_threshold: int = 3) -> list:
    """Формирует список фото для черновика объявления.
    Если оригиналов >= min_threshold - берёт до `want` штук по кругу, без клонов.
    Если оригиналов меньше min_threshold - использует все имеющиеся + добирает
    уникализированными клонами (uniquify_image_variant) до min_threshold, чтобы
    избежать повторяющихся фото внутри одного объявления (риск блокировки Авито)."""
    if not photo_urls:
        return []
    if len(photo_urls) >= min_threshold:
        return [photo_urls[i % len(photo_urls)] for i in range(min(want, len(photo_urls)))]

    result = list(photo_urls)
    variant_idx = 1
    while len(result) < min_threshold:
        base_url = photo_urls[len(result) % len(photo_urls)]
        try:
            rel = base_url.split("/images/")[-1]
            local_path = os.path.join(IMAGES_DIR, rel)
            if os.path.isfile(local_path):
                new_name = uniquify_image_variant(local_path, variant_idx)
                folder = os.path.dirname(rel)
                new_url = f"https://boris-ai.pro/images/{folder}/{new_name}" if folder else f"https://boris-ai.pro/images/{new_name}"
                result.append(new_url)
            else:
                result.append(base_url)
        except Exception:
            result.append(base_url)
        variant_idx += 1
    return result

def _safe_folder(name: str) -> str:
    name = _re_img.sub(r"[^a-zA-Zа-яА-Я0-9_-]", "_", (name or "").strip())[:40]
    return name or "общая"

def _resolve_folder_path(account_id: str, folder: str, create_if_missing: bool = False) -> str:
    """Путь к папке клиента. Для otdushi сначала ищет среди старых плоских папок (обратная совместимость), иначе — новая изолированная структура IMAGES_DIR/account_id/folder."""
    safe = _safe_folder(folder)
    if account_id == "otdushi":
        flat_path = f"{IMAGES_DIR}/{safe}"
        if os.path.isdir(flat_path):
            return flat_path
    nested_path = f"{IMAGES_DIR}/{account_id}/{safe}"
    if create_if_missing:
        os.makedirs(nested_path, exist_ok=True)
    return nested_path

def _folder_url(account_id: str, folder: str, fname: str) -> str:
    safe = _safe_folder(folder)
    flat_path = f"{IMAGES_DIR}/{safe}"
    if account_id == "otdushi" and os.path.isdir(flat_path):
        return f"/images/{safe}/{fname}"
    return f"/images/{account_id}/{safe}/{fname}"

class GenerateBannerRequest(BaseModel):
    prompt: str
    style: str = "реалистичный"
    count: int = 1
    folder: str = "общая"
    account_id: str = "otdushi"

@router.post("/generate_banner")
def generate_banner(req: GenerateBannerRequest):
    folder = _safe_folder(req.folder)
    folder_path = _resolve_folder_path(req.account_id, folder, create_if_missing=True)
    count = max(1, min(int(req.count or 1), 50))

    variations = [
        "", " Ракурс крупным планом.", " Вид сбоку, другая композиция.",
        " Другая цветовая гамма, тёплые тона.", " Атмосферная подача, мягкий свет."
    ]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _generate_one(i):
        full_prompt = f"Фотореалистичное изображение товара крупным планом: {req.prompt}.{variations[i % len(variations)]} Детальная фактура материала, естественное освещение, как профессиональная предметная фотосъёмка. Стиль: {req.style}. ВАЖНО: показывай именно сам товар вблизи, БЕЗ людей, БЕЗ текста, без надписей, без букв, без логотипов, без абстрактных фонов и пейзажей."
        _attempts = 0
        while _attempts < 3:
            _attempts += 1
            try:
                with GigaChat(credentials=os.getenv("GIGACHAT_KEY"), scope=_os_gc.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"), model=_GC_MODEL, verify_ssl_certs=False, timeout=120) as client:
                    response = client.chat(Chat(
                        messages=[
                            Messages(role=MessagesRole.SYSTEM, content="Ты — художник Kandinsky, создаёшь качественные изображения для рекламы."),
                            Messages(role=MessagesRole.USER, content=full_prompt)
                        ],
                        function_call="auto"
                    ))
                    content = response.choices[0].message.content
                    m = _re_img.search(r'src="([^"]+)"', content)
                    if not m:
                        return (i, None, f"Вариант {i+1}: не нарисовал")
                    file_id = m.group(1)
                    image = client.get_image(file_id)
                    img_b64 = image.content
                fname = f"gen_{int(_time.time())}_{random.randint(1000,9999)}.jpg"
                fpath = f"{folder_path}/{fname}"
                with open(fpath, "wb") as imgf:
                    imgf.write(_b64.b64decode(img_b64))
                return (i, _folder_url(req.account_id, folder, fname), None)
            except Exception as e:
                if "429" in str(e) and _attempts < 3:
                    _time.sleep(3 * _attempts)
                    continue
                return (i, None, f"Вариант {i+1}: {str(e)[:100]}")
        return (i, None, f"Вариант {i+1}: не удалось после {_attempts} попыток")

    results_by_index = {}
    errors = []
    with ThreadPoolExecutor(max_workers=1) as executor:  # тариф Freemium GigaChat = 1 поток одновременных запросов; поднять до 10 при переходе на Business
        futures = [executor.submit(_generate_one, i) for i in range(count)]
        for future in as_completed(futures):
            i, url, err = future.result()
            if url:
                results_by_index[i] = url
            if err:
                errors.append(err)

    results = [results_by_index[i] for i in sorted(results_by_index.keys())]

    if not results:
        return {"status": "error", "message": "; ".join(errors) or "Не получилось"}
    return {"status": "ok", "images": results, "errors": errors}

class DownloadImageRequest(BaseModel):
    url: str
    folder: str = "общая"
    account_id: str = "otdushi"

@router.post("/download_image")
def download_image(req: DownloadImageRequest):
    folder = _safe_folder(req.folder)
    folder_path = _resolve_folder_path(req.account_id, folder, create_if_missing=True)
    try:
        try:
            from proxy_pool import get_intl_requests_proxies
            _proxies_dict = get_intl_requests_proxies()
            _proxy_url = _proxies_dict.get("https") if _proxies_dict else None
        except Exception:
            _proxy_url = None
        try:
            if _proxy_url:
                with httpx.Client(proxy=_proxy_url, timeout=30, follow_redirects=True) as _client:
                    r = _client.get(req.url)
            else:
                r = httpx.get(req.url, timeout=30, follow_redirects=True)
            if r.status_code != 200:
                raise Exception(f"proxy attempt got {r.status_code}")
        except Exception:
            r = httpx.get(req.url, timeout=30, follow_redirects=True)
        if r.status_code != 200:
            return {"status": "error", "message": f"Не скачалось (код {r.status_code})"}
        ct = r.headers.get("content-type", "")
        ext = ".jpg" if "jpeg" in ct or "jpg" in ct else ".png" if "png" in ct else ""
        if not ext:
            return {"status": "error", "message": "По ссылке не картинка (нужна прямая ссылка на JPG/PNG)"}
        fname = f"dl_{int(_time.time())}_{random.randint(1000,9999)}{ext}"
        with open(f"{folder_path}/{fname}", "wb") as f2:
            f2.write(r.content)
        return {"status": "ok", "image_url": _folder_url(req.account_id, folder, fname)}
    except Exception as e:
        return {"status": "error", "message": str(e)[:150]}

class StockPhotoSearchRequest(BaseModel):
    query: str
    count: int = 10
    account_id: str = "otdushi"
    folder: str = ""
    source: str = "pexels"  # pexels | pixabay | unsplash

def _get_proxy_url():
    try:
        from proxy_pool import get_intl_requests_proxies
        d = get_intl_requests_proxies()
        return d.get("https") if d else None
    except Exception:
        return None

def _download_image_to_folder(img_url: str, account_id: str, folder: str, folder_path: str, proxy_url):
    _img_kwargs = {"timeout": 30, "follow_redirects": True}
    if proxy_url:
        _img_kwargs["proxy"] = proxy_url
    with httpx.Client(**_img_kwargs) as _ic:
        r = _ic.get(img_url)
    if r.status_code != 200:
        return None
    fname = f"stock_{int(_time.time())}_{random.randint(1000,9999)}.jpg"
    with open(f"{folder_path}/{fname}", "wb") as f2:
        f2.write(r.content)
    return _folder_url(account_id, folder, fname)

@router.post("/search_stock_photos")
def search_stock_photos(req: StockPhotoSearchRequest):
    """Ищет реальные бесплатные фото (Pexels/Pixabay/Unsplash) по запросу и скачивает N штук в галерею клиента."""
    folder = _safe_folder(req.folder or req.query)
    folder_path = _resolve_folder_path(req.account_id, folder, create_if_missing=True)
    proxy_url = _get_proxy_url()
    source = (req.source or "pexels").lower()

    try:
        img_urls = []

        if source == "pexels":
            pexels_key = os.environ.get("PEXELS_API_KEY")
            if not pexels_key:
                return {"status": "error", "message": "PEXELS_API_KEY не настроен в .env"}
            with httpx.Client(timeout=20, follow_redirects=True, **({"proxy": proxy_url} if proxy_url else {})) as client:
                resp = client.get(
                    "https://api.pexels.com/v1/search",
                    headers={"Authorization": pexels_key},
                    params={"query": req.query, "per_page": min(req.count, 80), "orientation": "landscape"},
                )
            if resp.status_code != 200:
                return {"status": "error", "message": f"Pexels API вернул код {resp.status_code}: {resp.text[:200]}"}
            photos = resp.json().get("photos", [])
            img_urls = [p.get("src", {}).get("large") or p.get("src", {}).get("original") for p in photos[:req.count]]

        elif source == "pixabay":
            pixabay_key = os.environ.get("PIXABAY_API_KEY")
            if not pixabay_key:
                return {"status": "error", "message": "PIXABAY_API_KEY не настроен в .env"}
            with httpx.Client(timeout=20, follow_redirects=True, **({"proxy": proxy_url} if proxy_url else {})) as client:
                resp = client.get(
                    "https://pixabay.com/api/",
                    params={"key": pixabay_key, "q": req.query, "image_type": "photo", "per_page": min(max(req.count, 3), 200), "orientation": "horizontal"},
                )
            if resp.status_code != 200:
                return {"status": "error", "message": f"Pixabay API вернул код {resp.status_code}: {resp.text[:200]}"}
            hits = resp.json().get("hits", [])
            img_urls = [h.get("largeImageURL") or h.get("webformatURL") for h in hits[:req.count]]

        elif source == "unsplash":
            unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY")
            if not unsplash_key:
                return {"status": "error", "message": "UNSPLASH_ACCESS_KEY не настроен в .env"}
            with httpx.Client(timeout=20, follow_redirects=True, **({"proxy": proxy_url} if proxy_url else {})) as client:
                resp = client.get(
                    "https://api.unsplash.com/search/photos",
                    headers={"Authorization": f"Client-ID {unsplash_key}"},
                    params={"query": req.query, "per_page": min(req.count, 30), "orientation": "landscape"},
                )
            if resp.status_code != 200:
                return {"status": "error", "message": f"Unsplash API вернул код {resp.status_code}: {resp.text[:200]}"}
            results = resp.json().get("results", [])
            img_urls = [r.get("urls", {}).get("regular") for r in results[:req.count]]

        else:
            return {"status": "error", "message": f"Неизвестный источник: {source}"}

        img_urls = [u for u in img_urls if u]
        if not img_urls:
            return {"status": "error", "message": f"{source} не нашёл фото по этому запросу"}

        downloaded = []
        for img_url in img_urls:
            try:
                url = _download_image_to_folder(img_url, req.account_id, folder, folder_path, proxy_url)
                if url:
                    downloaded.append(url)
            except Exception:
                continue

        return {"status": "ok", "source": source, "folder": folder, "downloaded": len(downloaded), "images": downloaded}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}



@router.post("/upload_images")
async def upload_images(folder: str = Form("общая"), account_id: str = Form("otdushi"), files: list[UploadFile] = File(...)):
    folder = _safe_folder(folder)
    folder_path = _resolve_folder_path(account_id, folder, create_if_missing=True)
    urls = []
    for file in files:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in [".jpg", ".jpeg", ".png"]:
            ext = ".jpg"
        fname = f"up_{int(_time.time())}_{random.randint(1000,9999)}{ext}"
        content = await file.read()
        with open(f"{folder_path}/{fname}", "wb") as f2:
            f2.write(content)
        urls.append(_folder_url(account_id, folder, fname))
    return {"status": "ok", "images": urls}

class GenerateNamesRequest(BaseModel):
    topic: str
    count: int = 10

@router.post("/generate_names")
def generate_names(req: GenerateNamesRequest):
    count = max(1, min(int(req.count or 10), 50))
    prompt = f"""Ты — SEO-специалист по Авито. Сгенерируй {count} коротких названий товара/услуги (3-6 слов каждое) по теме "{req.topic}".

ЖЁСТКИЕ ПРАВИЛА (нарушение = брак):
1. КАЖДОЕ название ОБЯЗАНО начинаться с самого товара или услуги (например: шкаф, гардеробная, кухня, кухонный гарнитур, прихожая, стол, стенка, комод, кровать, стеллаж, тумба). БЕЗ предмета название запрещено.
2. ЗАПРЕЩЕНЫ абстрактные слоганы без предмета: «удобное решение», «готовое решение», «порядок легко», «зона хранения», «стильное решение для семьи», «роскошный вид», «эффектная композиция».
3. ЗАПРЕЩЕНЫ призывы вместо товара: «напишите», «ответим за 5 минут», «все в чате», «узнать расчёт».
4. НИКАКИХ знаков препинания и символов: без точки, запятой, тире, дефиса, слэша, двоеточия, кавычек, скобок, вертикальной черты. Только буквы, цифры и пробелы.
5. Названия должны РАЗЛИЧАТЬСЯ между собой — добавляй различие через цвет, размер (компактный, широкий, узкий, угловой), тип (распашной, купе, встроенный, модульный). НЕ повторяй одно и то же название.
6. Длина каждого — до 50 символов, без ЗАГЛАВНЫХ слов целиком.
7. Это реалистичные поисковые формулировки, как реально ищут люди на Avito.

Формула: [Товар] + [признак: цвет/размер/тип/на заказ].
Верни СТРОГО JSON-массив строк, без markdown: ["название 1", "название 2", ...]"""
    try:
        raw = chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.9, max_tokens=1500,
            account_id=getattr(req, "account_id", None), operation="подбор названий"
        ).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        start, end = raw.find("["), raw.rfind("]")
        chunk = raw[start:end+1] if start != -1 and end != -1 else raw
        names = None
        # попытка 1: обычный JSON
        try:
            names = json.loads(chunk)
        except Exception:
            pass
        # попытка 2: GigaChat часто экранирует кавычки как \" — чистим и парсим снова
        if names is None:
            try:
                names = json.loads(chunk.replace('\\"', '"'))
            except Exception:
                pass
        # попытка 3: регуляркой достаём всё между кавычками
        if names is None:
            import re as _re3
            names = _re3.findall(r'"\s*([^"\\]+?)\s*"', chunk.replace('\\"', '"'))
        # чистка: убираем мусорные символы по краям, пустые, обрезаем
        import re as _re4
        clean = []
        for n in names:
            n = str(n).strip().strip('\\",[]{}|').strip()
            n = _re4.sub(r"\s+", " ", n)
            if n and n not in clean:
                clean.append(n)
        names = clean[:count]
        return {"status": "ok", "names": names}
    except Exception as e:
        return {"status": "error", "message": str(e)[:150]}

@router.get("/download_single")
def download_single(url: str):
    from fastapi.responses import FileResponse
    rel = url.split("/images/")[-1]
    if ".." in rel:
        return {"status": "error", "message": "Некорректный путь"}
    fpath = f"{IMAGES_DIR}/{rel}"
    if not os.path.isfile(fpath):
        return {"status": "error", "message": "Файл не найден"}
    fname = os.path.basename(fpath)
    return FileResponse(fpath, filename=fname, media_type="image/jpeg")

@router.get("/download_folder_zip")
def download_folder_zip(folder: str, account_id: str = "otdushi"):
    import zipfile
    import io
    from fastapi.responses import StreamingResponse
    folder = _safe_folder(folder)
    fpath = _resolve_folder_path(account_id, folder)
    if not os.path.isdir(fpath):
        return {"status": "error", "message": "Папка не найдена"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname in os.listdir(fpath):
            if fname.lower().endswith((".jpg", ".jpeg", ".png")):
                zf.write(os.path.join(fpath, fname), arcname=fname)
    buf.seek(0)
    from urllib.parse import quote
    encoded_name = quote(f"{folder}.zip")
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"})

class MoveImageRequest(BaseModel):
    url: str
    target_folder: str
    account_id: str = "otdushi"

@router.post("/move_image")
def move_image(req: MoveImageRequest):
    rel = req.url.split("/images/")[-1]
    if ".." in rel:
        return {"status": "error", "message": "Некорректный путь"}
    src_path = f"{IMAGES_DIR}/{rel}"
    if not os.path.isfile(src_path):
        return {"status": "error", "message": "Файл не найден"}
    target = _safe_folder(req.target_folder)
    os.makedirs(f"{IMAGES_DIR}/{target}", exist_ok=True)
    fname = os.path.basename(src_path)
    dst_path = f"{IMAGES_DIR}/{target}/{fname}"
    os.rename(src_path, dst_path)
    return {"status": "ok", "new_url": f"/images/{target}/{fname}"}

class RenameFolderRequest(BaseModel):
    old_name: str
    new_name: str

@router.post("/rename_folder")
def rename_folder(req: RenameFolderRequest):
    old_folder = _safe_folder(req.old_name)
    new_folder = _safe_folder(req.new_name)
    old_path = f"{IMAGES_DIR}/{old_folder}"
    new_path = f"{IMAGES_DIR}/{new_folder}"
    if not os.path.isdir(old_path):
        return {"status": "error", "message": "Папка не найдена"}
    if os.path.isdir(new_path):
        return {"status": "error", "message": "Папка с таким именем уже существует"}
    os.rename(old_path, new_path)
    return {"status": "ok", "new_name": new_folder}

@router.delete("/delete_folder")
def delete_folder(folder: str):
    import shutil
    target = _safe_folder(folder)
    fpath = f"{IMAGES_DIR}/{target}"
    if not os.path.isdir(fpath):
        return {"status": "error", "message": "Папка не найдена"}
    files = os.listdir(fpath)
    if files:
        return {"status": "error", "message": f"Папка не пуста ({len(files)} файлов), сначала перенесите или удалите фото"}
    shutil.rmtree(fpath)
    return {"status": "ok"}

@router.get("/images_list")
def images_list(account_id: str = "otdushi"):
    result = {}
    if account_id == "otdushi" and os.path.isdir(IMAGES_DIR):
        for folder in sorted(os.listdir(IMAGES_DIR)):
            fpath = f"{IMAGES_DIR}/{folder}"
            if not os.path.isdir(fpath):
                if folder.lower().endswith((".jpg",".jpeg",".png")):
                    result.setdefault("общая", []).append(f"/images/{folder}")
                continue
            if folder == account_id or "/" in folder:
                continue
            is_account_dir = any(
                os.path.isdir(f"{IMAGES_DIR}/{d}") and d == folder
                for d in os.listdir(IMAGES_DIR)
            )
            files = [f"/images/{folder}/{f}" for f in sorted(os.listdir(fpath)) if f.lower().endswith((".jpg",".jpeg",".png"))]
            result[folder] = files

    acc_dir = f"{IMAGES_DIR}/{account_id}"
    if os.path.isdir(acc_dir):
        for folder in sorted(os.listdir(acc_dir)):
            fpath = f"{acc_dir}/{folder}"
            if os.path.isdir(fpath):
                files = [f"/images/{account_id}/{folder}/{f}" for f in sorted(os.listdir(fpath)) if f.lower().endswith((".jpg",".jpeg",".png"))]
                result[folder] = files
    return {"status": "ok", "folders": result}

class DeleteImageRequest(BaseModel):
    url: str

@router.post("/delete_image")
def delete_image(req: DeleteImageRequest):
    rel = req.url.split("/images/")[-1]
    if ".." in rel:
        return {"status": "error", "message": "Некорректный путь"}
    fpath = f"{IMAGES_DIR}/{rel}"
    if os.path.isfile(fpath):
        os.remove(fpath)
        return {"status": "ok"}
    return {"status": "error", "message": "Файл не найден"}

class CreateFolderRequest(BaseModel):
    folder: str
    account_id: str = "otdushi"

@router.post("/create_folder")
def create_folder(req: CreateFolderRequest):
    target = _safe_folder(req.folder)
    fpath = _resolve_folder_path(req.account_id, target, create_if_missing=True)
    return {"status": "ok", "folder": target}


@router.get("/my_boris_items")
def my_boris_items(account_id: str = "otdushi"):
    """Объявления, опубликованные через Бориса и доступные для редактирования."""
    items = _load_feed_items(account_id)
    return {"status": "ok", "items": [it.model_dump() for it in items]}

class EditItemRequest(BaseModel):
    account_id: str = "otdushi"
    item_id: str
    title: str | None = None
    description: str | None = None
    price: int | None = None
    images: list[str] | None = None
    confirmed: bool = False

@router.post("/edit_item")
def edit_item(req: EditItemRequest):
    """Редактирует уже опубликованное Борисом объявление и переотправляет фид с тем же Id."""
    items = _load_feed_items(req.account_id)
    found = None
    for it in items:
        if it.id == req.item_id:
            found = it
            break
    if not found:
        return {"status": "error", "message": "Объявление не найдено среди опубликованных Борисом"}

    # Подтверждение разрушительного действия: если не подтверждено И автопилот не разрешает —
    # возвращаем предпросмотр изменений и ждём явного confirmed=true
    if not req.confirmed and not _autopilot_allows(req.account_id):
        preview = []
        if req.title is not None: preview.append(f"заголовок → «{req.title}»")
        if req.description is not None: preview.append("описание (новый текст)")
        if req.price is not None: preview.append(f"цена → {req.price} ₽")
        if req.images is not None: preview.append(f"фото ({len(req.images)} шт.)")
        return {
            "status": "needs_confirmation",
            "message": "Подтвердите изменение объявления на Авито",
            "item_id": req.item_id,
            "current": {"title": found.title, "price": found.price},
            "changes": preview,
        }

    if req.title is not None:
        found.title = req.title
    if req.description is not None:
        found.description = req.description
    if req.price is not None:
        found.price = req.price
    if req.images is not None:
        found.images = req.images

    # Фиксируем в журнал действий: что именно изменено
    changes = []
    if req.title is not None: changes.append("заголовок")
    if req.description is not None: changes.append("описание")
    if req.price is not None: changes.append(f"цена={req.price}")
    if req.images is not None: changes.append("фото")
    _audit_log(req.account_id, "edit_item",
               f"объявление {req.item_id}: изменено {', '.join(changes) or 'ничего'}",
               actor="user")

    _save_feed_items(req.account_id, items)
    return {"status": "ok", "message": "Объявление обновлено, изменения появятся на Авито при следующей синхронизации фида"}




@router.get("/audit_log")
def get_audit_log(account_id: str, limit: int = 100):
    """Возвращает журнал действий по аккаунту (последние N записей, новые сверху)."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "audit_log").first()
        if not row:
            return {"status": "ok", "log": []}
        log = _json.loads(row.value)
        return {"status": "ok", "log": [_humanize_entry(e) for e in list(reversed(log))[:limit]]}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}
    finally:
        db.close()




class AutopilotSettingsRequest(BaseModel):
    account_id: str = "otdushi"
    mode: str = "always_ask"   # always_ask | always_auto | dates
    date_from: str | None = None
    date_to: str | None = None


@router.get("/autopilot_settings")
def get_autopilot_settings(account_id: str = "otdushi"):
    """Возвращает текущие настройки автопилота аккаунта."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "autopilot_settings").first()
        if not row:
            return {"status": "ok", "settings": {"mode": "always_ask"}}
        return {"status": "ok", "settings": _json.loads(row.value)}
    finally:
        db.close()


@router.post("/set_autopilot_settings")
def set_autopilot_settings(req: AutopilotSettingsRequest):
    """Сохраняет настройки автопилота: когда Борису можно действовать без подтверждения."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    db = SessionLocal()
    try:
        cfg = {"mode": req.mode}
        if req.mode == "dates":
            cfg["date_from"] = req.date_from
            cfg["date_to"] = req.date_to
        raw = _json.dumps(cfg, ensure_ascii=False)
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "autopilot_settings").first()
        if row:
            row.value = raw
        else:
            row = Storage(account_id=req.account_id, key="autopilot_settings", value=raw)
            db.add(row)

        # СИНХРОНИЗАЦИЯ: "Автопилот всегда включён" (ежедневный, KPI) и "Автопилот ставок"
        # (почасовой, Советник) — это два уровня одного и того же переключателя для клиента.
        # Включаем/выключаем bid_autopilot вместе с always_auto, чтобы не было рассинхронизации.
        kpi_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "kpi_settings").first()
        if kpi_row:
            try:
                kpi_cfg = _json.loads(kpi_row.value)
            except Exception:
                kpi_cfg = {}
            kpi_cfg["bid_autopilot"] = (req.mode == "always_auto")
            kpi_row.value = _json.dumps(kpi_cfg, ensure_ascii=False)

        db.commit()
        _audit_log(req.account_id, "set_autopilot", f"режим={req.mode} {req.date_from or ''}..{req.date_to or ''}", actor="user")
        return {"status": "ok", "settings": cfg}
    finally:
        db.close()


@router.get("/collect_stats")
def collect_stats(account_id: str = "otdushi"):
    """Собирает дневной снимок статистики: просмотры/контакты по объявлениям + баланс. Сохраняет в Storage."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import date as _date, timedelta as _timedelta

    token_data = get_avito_token(account_id)
    if "access_token" not in token_data:
        return {"status": "error", "message": "Не удалось авторизоваться в Avito API"}
    token = _extract_token(token_data)
    _, _, avito_user_id = _get_avito_credentials(account_id)
    if not avito_user_id:
        me_resp = httpx.get("https://api.avito.ru/core/v1/accounts/self", headers={"Authorization": f"Bearer {token}"})
        avito_user_id = str(me_resp.json().get("id", ""))
    if not avito_user_id:
        return {"status": "error", "message": "Не удалось определить avito_user_id"}

    # Обходим ВСЕ страницы: раньше брали только первую сотню, и клиент с 1382
    # объявлениями видел статистику по 7% своих и считал её полной.
    items = []
    for page in range(1, 21):  # до 2000 объявлений
        items_resp = httpx.get(
            "https://api.avito.ru/core/v1/items",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100, "page": page, "status": "active"},
            timeout=25
        )
        if items_resp.status_code != 200:
            break
        page_items = items_resp.json().get("resources", [])
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < 100:
            break
    item_ids = [it["id"] for it in items]
    print(f"[collect_stats] {account_id}: объявлений собрано {len(item_ids)}", flush=True)

    stats_by_id = {}
    if item_ids:
        # Avito считает статистику с задержкой — "сегодня" почти всегда пусто.
        # Берём последние 2 дня (вчера+сегодня), чтобы не терять данные ни при какой задержке.
        date_to = _date.today().isoformat()
        date_from = (_date.today() - _timedelta(days=2)).isoformat()
        # Avito не принимает произвольно длинный список — шлём партиями по 200
        for chunk_start in range(0, len(item_ids), 200):
            chunk = item_ids[chunk_start:chunk_start + 200]
            stats_resp = httpx.post(
                f"https://api.avito.ru/stats/v1/accounts/{avito_user_id}/items",
                headers={"Authorization": f"Bearer {token}"},
                json={"dateFrom": date_from, "dateTo": date_to,
                      "fields": ["uniqViews", "uniqContacts"], "itemIds": chunk},
                timeout=40
            )
            if stats_resp.status_code != 200:
                print(f"[collect_stats] {account_id}: партия {chunk_start//200 + 1} — статус {stats_resp.status_code}", flush=True)
                continue
            for it in stats_resp.json().get("result", {}).get("items", []):
                views = 0
                contacts = 0
                for _s in it.get("stats", []):
                    views += _s.get("uniqViews", 0)
                    contacts += _s.get("uniqContacts", 0)
                stats_by_id[it["itemId"]] = {"views": views, "contacts": contacts}

    balance_resp = httpx.get(f"https://api.avito.ru/core/v1/accounts/{avito_user_id}/balance/", headers={"Authorization": f"Bearer {token}"})
    balance = balance_resp.json() if balance_resp.status_code == 200 else {}

    enriched = []
    for it in items:
        s = stats_by_id.get(it["id"], {"views": 0, "contacts": 0})
        conversion = round(s["contacts"] / s["views"] * 100, 1) if s["views"] > 0 else 0
        enriched.append({
            "id": it["id"], "title": it.get("title", ""), "price": it.get("price", 0),
            "category": it.get("category", {}).get("name", ""), "status": it.get("status", ""),
            "views": s["views"], "contacts": s["contacts"], "conversion": conversion
        })

    sorted_by_conv = sorted(enriched, key=lambda x: x["conversion"], reverse=True)
    snapshot = {
        "date": _date.today().isoformat(),
        "balance": balance,
        "items_count": len(enriched),
        "items": enriched,
        "top_effective": sorted_by_conv[:5],
        "least_effective": [x for x in sorted_by_conv if x["views"] > 0][-5:] if any(x["views"] > 0 for x in enriched) else []
    }

    db = SessionLocal()
    try:
        key = f"daily_stats:{snapshot['date']}"
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        raw = _json.dumps(snapshot, ensure_ascii=False)
        if row:
            row.value = raw
        else:
            row = Storage(account_id=account_id, key=key, value=raw)
            db.add(row)
        db.commit()
    finally:
        db.close()

    return {"status": "ok", "snapshot": snapshot}


@router.get("/weekly_summary")
def weekly_summary(account_id: str = "otdushi"):
    """Собирает недельное текстовое резюме на основе накопленных daily_stats через GigaChat."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json
    from datetime import date as _date, timedelta as _timedelta

    db = SessionLocal()
    try:
        snapshots = []
        for i in range(7):
            d = (_date.today() - _timedelta(days=i)).isoformat()
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == f"daily_stats:{d}").first()
            if row:
                snapshots.append(_json.loads(row.value))
    finally:
        db.close()

    if not snapshots:
        return {"status": "error", "message": "Нет накопленных данных за неделю. Соберите статистику хотя бы за пару дней."}

    latest = snapshots[0]
    oldest = snapshots[-1]
    balance_change = latest.get("balance", {}).get("real", 0) - oldest.get("balance", {}).get("real", 0)

    all_top = []
    all_low = []
    for snap in snapshots:
        all_top.extend(snap.get("top_effective", []))
        all_low.extend(snap.get("least_effective", []))

    summary_data = {
        "дней_данных": len(snapshots),
        "текущий_баланс": latest.get("balance", {}),
        "изменение_баланса_за_период": balance_change,
        "всего_объявлений": latest.get("items_count", 0),
        "примеры_эффективных": [{"title": x["title"], "conversion": x["conversion"]} for x in all_top[:5]],
        "примеры_неэффективных": [{"title": x["title"], "views": x["views"]} for x in all_low[:5]]
    }

    prompt = f"""Ты — аналитик-помощник для владельца бизнеса на Авито. Вот данные за последние {len(snapshots)} дн(я/ей):
{_json.dumps(summary_data, ensure_ascii=False, indent=2)}

Напиши короткое (5-8 предложений) резюме простым языком: что происходит с бизнесом, что хорошо, что плохо, 2-3 конкретных совета что улучшить. Без markdown-разметки, обычный текст."""

    try:
        summary_text = chat_with_fallback(
            [Messages(role=MessagesRole.USER, content=prompt)],
            temperature=0.5, max_tokens=800,
            account_id=account_id, operation="недельная сводка"
        )
    except Exception as e:
        print(f"[summary] ошибка генерации: {str(e)[:300]}", flush=True)
        summary_text = "💭 Борис анализирует данные — резюме появится через пару минут. Нажмите «Обновить»."

    return {"status": "ok", "summary": summary_text, "data": summary_data}


@router.get("/director_overview")
def director_overview(_=_DepSec(_ReqOwner)):
    """Сводка по всем клиентам для Бориса-директора: последний снимок статистики каждого."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    from app.models.account import Account
    import json as _json
    from datetime import date as _date, timedelta as _timedelta

    db = SessionLocal()
    try:
        accounts = db.query(Account).all()
        overview = []
        for acc in accounts:
            has_avito_keys = bool(acc.avito_client_id and acc.avito_client_secret)
            live_items_count = None
            live_balance = None
            latest_snapshot = None
            for i in range(7):
                d = (_date.today() - _timedelta(days=i)).isoformat()
                row = db.query(Storage).filter(Storage.account_id == acc.account_id, Storage.key == f"daily_stats:{d}").first()
                if row:
                    latest_snapshot = _json.loads(row.value)
                    break
            if latest_snapshot:
                live_balance = latest_snapshot.get("balance")
            if has_avito_keys:
                try:
                    token_data = get_avito_token(acc.account_id)
                    token = _extract_token(token_data)
                    total = 0
                    for page in range(1, 21):  # до 2000 объявлений живьём
                        r = httpx.get(
                            "https://api.avito.ru/core/v1/items",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"per_page": 100, "page": page, "status": "active"},
                            timeout=10
                        )
                        if r.status_code != 200:
                            break
                        page_items = r.json().get("resources", [])
                        if not page_items:
                            break
                        total += len(page_items)
                        if len(page_items) < 100:
                            break
                    live_items_count = total
                    balance_resp = httpx.get(
                        "https://api.avito.ru/core/v1/accounts/self/balance/real",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10
                    )
                    if balance_resp.status_code == 200:
                        live_balance = {"real": balance_resp.json().get("real", 0), "bonus": 0}

                    contacts_today = 0
                    try:
                        _, _, avito_user_id_c = _get_avito_credentials(acc.account_id)
                        if not avito_user_id_c:
                            me_resp_c = httpx.get("https://api.avito.ru/core/v1/accounts/self", headers={"Authorization": f"Bearer {token}"}, timeout=10)
                            avito_user_id_c = str(me_resp_c.json().get("id", ""))
                        r_items_c = httpx.get(
                            "https://api.avito.ru/core/v1/items",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"per_page": 100, "page": 1, "status": "active"},
                            timeout=10
                        )
                        item_ids_c = [it["id"] for it in r_items_c.json().get("resources", [])][:200]
                        if item_ids_c and avito_user_id_c:
                            today_c = _date.today().isoformat()
                            stats_resp_c = httpx.post(
                                f"https://api.avito.ru/stats/v1/accounts/{avito_user_id_c}/items",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"dateFrom": today_c, "dateTo": today_c, "fields": ["uniqContacts"], "itemIds": item_ids_c},
                                timeout=15
                            )
                            if stats_resp_c.status_code == 200:
                                for it_c in stats_resp_c.json().get("result", {}).get("items", []):
                                    for s_c in it_c.get("stats", []):
                                        contacts_today += s_c.get("uniqContacts", 0)
                    except Exception:
                        pass
                except Exception:
                    pass
            cost_per_contact = None
            if contacts_today and live_balance and latest_snapshot and latest_snapshot.get("balance"):
                try:
                    prev_real = latest_snapshot["balance"].get("real", 0)
                    curr_real = live_balance.get("real", 0)
                    spent = max(prev_real - curr_real, 0)
                    if spent > 0:
                        cost_per_contact = round(spent / contacts_today, 2)
                except Exception:
                    pass
            try:
                from app.api.payments import compute_status as _pay_status
                _payment = _pay_status(acc.account_id, db)
            except Exception:
                _payment = {"has_payment": False}
            try:
                from app.api.billing import get_status as _billing_status
                _billing = _billing_status(acc.account_id)
            except Exception:
                _billing = {"tier": "none", "unlimited": False}
            overview.append({
                "account_id": acc.account_id,
                "name": acc.name,
                "has_avito_keys": has_avito_keys,
                "latest_stats_date": _date.today().isoformat() if live_items_count is not None else None,
                "balance": live_balance,
                "items_count": live_items_count,
                "contacts_today": contacts_today,
                "cost_per_contact": cost_per_contact,
                "payment": _payment,
                "money": _client_money(acc.account_id, db),
                "billing": _billing
            })
        escalations_row = db.query(Storage).filter(Storage.account_id == "_global", Storage.key == "director_escalations").first()
        escalations = _json.loads(escalations_row.value) if escalations_row else []
        unread_escalations = [e for e in escalations if not e.get("read")]
    finally:
        db.close()

    return {"status": "ok", "accounts": overview, "escalations": escalations[:50], "unread_escalations_count": len(unread_escalations)}


@router.post("/director/clear_escalations")
def clear_director_escalations():
    """Очищает список эскалаций для панели директора."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == "_global", Storage.key == "director_escalations").first()
        if row:
            row.value = "[]"
            db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/director/resolve_escalation")
def resolve_director_escalation(payload: dict):
    """Убирает одну конкретную эскалацию из списка по её ts."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_r

    ts = payload.get("ts")
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == "_global", Storage.key == "director_escalations").first()
        if row:
            escalations = _json_r.loads(row.value)
            escalations = [e for e in escalations if e.get("ts") != ts]
            row.value = _json_r.dumps(escalations, ensure_ascii=False)
            db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.get("/batch_effectiveness")
def batch_effectiveness(account_id: str, batch_id: int, days: int = 30):
    """Считает эффективность конкретной партии шаблонов по накопленной статистике
    daily_stats за последние N дней, определяет уровень Высокая/Средняя/Низкая."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_be
    from datetime import date as _date_be, timedelta as _timedelta_be

    db = SessionLocal()
    try:
        feed_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not feed_row:
            return {"status": "no_data"}
        feed_items = _json_be.loads(feed_row.value)
        matching_ids = {str(it.get("id")) for it in feed_items if it.get("source_batch_id") == batch_id}

        if not matching_ids:
            return {"status": "no_data"}

        total_views = 0
        total_contacts = 0
        for i in range(days):
            d = (_date_be.today() - _timedelta_be(days=i)).isoformat()
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == f"daily_stats:{d}").first()
            if not row:
                continue
            data = _json_be.loads(row.value)
            for it in data.get("items", []):
                if str(it.get("id")) in matching_ids:
                    total_views += it.get("views", 0)
                    total_contacts += it.get("contacts", 0)

        if total_views == 0:
            return {"status": "no_data"}

        conversion = round(total_contacts / total_views * 100, 1)
        if conversion >= 5:
            level = "Высокая"
        elif conversion >= 1.5:
            level = "Средняя"
        else:
            level = "Низкая"

        return {
            "status": "ok",
            "level": level,
            "conversion": conversion,
            "views": total_views,
            "contacts": total_contacts,
            "days": days,
            "items_count": len(matching_ids)
        }
    finally:
        db.close()


@router.get("/republish_check")
def republish_check(account_id: str, min_views_no_contact: int = None, zero_views_days: int = None):
    """Находит объявления-кандидаты на снятие: 1) набрали min_views_no_contact+ просмотров
    и 0 обращений (накопительно с публикации), 2) 0 просмотров за последние zero_views_days дней.
    Пороги берутся из republish_settings аккаунта, если параметры не переданы явно."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_rc
    from datetime import date as _date_rc, timedelta as _timedelta_rc

    db = SessionLocal()
    try:
        settings_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "republish_settings").first()
        settings = _json_rc.loads(settings_row.value) if settings_row else {"min_views_no_contact": 10, "zero_views_days": 7, "enabled": True}
        if not settings.get("enabled", True):
            return {"status": "ok", "candidates": [], "disabled": True}
        if min_views_no_contact is None:
            min_views_no_contact = settings.get("min_views_no_contact", 10)
        if zero_views_days is None:
            zero_views_days = settings.get("zero_views_days", 7)

        feed_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not feed_row:
            return {"status": "ok", "candidates": []}
        items = _json_rc.loads(feed_row.value)
        item_ids = {it["id"] for it in items}

        cumulative_stats = {iid: {"views": 0, "contacts": 0} for iid in item_ids}
        recent_7d_views = {iid: 0 for iid in item_ids}

        for i in range(30):
            d = (_date_rc.today() - _timedelta_rc(days=i)).isoformat()
            row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == f"daily_stats:{d}").first()
            if not row:
                continue
            data = _json_rc.loads(row.value)
            for it in data.get("items", []):
                iid = str(it.get("id"))
                if iid in cumulative_stats:
                    cumulative_stats[iid]["views"] += it.get("views", 0)
                    cumulative_stats[iid]["contacts"] += it.get("contacts", 0)
                    if i < zero_views_days:
                        recent_7d_views[iid] += it.get("views", 0)

        import re as _re_rc
        import time as _time_rc

        def _item_age_days(item_id):
            m = _re_rc.search(r'-(\d{10})-\d+$', item_id)
            if not m:
                m = _re_rc.search(r'-(\d{10})$', item_id)
            if not m:
                return None
            published_ts = int(m.group(1))
            return (_time_rc.time() - published_ts) / 86400

        candidates = []
        for it in items:
            iid = it["id"]
            stats = cumulative_stats.get(iid, {"views": 0, "contacts": 0})
            age_days = _item_age_days(iid)
            reason = None
            if stats["views"] >= min_views_no_contact and stats["contacts"] == 0:
                reason = f"{stats['views']} просмотров и 0 обращений"
            elif age_days is not None and age_days >= zero_views_days and recent_7d_views.get(iid, 0) == 0:
                reason = f"0 просмотров за последние {zero_views_days} дней (объявлению {int(age_days)} дн.)"
            if reason:
                candidates.append({
                    "id": iid, "title": it.get("title", ""), "reason": reason,
                    "views": stats["views"], "contacts": stats["contacts"]
                })

        return {"status": "ok", "candidates": candidates, "total_checked": len(items)}
    finally:
        db.close()


class RepublishApplyRequest(BaseModel):
    account_id: str
    item_ids: list[str]

@router.post("/republish_apply")
def republish_apply(req: RepublishApplyRequest):
    """Снимает указанные неэффективные объявления (через DateEnd - штатный механизм
    Автозагрузки Avito) и создаёт им замену того же направления в том же количестве.
    Требует разрешения автопилота (always_auto) либо явного подтверждения владельца."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_ra, time as _time_ra, random as _random_ra
    from datetime import datetime as _dt_ra

    db = SessionLocal()
    try:
        autopilot_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "autopilot_settings").first()
        autopilot_mode = "always_ask"
        if autopilot_row:
            autopilot_mode = _json_ra.loads(autopilot_row.value).get("mode", "always_ask")

        if autopilot_mode != "always_auto":
            return {
                "status": "needs_confirmation",
                "message": f"Автопилот в режиме '{autopilot_mode}' - для снятия {len(req.item_ids)} объявлений нужно явное подтверждение владельца. Переключите автопилот на 'Всегда автоматически' либо подтвердите вручную.",
                "item_ids": req.item_ids
            }

        feed_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "feed_items").first()
        items = _json_ra.loads(feed_row.value)

        hist_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "gen_ads_history").first()
        gen_ads_history = _json_ra.loads(hist_row.value) if hist_row else []

        now_iso = _dt_ra.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        removed_count = 0
        new_items = []

        for it in items:
            if it["id"] in req.item_ids:
                it["date_end"] = now_iso
                removed_count += 1

                id_prefix = "-".join(it["id"].split("-")[:2])
                direction_words = id_prefix.replace("boris-", "").replace("-", " ")
                matching_batches = [b for b in gen_ads_history if any(w in b.get("topic", "").lower() for w in direction_words.split() if len(w) > 3)]
                if matching_batches and matching_batches[0].get("ads"):
                    source = _random_ra.choice(matching_batches[0]["ads"])
                    new_item = dict(it)
                    new_item["id"] = f"{id_prefix}-{int(_time_ra.time())}-{removed_count}"
                    try:
                        new_item["title"] = spin(source.get("title", it["title"]))[:100]
                    except Exception:
                        new_item["title"] = source.get("title", it["title"])
                    try:
                        new_item["description"] = spin(source.get("description", it["description"]))
                    except Exception:
                        new_item["description"] = source.get("description", it["description"])
                    new_item["price"] = source.get("price", it["price"])
                    new_item["date_end"] = ""
                    new_item.pop("source_batch_id", None)
                    new_items.append(new_item)

        items.extend(new_items)
        feed_row.value = _json_ra.dumps(items, ensure_ascii=False)
        db.commit()

        _audit_log(req.account_id, "republish_apply", f"Снято {removed_count} неэффективных объявлений, создано {len(new_items)} замен", actor="boris_auto")

        return {"status": "ok", "removed": removed_count, "replaced": len(new_items)}
    finally:
        db.close()


class KpiSettingsRequest(BaseModel):
    account_id: str
    target_leads_per_day: float = 0
    max_cost_per_lead_rub: float = 0
    daily_budget_limit_rub: float = 0  # суточный лимит бюджета (обязателен для активации Советника)
    bid_autopilot: bool = False  # автопилот ставок: Борис сам меняет ставки
    lead_temperature: str = "любые"  # "горячие" | "тёплые" | "холодные" | "любые"

@router.get("/kpi_settings")
def get_kpi_settings(account_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_kpi

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "kpi_settings").first()
        if not row:
            return {"status": "ok", "settings": None}
        return {"status": "ok", "settings": _json_kpi.loads(row.value)}
    finally:
        db.close()

@router.post("/set_kpi_settings")
def set_kpi_settings(req: KpiSettingsRequest):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_kpi

    db = SessionLocal()
    try:
        settings = {
            "target_leads_per_day": req.target_leads_per_day,
            "max_cost_per_lead_rub": req.max_cost_per_lead_rub,
            "daily_budget_limit_rub": req.daily_budget_limit_rub,
            "bid_autopilot": req.bid_autopilot,
            "lead_temperature": req.lead_temperature
        }
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "kpi_settings").first()
        if row:
            row.value = _json_kpi.dumps(settings, ensure_ascii=False)
        else:
            row = Storage(account_id=req.account_id, key="kpi_settings", value=_json_kpi.dumps(settings, ensure_ascii=False))
            db.add(row)

        # ОБРАТНАЯ СИНХРОНИЗАЦИЯ: "Автопилот ставок" (почасовой, Советник) и "Автопилот всегда
        # включён" (ежедневный, KPI) — один переключатель для клиента на двух экранах.
        autopilot_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "autopilot_settings").first()
        new_mode = "always_auto" if req.bid_autopilot else "always_ask"
        if autopilot_row:
            try:
                ap_cfg = _json_kpi.loads(autopilot_row.value)
            except Exception:
                ap_cfg = {}
            # не перезаписываем режим "dates" случайно при включении bid_autopilot=False,
            # если клиент явно настроил период отпуска — трогаем только когда включаем автопилот
            if req.bid_autopilot or ap_cfg.get("mode") != "dates":
                ap_cfg["mode"] = new_mode
                autopilot_row.value = _json_kpi.dumps(ap_cfg, ensure_ascii=False)
        else:
            autopilot_row = Storage(account_id=req.account_id, key="autopilot_settings",
                                     value=_json_kpi.dumps({"mode": new_mode}, ensure_ascii=False))
            db.add(autopilot_row)

        db.commit()
        _audit_log(req.account_id, "set_kpi_settings", f"Цель: {req.target_leads_per_day} лидов/день, макс {req.max_cost_per_lead_rub}₽/лид, {req.lead_temperature}", actor="user")
        return {"status": "ok"}
    finally:
        db.close()


@router.post("/delete_kpi_settings")
def delete_kpi_settings(account_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "kpi_settings").first()
        if row:
            db.delete(row)
            db.commit()
        _audit_log(account_id, "delete_kpi_settings", "Цель по лидам удалена", actor="user")
        return {"status": "ok"}
    finally:
        db.close()


class RepublishSettingsRequest(BaseModel):
    account_id: str
    min_views_no_contact: int = 10
    zero_views_days: int = 7
    enabled: bool = True

@router.get("/republish_settings")
def get_republish_settings(account_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_rs

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "republish_settings").first()
        if not row:
            return {"status": "ok", "settings": {"min_views_no_contact": 10, "zero_views_days": 7, "enabled": True}}
        return {"status": "ok", "settings": _json_rs.loads(row.value)}
    finally:
        db.close()

@router.post("/set_republish_settings")
def set_republish_settings(req: RepublishSettingsRequest):
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_rs

    db = SessionLocal()
    try:
        settings = {
            "min_views_no_contact": req.min_views_no_contact,
            "zero_views_days": req.zero_views_days,
            "enabled": req.enabled
        }
        row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "republish_settings").first()
        if row:
            row.value = _json_rs.dumps(settings, ensure_ascii=False)
        else:
            row = Storage(account_id=req.account_id, key="republish_settings", value=_json_rs.dumps(settings, ensure_ascii=False))
            db.add(row)
        db.commit()
        _audit_log(req.account_id, "set_republish_settings", f"Пороги: {req.min_views_no_contact} просмотров/0 контактов, {req.zero_views_days} дней, включено={req.enabled}", actor="user")
        return {"status": "ok"}
    finally:
        db.close()

@router.post("/delete_republish_settings")
def delete_republish_settings(account_id: str):
    from app.db.session import SessionLocal
    from app.models.storage import Storage

    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "republish_settings").first()
        if row:
            db.delete(row)
            db.commit()
        _audit_log(account_id, "delete_republish_settings", "Настройки перепубликации сброшены к дефолту", actor="user")
        return {"status": "ok"}
    finally:
        db.close()


class KpiPlanExecuteRequest(BaseModel):
    account_id: str
    id_prefix: str
    confirm: bool = False

@router.post("/kpi_plan_execute")
def kpi_plan_execute(req: KpiPlanExecuteRequest):
    """Формирует план действий для достижения KPI-цели по конкретному направлению
    (id_prefix) и, при confirm=true, выполняет его: генерирует УСИЛЕННЫЙ копирайтинг
    (не переиспользует старые шаблоны) и применяет к активным объявлениям направления,
    либо снимает неэффективные через republish_apply."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_kpe, httpx as _httpx_kpe, random as _random_kpe

    db = SessionLocal()
    try:
        feed_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "feed_items").first()
        if not feed_row:
            return {"status": "error", "message": "У аккаунта нет объявлений"}
        items = _json_kpe.loads(feed_row.value)
        matching = [it for it in items if it["id"].startswith(req.id_prefix)]
        if not matching:
            return {"status": "error", "message": f"Не найдены объявления с префиксом '{req.id_prefix}'"}

        prices = [it.get("price", 0) for it in matching]
        price_from, price_to = min(prices), max(prices)
        if price_from == price_to:
            price_to = int(price_from * 1.5)
        _prefix_parts = req.id_prefix.split("-")
        _type_word = _prefix_parts[1] if len(_prefix_parts) > 1 else req.id_prefix
        _direction_map = {"shkaf": "шкафы-купе", "kuhnya": "кухни", "beton": "товарный бетон", "plan": "товары"}
        direction = _direction_map.get(_type_word, _type_word)

        plan = {
            "direction": direction,
            "affected_items": len(matching),
            "steps": [
                f"Сгенерировать усиленный копирайтинг для направления «{direction}» (чёткая структура боль→решение→CTA, конкретная выгода в заголовке, без выдуманных фактов)",
                f"Применить новые тексты и цены к {len(matching)} активным объявлениям этого направления",
                "Обновить XML-фид"
            ]
        }

        if not req.confirm:
            return {"status": "plan_ready", "plan": plan}

        # САМООБУЧЕНИЕ: смотрим, какие партии этого аккаунта уже показали лучшую конверсию,
        # и передаём их как образец успеха в промпт нового копирайтинга
        learned_examples = ""
        hist_row_learn = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "gen_ads_history").first()
        if hist_row_learn:
            history_batches = _json_kpe.loads(hist_row_learn.value)
            scored_batches = []
            for b in history_batches[:10]:
                try:
                    eff_resp = _httpx_kpe.get(f"http://127.0.0.1:8000/api/avito/batch_effectiveness?account_id={req.account_id}&batch_id={b['id']}&days=30", timeout=15)
                    eff_data = eff_resp.json()
                    if eff_data.get("status") == "ok" and eff_data.get("conversion", 0) > 0:
                        scored_batches.append((eff_data["conversion"], b))
                except Exception:
                    pass
            scored_batches.sort(key=lambda x: -x[0])
            if scored_batches:
                best_batch = scored_batches[0][1]
                best_titles = [ad.get("title", "") for ad in best_batch.get("ads", [])[:3]]
                learned_examples = (
                    f"\n\nУ этого аккаунта уже была партия с конверсией {scored_batches[0][0]}% — "
                    f"вот примеры её заголовков, которые сработали хорошо (используй похожий СТИЛЬ и СТРУКТУРУ, "
                    f"но не копируй дословно): {'; '.join(best_titles)}"
                )

        strong_copy_prompt_extra = (
            "ВАЖНО: используй сильный продающий копирайтинг — чёткая структура боль клиента → "
            "наше решение → конкретная выгода → явный призыв к действию (CTA). Заголовок должен "
            "сразу называть товар И содержать конкретную выгоду (не просто название). Никогда не "
            "выдумывай факты (гарантии, скидки, сроки), которых нет в исходных данных."
        ) + learned_examples
        ads_resp = _httpx_kpe.post("http://127.0.0.1:8000/api/avito/generate_ads", json={
            "topic": direction, "count": min(len(matching), 10),
            "price_from": price_from, "price_to": price_to,
            "extra": strong_copy_prompt_extra, "goal": "message", "length": "medium",
            "use_my_ads": False, "account_id": req.account_id
        }, timeout=180)
        ads_data = ads_resp.json()
        if ads_data.get("status") != "ok":
            return {"status": "error", "message": f"Не удалось сгенерировать усиленный копирайтинг: {ads_data.get('message')}"}
        new_ads = ads_data["ads"]

        import time as _time_kpe
        hist_batch = {
            "id": int(_time_kpe.time() * 1000),
            "topic": f"{direction} (усиленный копирайтинг KPI)",
            "created_at": __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "ads": new_ads
        }
        hist_row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == "gen_ads_history").first()
        if hist_row:
            history = _json_kpe.loads(hist_row.value)
            history = [hist_batch] + history
            hist_row.value = _json_kpe.dumps(history, ensure_ascii=False)
        else:
            hist_row = Storage(account_id=req.account_id, key="gen_ads_history", value=_json_kpe.dumps([hist_batch], ensure_ascii=False))
            db.add(hist_row)

        updated_count = 0
        for it in matching:
            source = _random_kpe.choice(new_ads)
            try:
                it["title"] = spin(source.get("title", it["title"]))[:100]
            except Exception:
                it["title"] = source.get("title", it["title"])[:100]
            try:
                it["description"] = spin(source.get("description", it["description"]))
            except Exception:
                it["description"] = source.get("description", it["description"])
            it["price"] = source.get("price", it["price"])
            it["source_batch_id"] = hist_batch["id"]
            updated_count += 1

        feed_row.value = _json_kpe.dumps(items, ensure_ascii=False)
        db.commit()

        _audit_log(req.account_id, "kpi_plan_execute", f"Усиленный копирайтинг применён к {updated_count} объявлениям направления '{direction}'", actor="boris_kpi")

        return {"status": "ok", "updated": updated_count, "direction": direction, "batch_id": hist_batch["id"]}
    finally:
        db.close()


@router.post("/kpi_autopilot_run")
def kpi_autopilot_run(account_id: str):
    """Проверяет KPI-цель аккаунта и, ЕСЛИ автопилот включён на 'always_auto',
    автоматически выполняет рекомендованное действие без ручного одобрения.
    Предназначен для вызова по расписанию (cron), аналогично daily_stats_collector."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_kar, httpx as _httpx_kar

    db = SessionLocal()
    try:
        autopilot_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "autopilot_settings").first()
        autopilot_mode = _json_kar.loads(autopilot_row.value).get("mode", "always_ask") if autopilot_row else "always_ask"
        if autopilot_mode != "always_auto":
            return {"status": "skipped", "reason": f"Автопилот в режиме '{autopilot_mode}', требуется 'always_auto' для автозапуска"}

        kpi_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "kpi_settings").first()
        if not kpi_row:
            return {"status": "skipped", "reason": "Цель по лидам не задана"}

        check_resp = _httpx_kar.get(f"http://127.0.0.1:8000/api/avito/kpi_check?account_id={account_id}", timeout=30)
        check_data = check_resp.json()
        if check_data.get("status") != "ok":
            return {"status": "skipped", "reason": check_data.get("message", "kpi_check вернул статус не ok")}

        suggested_action = check_data.get("suggested_action")
        if suggested_action == "none_goal_met":
            return {"status": "ok", "action_taken": "none", "reason": "Цель уже достигается"}

        feed_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
        if not feed_row:
            return {"status": "skipped", "reason": "У аккаунта нет объявлений"}
        items = _json_kar.loads(feed_row.value)
        id_prefixes = set()
        for it in items:
            parts = it["id"].split("-")
            if len(parts) >= 3 and parts[0] == "boris":
                id_prefixes.add("-".join(parts[:3]))

        actions_taken = []

        if suggested_action == "republish_apply":
            republish_resp = _httpx_kar.post("http://127.0.0.1:8000/api/avito/republish_apply", json={
                "account_id": account_id,
                "item_ids": [c["id"] for c in _httpx_kar.get(f"http://127.0.0.1:8000/api/avito/republish_check?account_id={account_id}", timeout=30).json().get("candidates", [])]
            }, timeout=60)
            actions_taken.append({"action": "republish_apply", "result": republish_resp.json()})

        elif suggested_action == "edit_active_listings_review":
            for prefix in id_prefixes:
                plan_resp = _httpx_kar.post("http://127.0.0.1:8000/api/avito/kpi_plan_execute", json={
                    "account_id": account_id, "id_prefix": prefix, "confirm": True
                }, timeout=180)
                actions_taken.append({"action": "kpi_plan_execute", "id_prefix": prefix, "result": plan_resp.json()})

        _audit_log(account_id, "kpi_autopilot_run", f"Автоматически выполнено: {suggested_action}, действий: {len(actions_taken)}", actor="boris_kpi_auto")

        return {"status": "ok", "suggested_action": suggested_action, "actions_taken": actions_taken}
    finally:
        db.close()


@router.get("/kpi_check")
def kpi_check(account_id: str):
    """Сравнивает реальные показатели (лиды сегодня, стоимость лида) с целью KPI
    и возвращает конкретную рекомендацию, что сделать дальше."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_kc
    from datetime import date as _date_kc, timedelta as _timedelta_kc

    db = SessionLocal()
    try:
        kpi_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "kpi_settings").first()
        if not kpi_row:
            return {"status": "no_goal", "message": "Цель по лидам не задана — задайте её во вкладке Автопилот, чтобы Борис мог давать рекомендации."}
        kpi = _json_kc.loads(kpi_row.value)
        target_leads = kpi.get("target_leads_per_day", 0)
        max_cpl = kpi.get("max_cost_per_lead_rub", 0)

        today = _date_kc.today().isoformat()
        yesterday = (_date_kc.today() - _timedelta_kc(days=1)).isoformat()
        today_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == f"daily_stats:{today}").first()
        yesterday_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == f"daily_stats:{yesterday}").first()

        contacts_today = 0
        if today_row:
            data = _json_kc.loads(today_row.value)
            for it in data.get("items", []):
                contacts_today += it.get("contacts", 0)

        cost_per_lead = None
        if today_row and yesterday_row:
            today_data = _json_kc.loads(today_row.value)
            yesterday_data = _json_kc.loads(yesterday_row.value)
            prev_balance = yesterday_data.get("balance", {}).get("real", 0)
            curr_balance = today_data.get("balance", {}).get("real", 0)
            spent = max(prev_balance - curr_balance, 0)
            if contacts_today > 0 and spent > 0:
                cost_per_lead = round(spent / contacts_today, 2)

        leads_gap = target_leads - contacts_today
        recommendation = None
        suggested_action = None

        if target_leads == 0:
            recommendation = "Цель не задана корректно (0 лидов/день) — уточните желаемое количество лидов."
        elif contacts_today >= target_leads and (cost_per_lead is None or max_cpl == 0 or cost_per_lead <= max_cpl):
            recommendation = f"Цель достигается: {contacts_today} лидов сегодня при цели {target_leads}. Дополнительных действий не требуется."
            suggested_action = "none_goal_met"
        elif cost_per_lead is not None and max_cpl > 0 and cost_per_lead > max_cpl:
            recommendation = f"Стоимость лида ({cost_per_lead}₽) выше цели ({max_cpl}₽) — рекомендуется улучшить конверсию: обновить тексты/баннеры через 'Перепубликацию неэффективных' или отредактировать активные объявления, а не увеличивать количество."
            suggested_action = "edit_active_listings_review"
        else:
            recommendation = f"Не хватает лидов: {contacts_today} из {target_leads} в день. Рекомендуется: 1) проверить и снять неэффективные объявления через 'Перепубликацию', 2) при возможности увеличить число активных объявлений."
            suggested_action = "republish_apply"

        return {
            "status": "ok",
            "target_leads_per_day": target_leads,
            "max_cost_per_lead_rub": max_cpl,
            "contacts_today": contacts_today,
            "cost_per_lead_today": cost_per_lead,
            "leads_gap": leads_gap,
            "recommendation": recommendation,
            "suggested_action": suggested_action
        }
    finally:
        db.close()


@router.get("/wordstat_analyze")
def wordstat_analyze(query: str, account_id: str = None):
    """Анализ спроса по Яндекс.Вордстату: топ запросов, объём, похожие/коммерческие
    запросы для указанной ниши. Использует официальный API Вордстата."""
    import os as _os_ws, httpx as _httpx_ws
    from datetime import datetime as _dt_ws

    token = _os_ws.environ.get("YANDEX_WORDSTAT_TOKEN")
    if not token:
        return {"status": "error", "message": "YANDEX_WORDSTAT_TOKEN не настроен в .env"}

    try:
        # У api.wordstat.yandex.net сертификат не включает этот поддомен в SAN
        # (только wordstat.yandex.ru и региональные варианты) - это особенность
        # официального сервера Яндекса, не наша проблема. Отключаем проверку ИМЕНИ
        # хоста, но оставляем проверку подлинности самого сертификата (verify=True).
        import ssl as _ssl_ws
        _ctx = _ssl_ws.create_default_context()
        _ctx.check_hostname = False
        resp = _httpx_ws.post(
            "https://api.wordstat.yandex.net/v1/topRequests",
            headers={"Content-type": "application/json;charset=utf-8", "Authorization": f"Bearer {token}"},
            json={"phrase": query, "regions": [225], "devices": ["all"]},
            timeout=30,
            verify=_ctx
        )
        if resp.status_code == 403:
            return {"status": "error", "message": "Доступ к API Вордстата ещё не одобрен Яндексом (заявка на рассмотрении) или квота исчерпана"}
        if resp.status_code != 200:
            return {"status": "error", "message": f"Wordstat API ошибка {resp.status_code}: {resp.text[:300]}"}

        data = resp.json()
        total = data.get("totalCount", 0)
        top_requests = data.get("topRequests", [])
        associations = data.get("associations", [])

        # Простая эвристика коммерческого интента: наличие слов типа "купить", "цена", "заказать" и т.д.
        commercial_markers = ["купить", "цена", "заказать", "стоимость", "недорого", "с доставкой", "оптом", "прайс", "заказ"]
        commercial_requests = [r for r in top_requests if any(m in r.get("phrase", "").lower() for m in commercial_markers)]
        commercial_share = round(len(commercial_requests) / len(top_requests) * 100, 1) if top_requests else 0

        result = {
            "status": "ok",
            "query": query,
            "total_count": total,
            "top_requests": top_requests[:20],
            "associations": associations[:20],
            "commercial_requests": commercial_requests,
            "commercial_share_percent": commercial_share,
            "checked_at": _dt_ws.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        }

        if account_id:
            from app.db.session import SessionLocal
            from app.models.storage import Storage
            import json as _json_ws
            db = SessionLocal()
            try:
                key = f"wordstat_analysis:{query}"
                row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
                if row:
                    row.value = _json_ws.dumps(result, ensure_ascii=False)
                else:
                    row = Storage(account_id=account_id, key=key, value=_json_ws.dumps(result, ensure_ascii=False))
                    db.add(row)
                db.commit()
            finally:
                db.close()

        return result
    except Exception as e:
        return {"status": "error", "message": str(e)[:300]}


@router.get("/wordstat_history")
def wordstat_history(account_id: str):
    """Список всех ранее сохранённых анализов ниш/запросов для аккаунта."""
    from app.db.session import SessionLocal
    from app.models.storage import Storage
    import json as _json_wh

    db = SessionLocal()
    try:
        rows = db.query(Storage).filter(Storage.account_id == account_id, Storage.key.like("wordstat_analysis:%")).all()
        results = [_json_wh.loads(r.value) for r in rows]
        results.sort(key=lambda x: x.get("checked_at", ""), reverse=True)
        return {"status": "ok", "analyses": results}
    finally:
        db.close()


@router.get("/director/new_clients")
def new_clients_overview(_=_DepSec(_ReqOwner)):
    """Список клиентов-пользователей для панели Директора: кто зарегался, когда,
    сколько дней триала осталось, сколько аккаунтов подключил. Owner в список не входит."""
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.models.account import Account
    from datetime import datetime as _dt, timezone as _tz
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.role == "client").order_by(User.created_at.desc()).all()
        now = _dt.now(_tz.utc)
        result = []
        for u in users:
            acc_count = db.query(Account).filter(Account.owner_user_id == u.id).count()
            days_left = None
            status = "нет подписки"
            exp = u.subscription_expires_at
            if exp is not None:
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=_tz.utc)
                delta = (exp - now).days
                days_left = delta
                if delta < 0:
                    status = "истёк"
                elif delta <= 4:
                    status = "триал"
                else:
                    status = "активна"
            result.append({
                "id": u.id,
                "email": u.email,
                "account_id": u.account_id,
                "registered_at": u.created_at.isoformat() if u.created_at else None,
                "subscription_expires_at": u.subscription_expires_at.isoformat() if u.subscription_expires_at else None,
                "days_left": days_left,
                "status": status,
                "accounts_count": acc_count,
                "is_active": u.is_active,
            })
        return {"count": len(result), "clients": result}
    finally:
        db.close()



def _cat_for_feed(item):
    """Валидное для Avito имя тега <Category> — 2-й сегмент пути дерева."""
    raw = (getattr(item, "category", "") or "").strip()
    niche = (getattr(item, "category_id", "") or "").strip()
    tid = getattr(item, "template_id", None)
    try:
        got = _normalize_category(raw, niche or None, tid)
        if got:
            return got
    except Exception:
        pass
    key = niche or raw
    if not key:
        return raw
    _SL = globals().get("SessionLocal")
    if _SL is None:
        for _mod in ("app.db.session", "app.database", "app.db"):
            try:
                _SL = getattr(__import__(_mod, fromlist=["SessionLocal"]), "SessionLocal")
                break
            except Exception:
                continue
    if _SL is None:
        return raw
    try:
        from sqlalchemy import text as _t
        db = _SL()
        try:
            r = db.execute(_t(
                "select coalesce(nullif(category_path, \'\'), category_name) "
                "from category_templates "
                "where category_id ilike :k or category_name ilike :k "
                "order by (case when coalesce(category_path, category_name) like :g "
                "then 0 else 1 end) limit 1"), {"k": "%" + key + "%", "g": "%>%"}).fetchone()
        finally:
            db.close()
        if r and r[0] and ">" in r[0]:
            parts = [p.strip() for p in r[0].split(">")]
            if len(parts) >= 2 and parts[1]:
                return parts[1]
    except Exception:
        pass
    return raw


# ============ НАСТРОЙКИ ПРОДАВЦА ============
# Эти теги встречаются почти у всех категорий Avito и одинаковы для всех
# объявлений клиента. Спрашиваем ОДИН РАЗ, дальше подставляем всюду.
# Замер по 660 шаблонам: ContactMethod 100%, InternetCalls 97%,
# TargetAudience 88%, Condition 73%, AdType 68%.

SELLER_TAGS = [
    ("ContactMethod",   "Как с вами связываться"),
    ("InternetCalls",   "Принимать звонки через интернет"),
    ("TargetAudience",  "Кому продаёте"),
    ("Condition",       "Состояние товара"),
    ("AdType",          "Происхождение товара"),
    ("Availability",    "Наличие"),
    ("DeliverySubsidy", "Компенсация доставки Avito"),
    # Тоже настройки магазина, а не товара: одинаковы для всех объявлений
    # клиента. Спрашиваются один раз и больше в этом аккаунте не повторяются.
    ("Delivery",        "Доставка"),
    ("ReturnPolicy",    "Условия возврата"),
    ("MultiItem",       "Продаёте несколько одинаковых"),
]


@router.get("/seller_defaults")
def get_seller_defaults(account_id: str):
    """Вопросы настроек продавца с вариантами + сохранённые ответы."""
    import json as _j
    from collections import Counter as _C
    from app.db.session import SessionLocal as _SL
    from app.models.storage import Storage as _St
    from app.models.category_template import CategoryTemplate as _CT
    db = _SL()
    try:
        row = db.query(_St).filter(_St.account_id == account_id,
                                   _St.key == "seller_defaults").first()
        saved = {}
        if row and row.value:
            try:
                saved = _j.loads(row.value) or {}
            except Exception:
                saved = {}

        seen = {t: _C() for t, _ in SELLER_TAGS}
        for tpl in db.query(_CT).all():
            try:
                fields = _j.loads(tpl.required_fields or "[]")
            except Exception:
                continue
            for f in fields:
                tag = f.get("tag")
                av = f.get("allowed_values") or []
                if tag in seen and len(av) > 1:
                    seen[tag][tuple(av)] += 1

        questions = []
        for tag, label in SELLER_TAGS:
            if not seen[tag]:
                continue
            options = list(seen[tag].most_common(1)[0][0])
            questions.append({"tag": tag, "label": label, "options": options,
                              "value": saved.get(tag, ""),
                              "coverage": sum(seen[tag].values())})
        return {"status": "ok", "questions": questions, "saved": saved}
    finally:
        db.close()


class SellerDefaultsRequest(BaseModel):
    account_id: str
    values: dict


@router.post("/seller_defaults")
def set_seller_defaults(req: SellerDefaultsRequest):
    """Сохраняет ответы. Посторонние теги отбрасываются."""
    import json as _j
    from app.db.session import SessionLocal as _SL
    from app.models.storage import Storage as _St
    db = _SL()
    try:
        allowed_tags = {t for t, _ in SELLER_TAGS}
        clean = {k: v for k, v in (req.values or {}).items()
                 if k in allowed_tags and str(v).strip()}
        row = db.query(_St).filter(_St.account_id == req.account_id,
                                   _St.key == "seller_defaults").first()
        if row:
            row.value = _j.dumps(clean, ensure_ascii=False)
        else:
            db.add(_St(account_id=req.account_id, key="seller_defaults",
                       value=_j.dumps(clean, ensure_ascii=False)))
        db.commit()
        return {"status": "ok", "saved": clean, "count": len(clean)}
    finally:
        db.close()
