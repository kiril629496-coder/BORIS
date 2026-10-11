from pathlib import Path
import sys

root = Path("/root/BORIS/frontend")
checks = []

def need(path, needle, name):
    text = (root / path).read_text()
    ok = needle in text
    checks.append((name, ok))
    print(f"{name}={'PASS' if ok else 'FAIL'}")
    return ok

need("app/messenger/invite/page.tsx", 'onboarding=1', "INVITE_TO_ONBOARDING")
need("app/messenger/page.tsx", 'params.get("onboarding") === "1"', "ONBOARDING_QUERY_PARSE")
need("app/messenger/page.tsx", 'setSection("settings")', "ONBOARDING_OPENS_SETTINGS")
need("app/messenger/page.tsx", 'guideHref="/messenger/guide"', "GUIDE_LINK_WIRED")
need("app/messenger/page.tsx", 'onFinishOnboarding', "FINISH_HANDLER_WIRED")
need("app/messenger/page.tsx", 'Скопировать всё для сотрудника', "OWNER_COPY_PACKAGE")
need("app/messenger/components/SettingsPanel.tsx", 'Добро пожаловать в BORIS Messenger', "WELCOME_UI")
need("app/messenger/components/SettingsPanel.tsx", 'Установите BORIS', "INSTALL_STEP")
need("app/messenger/components/SettingsPanel.tsx", 'Включите уведомления', "PUSH_STEP")
need("app/messenger/components/SettingsPanel.tsx", 'Разрешите микрофон и камеру', "MEDIA_STEP")
need("app/messenger/components/SettingsPanel.tsx", 'Инструкция для сотрудников', "PERMANENT_GUIDE_ENTRY")
need("app/messenger/guide/page.tsx", 'Новый лид', "GUIDE_LEADS")
need("app/messenger/guide/page.tsx", 'CRM и задачи', "GUIDE_CRM")
for img in ("iphone.jpg","android.jpg","windows.jpg","mac.jpg"):
    p = root / "public/messenger-guide" / img
    ok = p.is_file() and p.stat().st_size > 1000
    checks.append((f"GUIDE_IMAGE_{img}", ok))
    print(f"GUIDE_IMAGE_{img}={'PASS' if ok else 'FAIL'}")
failed = [n for n, ok in checks if not ok]
print("FAILURES="+str(len(failed)))
if failed:
    print("FAILED="+",".join(failed))
    sys.exit(1)
print("MESSENGER_UNIVERSAL_ONBOARDING_SOURCE=PASS")
