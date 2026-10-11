from pathlib import Path

ROOT=Path("/root/BORIS")
PAGE=ROOT/"frontend/app/messenger/page.tsx"
REPORTS=ROOT/"frontend/app/messenger/components/ReportsPanel.tsx"
CSS=ROOT/"frontend/app/messenger/messenger.module.css"

def test_more_menu_uses_stable_url_sections_and_reload_contract():
    src=PAGE.read_text(encoding="utf-8")
    assert "const openMoreSection" in src
    for section in ("crm","reports","team","settings","security"):
        assert f'openMoreSection("{section}")' in src
    assert 'url.searchParams.set("section", next)' in src
    assert 'window.addEventListener("popstate", applySectionFromUrl)' in src
    assert 'requested === "crm"' in src and 'requested === "security"' in src

def test_primary_nav_clears_stale_more_section_query():
    src=PAGE.read_text(encoding="utf-8")
    assert "const clearMoreSectionUrl" in src
    assert 'url.searchParams.delete("section")' in src
    assert 'clearMoreSectionUrl("chats")' in src
    assert 'clearMoreSectionUrl("clients")' in src

def test_report_chat_picker_is_custom_and_not_native_select():
    ui=REPORTS.read_text(encoding="utf-8")
    css=CSS.read_text(encoding="utf-8")
    assert "reportChatSelectButton" in ui
    assert "reportChatOptions" in ui
    assert 'role="listbox"' in ui
    assert 'role="option"' in ui
    assert "<select" not in ui
    assert "BORIS_MESSENGER_V70_REPORT_CHAT_PICKER" in css


def test_messenger_has_no_native_select_dropdowns_left():
    page=PAGE.read_text(encoding="utf-8")
    css=CSS.read_text(encoding="utf-8")
    assert "<select" not in page
    assert "MessengerSelect" in page
    assert "BORIS_MESSENGER_V70_CUSTOM_SELECT" in css


def test_settings_use_independent_columns_and_accessible_type_scale():
    settings=(ROOT/"frontend/app/messenger/components/SettingsPanel.tsx").read_text(encoding="utf-8")
    css=CSS.read_text(encoding="utf-8")
    assert "settingsColumns" in settings and "settingsColumn" in settings
    assert "BORIS_MESSENGER_V70_SETTINGS_LAYOUT + ACCESSIBLE_TYPE_SCALE" in css
    assert "--ms-font-body:.9375rem" in css
    assert "--ms-font-meta:.875rem" in css
    assert "--ms-font-caption:.8125rem" in css
    assert "--ms-font-display:1.25rem" in css
    assert ".page input,.page textarea,.page select{font-size:1rem!important" in css
    assert ".settingsColumns{grid-template-columns:1fr}" in css
    assert "font-family:var(--font-manrope),var(--font-inter)" in css


def test_thread_list_self_heals_even_when_sse_is_online():
    src=PAGE.read_text(encoding="utf-8")
    assert "THREAD_LIST_RECONCILIATION_V1" in src
    assert "document.visibilityState === \"visible\"" in src
    assert "}, 2500);" in src


def test_viewer_ui_is_fail_closed_for_write_controls():
    src=PAGE.read_text(encoding="utf-8")
    assert 'const currentAccountRole = String(currentAccountAccess?.role || "").toUpperCase()' in src
    assert 'currentAccountRole !== "VIEWER" && Boolean(currentAccountAccess?.can_reply)' in src
    assert 'currentAccountRole !== "VIEWER" && Boolean(currentAccountAccess?.can_manage_team' in src


def test_open_message_history_self_heals_even_when_sse_is_online():
    src=PAGE.read_text(encoding="utf-8")
    assert "MESSAGE_HISTORY_RECONCILIATION_V1" in src
    assert "void loadInternalMessages();" in src


def test_more_menu_does_not_hide_team_item_while_account_permissions_are_loading():
    src=PAGE.read_text(encoding="utf-8")
    assert "!currentAccountAccess && <div className={`${styles.moreMenuItem} ${styles.moreMenuItemLoading}`}" in src
    assert "Проверяем права…" in src
    assert 'canManageTeam && <button className={`${styles.moreMenuItem}' in src


def test_hidden_client_shell_wins_over_responsive_display_rules():
    css=CSS.read_text(encoding="utf-8")
    assert ".shell.shellHidden{display:none!important}" in css


def test_viewer_cannot_open_new_chat_and_deeplink_is_applied_once():
    src=PAGE.read_text(encoding="utf-8")
    assert 'aria-expanded={newChatOpen} disabled={!canReply} onClick={toggleNewChat}' in src
    assert "if (internalDeepLinkAppliedRef.current === key) return;" in src
    assert 'window.history.state?.messengerSection === "clients"' in src
