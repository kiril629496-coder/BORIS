#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "app/messenger/page.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "app/messenger/messenger.module.css").read_text(encoding="utf-8")

checks = {
    "SIMPLIFIED_NAV": all(x in PAGE for x in ('<MessageCircle size={19}','<UsersRound size={19}','<Ellipsis size={20}','styles.navLabel}>Ещё</span>')),
    "NO_DASHBOARD_NAV": all(x not in PAGE for x in ('["work","Работа","⌂"]','["crm","CRM","◆"]','["reports","Отчёты","▥"]')),
    "MORE_MENU": all(x in PAGE for x in ("Задачи и напоминания","Команда и доступы","Уведомления и приложение","Открыть основной BORIS")),
    "NO_GIANT_CHAT_HERO": "<div className={styles.internalHead}>" not in PAGE,
    "APP_RAIL": "@media(min-width:1025px)" in CSS and "width:86px" in CSS,
    "CHAT_LIST": "styles.chatListHead" in PAGE and "styles.chatListTools" in PAGE,
    "CLEAR_NEW_CHAT_FLOW": "styles.newChatMenu" in PAGE and "Новый чат" in PAGE and "Личный чат" in PAGE and "Создать группу" in PAGE,
    "TEAM_ICON": 'thread.isGeneral ? "👥"' in PAGE,
    "CHAT_WIDE_COMPOSER": "BORIS_MESSENGER_SIMPLIFIED_V5" in CSS and "grid-template-columns:minmax(0,1fr) auto!important" in CSS,
    "CHAT_GLASS_BACKGROUND": "backdrop-filter:blur(8px)" in CSS and "radial-gradient(circle at 24px 24px" in CSS,
    "MESSAGE_DENSITY": "max-width:70%" in CSS,
    "DESKTOP_ACTIONS_ON_HOVER": ".internalBubble:hover .messageActionList" in CSS,
    "DESKTOP_NO_REDUNDANT_CHAT_BUTTONS": ".internalHeaderActions{display:none!important}" in CSS,
    "TEAM_LIST_FIRST": "styles.teamMembersList" in PAGE and "styles.teamMemberRow" in PAGE,
    "INVITE_DRAWER": "styles.inviteBackdrop" in PAGE and "styles.inviteDrawer" in PAGE,
    "BACKGROUND_FETCH_QUIET": all(marker not in PAGE for marker in ("Контекст диалога недоступен:", "CRM задачи аккаунта недоступны:", "Внутренние чаты недоступны:")) and "isTransientNetworkError(error)" in PAGE,
    "CLIENT_TASKS_NOT_FULL_CRM": 'aria-label="Этап клиента"' not in PAGE and "Задачи и напоминания" in PAGE and "Режим ответа" in PAGE,
    "TECH_PERMISSION_SANITIZED": "У вашей роли нет доступа к этому разделу." in PAGE,
    "ACCOUNT_SWITCH_CLEAR": all(x in PAGE for x in ("setLeads([])","setInternalThreads([])","setTeamMembers([])","setAccountTasks([])")),
}

failed = [name for name, ok in checks.items() if not ok]
print("MESSENGER_DESIGN_AUDIT")
for name, ok in checks.items():
    print(f"{name}={'PASS' if ok else 'FAIL'}")
print(f"FAILURES={len(failed)}")
if failed:
    raise SystemExit(1)
print("MESSENGER_DESIGN_V5=PASS")
