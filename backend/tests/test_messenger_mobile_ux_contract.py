from pathlib import Path

ROOT=Path('/root/BORIS')
FRONTEND=ROOT/'frontend/app/messenger/page.tsx'
CSS=ROOT/'frontend/app/messenger/messenger.module.css'

def test_mobile_internal_message_actions_are_collapsed_under_more_menu():
    source=FRONTEND.read_text(encoding='utf-8')
    css=CSS.read_text(encoding='utf-8')
    assert 'messageActionMenuId' in source
    assert 'mobileMessageMore' in source
    assert '>⋯</button>' in source
    assert 'messageActionListOpen' in source
    assert '.mobileMessageMore' in css
    assert 'min-height:44px' in css

def test_client_ownership_actions_use_single_management_menu():
    source=FRONTEND.read_text(encoding='utf-8')
    assert 'ownershipMenuOpen' in source
    assert '>Управление</button>' in source
    assert 'Взять себе' in source
    assert 'Вести вручную' in source
    assert 'Снять назначение' in source
    assert 'Вернуть BORIS' in source


def test_mobile_bottom_navigation_matches_product_contract():
    source = FRONTEND.read_text(encoding="utf-8")
    assert '<MessageCircle size={19}' in source
    assert '<UsersRound size={19}' in source
    assert '<Ellipsis size={20}' in source
    assert 'styles.navLabel}>Ещё</span>' in source
    assert 'Задачи и напоминания' in source
    assert 'Команда и доступы' in source
    assert 'Уведомления и приложение' in source
    assert 'Открыть основной BORIS' in source
    assert '["work","Работа","⌂"]' not in source
    assert '["crm","CRM","◆"]' not in source
