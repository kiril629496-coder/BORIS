from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_subcategory_contract_hides_service_only_fields():
    feed=(ROOT/"app/api/feed_contract.py").read_text(encoding="utf-8")
    assert '"AvitoId"' in feed
    assert '"AuctionPrice"' in feed
    assert '"AuctionPriceLastDate"' in feed


def test_subcategory_ui_is_humanized_and_uses_boris_select():
    ui=(ROOT.parent/"frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert "AVITO_FIELD_LABELS" in ui
    assert "AVITO_ADVANCED_OPTIONAL_FIELDS" in ui
    assert "avitoDependencyText" in ui
    assert "Доставка, опт и документы" in ui
    assert "function BorisSelect" in ui
    assert '<select value={bulkTemplateId}' not in ui
    assert '<select value={draft.template_id' not in ui
    block=ui[ui.index("Категория и характеристики Avito"):ui.index("Фото и баннеры для выбранных")]
    assert "BorisSelect" in block
    assert "<select" not in block
    assert "avitoFieldLabel(f)" in block
    assert "avitoDependencyText(f.depends_on" in block


def test_subcategory_ui_hides_singleton_contract_constants_from_questions():
    ui=(ROOT.parent/"frontend/app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert "missingNameSet" in ui
    assert "allowed.length===1" in ui
    assert "there is nothing useful for the client to choose" in ui
