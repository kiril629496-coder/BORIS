import uuid
from app.db.session import SessionLocal
from app.models.control_plane import ControlWorkspaceThread, ControlWorkspaceEvent
from app.models.account import Account

TASKS = [
    ("Журнал программирования и управление задачами", "Единый восстанавливаемый журнал: каждая задача, постоянный thread_id, история, фактический прогресс, план и управление промтом."),
    ("Центр управления клиентами", "Одно окно на клиента: память, команды, KPI, почасовой план/факт BORIS, диалоги, ответы МОПа и фактические действия."),
    ("Фильтр соискателей из KPI", "Классифицировать обращения соискателей по обычным рекламным объявлениям и не засчитывать их в бизнес-лиды/выполнение плана; вакансии и рекрутинг не фильтровать."),
    ("Единый мозг BORIS / Control Plane", "Единый desired-state и runtime-контроль модулей, правила владельца, incidents, action plan, evidence и self-heal."),
    ("Автопостинг BORIS", "Контроль публикаций, очередей, подтверждений Avito и автоматическое восстановление без ручного оператора."),
    ("Автопилот ставок BORIS", "Автоматическое управление ставками и KPI с безопасными лимитами, почасовым темпом и evidence."),
    ("ИИ-маркетолог: KPI и автономные действия", "План/факт, CPL, диагностика причины, безопасные действия, измерение эффекта, память гипотез и откат."),
    ("Gateway production", "Проверка и доведение Gateway до стабильного production-контура с runtime QA."),
    ("Telegram лидогенерация", "Исходящие, ключевые слова, видимость действий, реактивация и автономный цикл."),
    ("Email outreach", "Автоматизированная email-лидогенерация, дневные лимиты, отправленные письма и отчётность."),
    ("Защита баннеров Avito", "Визуальные проверки, соответствие категории, идемпотентность и защита от повторных платных генераций."),
    ("Feed Factory и публикации", "Фид, валидация карточек, preview/approval/export, соответствие фото названию/категории и безопасная публикация."),
    ("МОП / Сообщения / CRM", "Стабильные ответы МОПа, очередь сообщений, квалификация, CRM-сделки, реактивация и контроль следующего действия."),
    ("РОП и анализ звонков", "Аудит звонков и переписок, оценки, рекомендации, отчёты и контроль качества продаж."),
    ("Телефония и автоответчик", "Входящий сценарий, вопрос+телефон, создание заявки в CRM, коллтрекинг и отчётность."),
    ("Яндекс Директ / ИИ-директолог", "API-доступ, кампании, объявления, ставки, цели, регионы, минус-слова, отчётность и реальный запуск."),
    ("Голосовое управление BORIS", "Голосовые команды, подключение Command Center, русский голос и управление системой."),
    ("Video Factory", "Короткие продающие ролики и обучающие видео по BORIS с качественной озвучкой и визуальной подачей."),
    ("Cross-chat память и восстановление контекста", "Связать рабочие чаты через постоянный реестр, resume-pack и единый контекст, чтобы задача не зависела от истории ChatGPT."),
    ("Production supervisor клиентов", "Автоматический контроль исправности клиентских контуров: публикации, очереди, KPI, self-heal и эскалация только при невозможности автоисправления."),
    ("Единый UI/UX BORIS", "Унифицированный shell, карточки, навигация, адаптивность и одинаковые паттерны управления во всех рабочих разделах."),
    ("Planeta: проверка фото и карточек", "Проверка соответствия фото категории/названию/описанию, карточек кампании и готовности к реальной публикации."),
]

CLIENTS = [
    ("Мария Брокер", "Контроль KPI, объявлений, продвижения, МОП/CRM/реактивации и действий ИИ-маркетолога."),
    ("Бытовки СПБ", "Контроль двух аккаунтов, публикаций/очередей/KPI, МОП/РОП и самовосстановления клиентского контура."),
    ("Андрей — Пенза / Москва", "Два Avito-аккаунта: РОП, МОП, кампании и KPI."),
    ("Планета мебели", "Кампания 144, карточки, валидация фото/категории, Feed Factory и подготовка к публикации."),
    ("UFA Kitchens", "Многоконтурная работа по аккаунтам: МОП, РОП, стабильность и единое управление."),
    ("Сочи — мебель / стекло", "Клиентские направления, объявления, лиды, маркетинг и управление BORIS."),
    ("Штукатурка Сочи", "Объявления, KPI лидов, CPL и действия ИИ-маркетолога."),
    ("Василий — Camry", "Фото, баннеры, конкурентный анализ и объявления без выдуманных данных."),
]

