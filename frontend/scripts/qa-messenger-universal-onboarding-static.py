#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
page=(ROOT/"app/messenger/page.tsx").read_text(encoding="utf-8")
invite=(ROOT/"app/messenger/invite/page.tsx").read_text(encoding="utf-8")
settings=(ROOT/"app/messenger/components/SettingsPanel.tsx").read_text(encoding="utf-8")
guide=(ROOT/"app/messenger/guide/page.tsx").read_text(encoding="utf-8")
css=(ROOT/"app/messenger/messenger.module.css").read_text(encoding="utf-8")
checks=[]

def ck(name, ok):
    checks.append((name,bool(ok)))
    print(("PASS " if ok else "FAIL ")+name)

ck("invite_routes_to_onboarding", 'onboarding=1' in invite and 'boris_post_login_return' in invite)
ck("page_reads_onboarding_query", 'params.get("onboarding") === "1"' in page)
ck("page_opens_settings_first_run", 'setSection("settings")' in page and 'setFirstRunOnboarding(onboarding)' in page)
ck("page_finishes_onboarding_to_chats", 'onFinishOnboarding' in page and 'url.searchParams.delete("onboarding")' in page)
for phrase,name in [
    ("Добро пожаловать в BORIS Messenger","welcome"),
    ("Установите BORIS","install"),
    ("Включите уведомления","push"),
    ("Разрешите микрофон и камеру","media_permissions"),
    ("Перейти к работе","finish"),
    ("Инструкция для сотрудников","settings_guide"),
]:
    ck("settings_"+name, phrase in settings)
ck("settings_guide_href", 'guideHref="/messenger/guide"' in page)
for phrase,name in [
    ("iPhone","iphone"),("Android","android"),("Windows","windows"),("Mac","mac"),
    ("Новый лид","lead"),("CRM и задачи","crm_tasks"),("Push-уведомления","push"),
    ("Командные чаты","team_chats"),("Звонки","calls"),
]:
    ck("guide_"+name, phrase in guide)
for img in ("iphone.jpg","android.jpg","windows.jpg","mac.jpg"):
    ck("guide_image_"+img, (ROOT/"public/messenger-guide"/img).is_file() and (ROOT/"public/messenger-guide"/img).stat().st_size>1000)
lower=(guide+"\n"+settings).lower()
for forbidden in ("планета мебели","planetazayavki","тимофей","альберт"):
    ck("no_client_hardcode_"+forbidden.replace(" ","_"), forbidden not in lower)
ck("onboarding_css_present", "BORIS_MESSENGER_UNIVERSAL_ONBOARDING_V1_20261005" in css)
failed=[n for n,ok in checks if not ok]
print(f"MESSENGER_UNIVERSAL_ONBOARDING_STATIC={'PASS' if not failed else 'FAIL'} checks={len(checks)} failures={len(failed)}")
if failed:
    print("FAILED:",", ".join(failed))
    raise SystemExit(1)
