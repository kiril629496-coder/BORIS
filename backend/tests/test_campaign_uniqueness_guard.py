import json
from types import SimpleNamespace

from app.api import campaigns as C


def _row(item_id, description, *, direction="Кухни на заказ", unique=True):
    payload={
        "title":f"Тест {item_id}",
        "description":description,
        "direction":direction,
    }
    if unique:
        payload["unique_variant"]=f"v:{item_id}"
        payload["uniqueness_strategy"]="full-body"
    return SimpleNamespace(
        id=item_id,
        account_id="acc",
        segment=direction,
        payload_json=json.dumps(payload,ensure_ascii=False),
    )


def test_cosmetic_city_or_footer_change_is_still_duplicate():
    body=(
        "Проектируем мебель под размеры помещения. Сначала проверяем геометрию, "
        "затем согласуем компоновку, материалы, фасады, фурнитуру и внутреннее "
        "наполнение до производства. Пришлите фотографии и размеры помещения, "
        "чтобы начать предметное обсуждение проекта. "
    ) * 3
    a=_row(1,body+"\n\n✦ Пример задачи: Кухня · Уфа. Напишите исходные данные.")
    b=_row(2,body.replace("помещения","комнаты",1)+"\n\n✦ Пример задачи: Кухня · Ижевск. Пришлите вводные.")
    pairs=C._ff_near_duplicate_pairs([a,b])
    assert pairs
    assert {pairs[0]["a"],pairs[0]["b"]}=={1,2}
    assert pairs[0]["ratio"]>=0.86


def test_distinct_selling_bodies_pass_uniqueness_gate():
    a=_row(1,(
        "Начинаем проект кухни с геометрии помещения и проходов. Проверяем размеры "
        "стен, расположение секций и открывание фасадов. Затем согласуем материалы, "
        "цвет и фурнитуру. Фотографии помогают увидеть сложные места и построить "
        "конфигурацию вокруг реального помещения. "
    )*2)
    b=_row(2,(
        "Систему хранения строим от ежедневных привычек. Сначала разбираем, какие "
        "вещи должны быть рядом, что используется редко и где нужен быстрый доступ. "
        "После этого распределяем внутреннее наполнение и только потом обсуждаем "
        "внешний вид будущей мебели. "
    )*2)
    assert C._ff_near_duplicate_pairs([a,b])==[]


def test_legacy_rows_without_uniqueness_contract_are_not_retroactively_blocked():
    body=("Одинаковый старый импортированный текст без нового контракта уникализации. ")*12
    a=_row(1,body,unique=False)
    b=_row(2,body,unique=False)
    assert C._ff_near_duplicate_pairs([a,b])==[]


def test_similarity_normalizer_ignores_cosmetic_footer():
    base="Основная продающая часть объявления про мебель под конкретное помещение."
    a=C._ff_similarity_text(base+"\n\n✦ Пример задачи: Уфа.")
    b=C._ff_similarity_text(base+"\n\n✦ Пример задачи: Ижевск.")
    assert a==b
