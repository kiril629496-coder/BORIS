import sys, json
sys.path.insert(0, "/root/BORIS/backend")
import requests
from app.api.auth import create_access_token

B = "https://boris-ai.pro"
ACC = "3411770_94346"
USERS = [(56, "Валерия"), (57, "Юлия"), (42, "Сергей влад.")]

def call(tok, method, path, body=None, params=None):
    h = {"Authorization": "Bearer " + tok}
    try:
        if method == "GET":
            r = requests.get(B + path, headers=h, params=params, timeout=40)
        else:
            r = requests.post(B + path, headers=h, json=body, timeout=40)
    except Exception as e:
        return 0, repr(e)[:80]
    return r.status_code, r.text[:150].replace("\n", " ")

for uid, name in USERS:
    tok = create_access_token(uid)
    print("")
    print("========== %s (%s) ==========" % (name, uid))

    st, body = call(tok, "POST", "/api/crm/tasks",
                    {"account_id": ACC, "chat_id": "qa-emp-check", "due_date": "2026-08-13",
                     "due_time": "10:00", "type": "позвонить",
                     "title": "проверка доступа сотрудника", "comment": "тест, будет удалён"})
    print("создать задачу      %s %s" % (st, body))
    tid = None
    try:
        tid = (json.loads(body) or {}).get("id") or (json.loads(body) or {}).get("task_id")
    except Exception:
        pass

    print("список задач        %s %s" % call(tok, "GET", "/api/crm/tasks",
                                             params={"account_id": ACC, "chat_id": "qa-emp-check"}))
    print("метки диалогов      %s %s" % call(tok, "GET", "/api/crm/badges", params={"account_id": ACC}))

    if tid:
        print("выполнить задачу    %s %s" % call(tok, "POST", "/api/crm/tasks/done",
                                                 {"account_id": ACC, "task_id": tid}))
        print("удалить задачу      %s %s" % call(tok, "POST", "/api/crm/tasks/delete",
                                                 {"account_id": ACC, "task_id": tid}))
    else:
        print("!! id задачи не получен — удалять нечего, проверьте вывод выше")

    st, body = call(tok, "POST", "/api/inbox/templates/save",
                    {"name": "QA доступ сотрудника", "text": "тестовый шаблон, будет удалён",
                     "category": "Свои", "scope": "global"})
    print("сохранить шаблон    %s %s" % (st, body))
    tpl = None
    try:
        tpl = (json.loads(body) or {}).get("id")
    except Exception:
        pass
    print("список шаблонов     %s %s" % call(tok, "GET", "/api/inbox/templates",
                                             params={"account_id": ACC}))
    if tpl:
        print("удалить шаблон      %s %s" % call(tok, "POST", "/api/inbox/templates/delete", {"id": tpl}))
    else:
        print("!! id шаблона не получен")

    print("черновики МОПа      %s %s" % call(tok, "GET", "/api/messenger/pending_drafts",
                                             params={"account_id": ACC}))
    print("спящие кандидаты    %s %s" % call(tok, "GET", "/api/reactivation/candidates"))
