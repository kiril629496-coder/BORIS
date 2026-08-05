from fastapi import APIRouter
from pydantic import BaseModel
import os, json, requests

router = APIRouter(prefix="/api/prompt", tags=["prompt"])

KINDS = {
    "posts": "промт для автопостинга в Telegram и ВКонтакте",
    "banner": "промт для баннера/картинки к посту",
    "manager": "промт для ИИ-менеджера, который отвечает покупателям на Авито",
    "listings": "промт для генерации объявлений на Авито",
    "rop": "промт для разбора звонков (ИИ РОП)",
    "other": "промт для ИИ",
}

LIMITS = (
    "ОГРАНИЧЕНИЯ ПЛАТФОРМЫ (учитывай при разборе):\n"
    "- У ИИ нет доступа в интернет во время генерации: указания «посмотри в интернете», «возьми с сайтов», «изучи тренды» невыполнимы.\n"
    "- ИИ не видит лендинг, базу знаний и кабинет: все факты, цифры и правила должны быть прямо в промте.\n"
    "- Цифры и формулировки ИИ повторяет дословно — опечатка в цене попадёт в пост.\n"
    "- В постах Telegram и ВК не работает markdown: **жирный**, ##, __ видны как мусор.\n"
    "- Ссылки и контакты подставляются системой автоматически, просить их у ИИ не нужно.\n"
    "- Одна генерация = один пост, ИИ не ведёт рубрики и не помнит прошлые посты.\n"
)


class CheckBody(BaseModel):
    text: str = ""
    kind: str = "other"
    account_id: str = ""


@router.post("/check")
def check_prompt(body: CheckBody):
    text = (body.text or "").strip()
    if len(text) < 10:
        return {"status": "empty", "message": "Промт пустой или слишком короткий — напишите хотя бы пару предложений."}
    kind = KINDS.get(body.kind, KINDS["other"])
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"status": "error", "message": "нет ключа OpenAI"}
    try:
        from proxy_pool import get_intl_requests_proxies
        proxies = get_intl_requests_proxies()
    except Exception:
        proxies = None

    sys_prompt = (
        "Ты — опытный редактор промтов. Разбираешь " + kind + ", который написал владелец бизнеса.\n\n"
        + LIMITS +
        "\nЗАДАЧА: найди проблемы и перепиши промт лучше.\n"
        "Ищи: невыполнимые требования, противоречия, опечатки в цифрах и ценах, расплывчатые формулировки, "
        "отсутствие важных фактов, лишнее многословие.\n\n"
        "Верни СТРОГО JSON без пояснений и без markdown:\n"
        '{"verdict":"коротко одной фразой, насколько промт рабочий",'
        '"issues":[{"problem":"что не так","fix":"как исправить"}],'
        '"improved":"полностью переписанный промт, готовый к вставке"}'
    )
    try:
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-5.4",
                  "messages": [{"role": "user", "content": sys_prompt + "\n\nПРОМТ КЛИЕНТА:\n" + text}],
                  "max_completion_tokens": 2000},
            proxies=proxies, timeout=180)
        data = resp.json()
        try:  # учёт расхода: прямой вызов идёт мимо пула
            from app.usage import log_usage as _lu
            _u = data.get("usage") or {}
            _lu(None, "openai", data.get("model") or "gpt-5.4", "system:prompt_check",
                int(_u.get("prompt_tokens") or 0), int(_u.get("completion_tokens") or 0))
        except Exception as _e:
            print("[usage]", str(_e)[:100], flush=True)
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}

    try:
        raw = data["choices"][0]["message"]["content"].strip()
    except Exception:
        return {"status": "error", "message": str(data)[:300]}
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"status": "ok", "verdict": "", "issues": [], "improved": raw}

    try:
        from app.api.calltracking import _lc
        u = data.get("usage", {})
        _lc(body.account_id or "system", "openai", "gpt-5.4", "проверка промта",
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    except Exception:
        pass

    return {"status": "ok",
            "verdict": parsed.get("verdict", ""),
            "issues": parsed.get("issues", []),
            "improved": parsed.get("improved", "")}
