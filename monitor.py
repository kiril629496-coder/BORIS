import json
import os
import subprocess
import requests

TELEGRAM_TOKEN_ENV = "BORIS_TELEGRAM_BOT_TOKEN"
OWNER_CHAT_ID = "292876862"  # см. как получить ниже
STATE_FILE = "/root/BORIS/monitor_state.json"

def get_env(key, env_path="/root/BORIS/backend/.env"):
    with open(env_path) as f:
        for line in f:
            if line.startswith(key + "="):
                return line.strip().split("=", 1)[1]
    return None

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def send_alert(token, message):
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": OWNER_CHAT_ID, "text": message},
            timeout=10,
        )
    except Exception:
        pass  # если сам телеграм недоступен — молча пропускаем, не роняем скрипт

def check_url(name, url, method="GET", json_body=None, timeout=8):
    try:
        if method == "POST":
            r = requests.post(url, json=json_body, timeout=timeout)
        else:
            r = requests.get(url, timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False

def check_service(name):
    result = subprocess.run(
        ["systemctl", "is-active", name], capture_output=True, text=True
    )
    return result.stdout.strip() == "active"

def main():
    token = get_env(TELEGRAM_TOKEN_ENV)
    state = load_state()
    checks = {
        "frontend": lambda: check_url("frontend", "https://boris-ai.pro"),
        # Проверяем живость лёгким запросом без обращения к нейросети:
        # раньше сюда слался настоящий вопрос в чат и каждый запуск стоил ~0.96 ₽
        # (GigaChat отвалился по 402, всё уходило на gpt-5.4) — около 20 000 ₽ в месяц.
        "backend_chat": lambda: check_url(
            "backend_chat",
            "http://localhost:8000/api/chat/notifications?account_id=otdushi&unread_only=true",
        ),
        "service_backend": lambda: check_service("boris-backend"),
        "service_frontend": lambda: check_service("boris-frontend"),
        "service_postgresql": lambda: check_service("postgresql"),
    }

    new_state = {}
    for name, fn in checks.items():
        is_ok = fn()
        new_state[name] = is_ok
        was_ok = state.get(name, True)  # по умолчанию считаем что было ок, чтобы не алертить при первом запуске

        if is_ok and not was_ok:
            send_alert(token, f"✅ Восстановлено: {name}")
        elif not is_ok and was_ok:
            send_alert(token, f"🔴 Проблема: {name} не отвечает")

    save_state(new_state)

if __name__ == "__main__":
    main()