def ensure_state(row):
    state = dict(row.current_state_json or {})
    changed = False
    defaults = {
        "progress_percent": None,
        "progress_status": "needs_runtime_recheck",
        "current_step": "Нужно сверить фактическое состояние production",
        "next_step": "Продолжить с последней доказанной точки и записать evidence",
        "memory_mode": "durable_workspace",
    }
    for key, value in defaults.items():
        if key not in state:
            state[key] = value
            changed = True
    if changed:
        row.current_state_json = state
    return changed

def upsert(db, thread_type, title, summary):
    existing = (
        db.query(ControlWorkspaceThread)
        .filter(
            ControlWorkspaceThread.title == title,
            ControlWorkspaceThread.thread_type == thread_type,
        )
        .first()
    )
    if existing:
        changed = False
        if not existing.summary:
            existing.summary = summary
            changed = True
        if thread_type == "task":
            changed = ensure_state(existing) or changed
        elif thread_type == "client":
            state = dict(existing.current_state_json or {})
            if "memory_mode" not in state:
                state["memory_mode"] = "durable_client_workspace"
                existing.current_state_json = state
                changed = True
        if changed:
            db.add(
                ControlWorkspaceEvent(
                    thread_id=existing.id,
                    event_type="registry_sync",
                    actor="system",
                    text="Рабочая ветка синхронизирована с единым реестром BORIS; прогресс не выдумывался.",
                    payload_json={"source": "recovered_context", "date": "2026-09-04"},
                )
            )
        return False, changed

    thread_id = str(uuid.uuid4())
    current_state = (
        {
            "progress_percent": None,
            "progress_status": "needs_runtime_recheck",
            "current_step": "Нужно сверить фактическое состояние production",
            "next_step": "Продолжить с последней доказанной точки и записать evidence",
            "memory_mode": "durable_workspace",
        }
        if thread_type == "task"
        else {"memory_mode": "durable_client_workspace"}
    )
    row = ControlWorkspaceThread(
        id=thread_id,
        thread_type=thread_type,
        title=title,
        status="active",
        summary=summary,
        current_state_json=current_state,
        plan_json=[],
        source_json={
            "source": "recovered_context",
            "date": "2026-09-04",
            "progress_truth": "runtime_recheck_required",
        },
    )
    db.add(row)
    db.flush()
    db.add(
        ControlWorkspaceEvent(
            thread_id=thread_id,
            event_type="recovered",
            actor="system",
            text="Ветка восстановлена в долговечный журнал из текущего рабочего контекста BORIS. Процент выполнения не назначен без runtime-проверки.",
            payload_json={"source": "recovered_context", "date": "2026-09-04"},
        )
    )
    return True, False

def main():
    db = SessionLocal()
    created = updated = 0
    try:
        for title, summary in TASKS:
            c, u = upsert(db, "task", title, summary)
            created += int(c)
            updated += int(u)
        for title, summary in CLIENTS:
            c, u = upsert(db, "client", title, summary)
            created += int(c)
            updated += int(u)

        # Every real owner account gets an account-scoped durable client memory.
        # The client Command Center resolves by account_id, so this is the
        # canonical bridge from live account data to persistent client context.
        for account in db.query(Account).all():
            aid = str(account.account_id or "")
            name = str(account.name or aid or "Клиент")
            if not aid or aid.lower().startswith("qa") or name.lower().startswith("qa"):
                continue
            existing = (
                db.query(ControlWorkspaceThread)
                .filter(
                    ControlWorkspaceThread.thread_type == "client",
                    ControlWorkspaceThread.account_id == aid,
                )
                .first()
            )
            if existing:
                state = dict(existing.current_state_json or {})
                if state.get("memory_mode") != "durable_client_workspace":
                    state["memory_mode"] = "durable_client_workspace"
                    existing.current_state_json = state
                    updated += 1
                continue
            thread_id = str(uuid.uuid4())
            row = ControlWorkspaceThread(
                id=thread_id,
                thread_type="client",
                title=f"Клиент · {name}"[:500],
                account_id=aid,
                status="active",
                summary=f"Постоянная онлайн-память и центр управления аккаунтом {name}.",
                current_state_json={"memory_mode": "durable_client_workspace"},
                plan_json=[],
                source_json={"source": "active_account_registry", "date": "2026-09-04", "account_id": aid},
            )
            db.add(row); db.flush()
            db.add(ControlWorkspaceEvent(
                thread_id=thread_id,
                event_type="client_memory_created",
                actor="system",
                text="Создана постоянная клиентская память, привязанная к реальному account_id.",
                payload_json={"account_id": aid, "account_name": name},
            ))
            created += 1
        db.commit()
        task_total = db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.thread_type == "task").count()
        client_total = db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.thread_type == "client").count()
        active_total = db.query(ControlWorkspaceThread).filter(ControlWorkspaceThread.status == "active").count()
        print({"created": created, "updated": updated, "tasks": task_total, "clients": client_total, "active_total": active_total})
    finally:
        db.close()

if __name__ == "__main__":
    main()
