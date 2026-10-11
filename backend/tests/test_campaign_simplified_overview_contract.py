from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT.parent/"frontend/app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
CSS=(ROOT.parent/"frontend/app/globals.css").read_text(encoding="utf-8")


def test_campaign_primary_navigation_is_launch_focused():
    assert '{id:"projects",label:"Обзор"}' in UI
    assert '{id:"ads",label:"Объявления"}' in UI
    assert '{id:"history",label:"История"}' in UI
    assert '>A/B-тесты</button>' in UI
    assert 'label="Быстрый запуск"' in UI
    assert 'label="Управление"' in UI
    assert 'label="Ещё"' in UI


def test_campaign_simplified_overview_keeps_secondary_tools_hidden():
    assert 'data-testid="campaign-simplified-overview"' in UI
    assert '>Центр объявлений</h2>' in UI
    assert 'Тексты, фото, публикация и A/B — в одном окне.' in UI
    assert '>Запустить BORIS</Button>' in UI
    assert 'Подготовить объявления' in UI
    assert 'Что BORIS сделал' in UI
    assert 'Активные A/B' in UI
    assert 'workspaceTab==="projects"&&showLaunchWorkspace' in UI
    assert 'Свернуть подготовку' in UI


def test_campaign_secondary_features_live_in_menus():
    for text in (
        "Массовые действия","Автоматизация","Подкатегории Avito","История задач",
        "Мои материалы","Экспорт CSV","Настройки","Как это работает",
    ):
        assert text in UI


def test_campaign_mobile_menu_stays_in_flow():
    assert ".boris-campaign-top-actions" in CSS
    assert ".boris-campaign-menu-panel" in CSS
    block=CSS[CSS.index("/* BORIS Campaign simplified client menus */"):]
    assert "@media(max-width:520px)" in block
    assert "position:relative!important" in block
    assert "width:100%!important" in block
