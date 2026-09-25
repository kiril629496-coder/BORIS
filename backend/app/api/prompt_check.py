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
    full_prompt = sys_prompt + "\n\nПРОМТ КЛИЕНТА:\n" + text
    try:
        import hashlib
        from app.api.campaigns import _ff_guarded_openai_response
        account_id = str(body.account_id or "system")
        intent = "prompt-check:" + hashlib.sha256(
            (account_id + "\n" + str(body.kind or "other") + "\n" + full_prompt).encode("utf-8", "ignore")
        ).hexdigest()
        paid = _ff_guarded_openai_response(
            account_id=account_id,
            operation="system:prompt_check",
            model="gpt-5.4",
            input_payload=[{"role": "user", "content": full_prompt}],
            max_output_tokens=2000,
            idempotency_key=intent,
            timeout=180,
        )
        raw = str((paid or {}).get("text") or "").strip()
        if not raw:
            return {"status": "error", "message": "Проверка промта не вернула результат"}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        parsed = json.loads(raw)
    except Exception:
        return {"status": "ok", "verdict": "", "issues": [], "improved": raw}

    return {"status": "ok",
            "verdict": parsed.get("verdict", ""),
            "issues": parsed.get("issues", []),
            "improved": parsed.get("improved", "")}
