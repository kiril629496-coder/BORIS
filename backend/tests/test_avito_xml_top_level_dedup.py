from app.api.avito import FeedItem, _build_xml


def _base(**overrides):
    data = dict(
        id="dedup-1",
        title="Тестовое объявление",
        description="Описание",
        price=1000,
        category="Мебель и интерьер",
        address="Уфа",
        images=[],
        params={},
    )
    data.update(overrides)
    return FeedItem(**data)


def test_build_xml_deduplicates_core_tags_already_emitted_from_dedicated_fields():
    item = _base(
        goods_type="Шкафы, комоды и стеллажи",
        goods_subtype="Прихожие и обувницы",
        price_type="за штуку",
        color="Серый",
        ad_type="Товар куплен на продажу",
        condition="Новое",
        params={
            "Category": "Другая категория",
            "GoodsType": "ДУБЛЬ",
            "GoodsSubType": "ДУБЛЬ",
            "PriceType": "ДУБЛЬ",
            "Color": "ДУБЛЬ",
            "AdType": "ДУБЛЬ",
            "Condition": "ДУБЛЬ",
            "FurnitureAdditions": "Зеркало",
        },
    )
    xml = _build_xml([item])

    for tag in ("Category", "GoodsType", "GoodsSubType", "PriceType", "Color", "AdType", "Condition"):
        assert xml.count(f"<{tag}>") == 1, (tag, xml)

    assert "<GoodsType>Шкафы, комоды и стеллажи</GoodsType>" in xml
    assert "<GoodsSubType>Прихожие и обувницы</GoodsSubType>" in xml
    assert "<PriceType>за штуку</PriceType>" in xml
    assert "<FurnitureAdditions>Зеркало</FurnitureAdditions>" in xml
    assert ">ДУБЛЬ<" not in xml


def test_build_xml_keeps_legacy_param_when_dedicated_field_is_empty():
    item = _base(
        goods_type="",
        goods_subtype="",
        price_type="",
        color="",
        params={
            "GoodsType": "Шкафы",
            "GoodsSubType": "Прихожие",
            "PriceType": "за погонный метр",
            "Color": "Бежевый",
        },
    )
    xml = _build_xml([item])

    assert xml.count("<GoodsType>") == 1
    assert "<GoodsType>Шкафы</GoodsType>" in xml
    assert xml.count("<GoodsSubType>") == 1
    assert "<GoodsSubType>Прихожие</GoodsSubType>" in xml
    assert xml.count("<PriceType>") == 1
    assert "<PriceType>за погонный метр</PriceType>" in xml
    assert xml.count("<Color>") == 1
    assert "<Color>Бежевый</Color>" in xml
