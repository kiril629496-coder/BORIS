#!/usr/bin/env python3

import ast
import os
import re
import subprocess
from pathlib import Path

from sqlalchemy import (
    create_engine,
    text,
)

from app.monitoring.quality import classify as quality_classify
from app.services.telegram_sales_identity import trusted_author_identity

###############################################################################
# CONFIG
###############################################################################

MIN_BORIS_FIT = 75
LOOKBACK_DAYS = 14
MAX_ROWS = 500

###############################################################################
# SERVER GATE
###############################################################################

host = subprocess.check_output(
    ["hostname"],
    text=True,
).strip()

if not host.startswith(
    "8509713-oh794426"
):
    raise SystemExit(
        "FAIL CLOSED: wrong server"
    )

if Path.cwd() != Path(
    "/root/BORIS/backend"
):
    raise SystemExit(
        "FAIL CLOSED: wrong cwd"
    )

###############################################################################
# ENV
###############################################################################

pid = subprocess.check_output(
    [
        "systemctl",
        "show",
        "boris-backend",
        "-p",
        "MainPID",
        "--value",
    ],
    text=True,
).strip()

if not pid or pid == "0":
    raise SystemExit(
        "FAIL CLOSED: backend PID missing"
    )

try:
    _runtime_env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
except PermissionError:
    # Production service may run under a different uid; use the same canonical
    # backend EnvironmentFile instead of failing the queue rebuild.
    from dotenv import load_dotenv
    load_dotenv("/root/BORIS/backend/.env")
    _runtime_env = []

for item in _runtime_env:
    if b"=" not in item:
        continue
    k,v = item.split(b"=",1)
    try:
        os.environ.setdefault(k.decode(),v.decode())
    except Exception:
        pass

url = (
    os.getenv("DATABASE_URL")
    or os.getenv("SQLALCHEMY_DATABASE_URL")
    or os.getenv("DB_URL")
    or os.getenv("POSTGRES_URL")
)

if not url:
    raise SystemExit(
        "FAIL CLOSED: DB URL missing"
    )

engine = create_engine(
    url,
    pool_pre_ping=True,
)

# Keep the visible queue aligned with the actual autosender eligibility window.
# Old external requests must not sit forever as NEW after they are no longer safe
# to contact. Existing-contact reactivation has its own lifecycle and is excluded.
AUTO_MAX_AGE_HOURS = int(os.getenv("HOT_LEAD_AUTO_MAX_AGE_HOURS", "72") or 72)
with engine.begin() as _db:
    stale_external = _db.execute(text("""
      UPDATE boris_sales_hot_leads
      SET status='skipped', skipped_at=COALESCE(skipped_at,NOW()),
          approval_note='stale_external_lead', updated_at=NOW()
      WHERE status IN ('new','approved','later')
        AND contacted_at IS NULL
        AND monitor_intent IS DISTINCT FROM 'existing_contact_reactivation'
        AND COALESCE(source_posted_at,discovered_at) < NOW() - make_interval(hours => :hrs)
    """), {"hrs":AUTO_MAX_AGE_HOURS}).rowcount

###############################################################################
# LOAD EXISTING BORIS FIT + REAL CONTACT LOGIC
###############################################################################

src = Path(
    "monitor_lead_digest.py"
).read_text(
    encoding="utf-8"
)

tree = ast.parse(src)

wanted_globals = {
    "BORIS_FIT_POSITIVE",
    "BORIS_FIT_NEGATIVE",
    "BOT_SUFFIX_RE",
    "TG_USERNAME_RE",
    "TG_LINK_USER_RE",
    "CONTACT_HINT_RE",
}

wanted_functions = {
    "calculate_boris_fit",
    "normalize_username",
    "looks_bot_username",
    "usernames_from_text",
    "choose_real_contact",
}

selected = []

for node in tree.body:

    if (
        isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name)
            and t.id in wanted_globals
            for t in node.targets
        )
    ):
        selected.append(node)

    elif (
        isinstance(node, ast.FunctionDef)
        and node.name in wanted_functions
    ):
        selected.append(node)

module = ast.Module(
    body=selected,
    type_ignores=[],
)

ast.fix_missing_locations(
    module
)

ns = {
    "re":re,
}

exec(
    compile(
        module,
        "<boris_sales_helpers>",
        "exec",
    ),
    ns,
)

calculate_boris_fit = ns.get(
    "calculate_boris_fit"
)

choose_real_contact = ns.get(
    "choose_real_contact"
)

if not calculate_boris_fit:
    raise SystemExit(
        "FAIL CLOSED: BORIS FIT unavailable"
    )

if not choose_real_contact:
    raise SystemExit(
        "FAIL CLOSED: contact extractor unavailable"
    )

###############################################################################
# HELPERS
###############################################################################

def compact(value, limit=1800):

    value = re.sub(
        r"\s+",
        " ",
        str(value or "").strip(),
    )

    if len(value) > limit:
        value = value[:limit-1] + "…"

    return value


def _human_first_name(value):
    raw=compact(value,120)
    if not raw or raw.startswith('@') or raw in ('Автор сообщения','Контакт не извлечён'):
        return ''
    first=re.split(r'\s+',raw,1)[0].strip('.,:;!?()[]{}<>«»"\'')
    if not re.fullmatch(r'[А-ЯЁA-Z][а-яёa-z-]{1,19}',first):
        return ''
    # Telegram display names often double as CRM labels/business titles. Use a
    # greeting only when the first token does not look like a role/niche/company.
    if first.lower() in {
        'авито','авитолог','маркетолог','директолог','менеджер','кандидат','клиент',
        'кухни','мебель','строительство','стройка','грузоперевозки','детейлинг',
        'продажи','лиды','лидогенерация','бизнес','компания','агентство','сервис',
        'поддержка','отзывы','магазин','производство','завод','недвижимость'
    }:
        return ''
    return first


BORIS_AVITO_OFFER = """BORIS закрывает весь путь от рекламы до продажи: AI-маркетолог анализирует нишу и конкурентов, объявления, фото, тексты, ставки и CPL; Feed Factory готовит массовые объявления; AI-менеджер отвечает на обращения; CRM не даёт терять лиды; телефония связывает звонки с карточкой клиента; AI-РОП разбирает диалоги и звонки; аналитика показывает источник, CPL и результат; реактивация возвращает старую базу. Смысл — меньше ручной рутины, меньше потерянных лидов и больше контроля. Можем дать бесплатный тест BORIS на 4 дня на вашем сценарии. Если задача актуальна, могу сразу предложить первый шаг по данным из вашего сообщения — повторно нишу и город спрашивать не буду."""


def first_message_draft(name, body, boris_fit):
    text = str(body or "").lower()
    first_name=_human_first_name(name)
    hello = f"{first_name}, добрый день." if first_name else "Добрый день."
    avito_excluded=any(x in text for x in (
        'не через авито','без авито','авито не надо','авито не нужен','авито не рассматриваю',
        'авито не рассматриваем','кроме авито','только не авито','не авито',
    ))
    yandex_excluded=any(x in text for x in (
        'не через яндекс','без яндекс','без яндекса','директ не надо','не через директ',
        'яндекс директ не рассматриваю','яндекс директ не рассматриваем',
    ))

    # One canonical queue, but different commercial messages by detected need.
    if any(x in text for x in ("сквозн", "romi", "окупаемост", "стоимость договора", "источник продаж")):
        return (
            f"{hello} Увидел ваш вопрос по сквозной аналитике. "
            "В BORIS мы связываем источник рекламы, обращение, переписку или звонок, CRM, договор и оплату, "
            "чтобы было видно не только количество лидов, а какие каналы реально приносят деньги. "
            "Если задача актуальна, могу коротко показать, как это можно собрать на ваших источниках."
        )
    if any(x in text for x in ("коллтрекинг", "calltracking", "call tracking", "подменн", "откуда звон")):
        return (
            f"{hello} Увидел ваш вопрос по коллтрекингу. "
            "BORIS может связать звонок с рекламным источником и карточкой клиента, а дальше показать, "
            "что произошло с лидом вплоть до договора и оплаты. Если актуально, могу показать схему без лишней теории."
        )
    if any(x in text for x in ("ip-телефон", "ip телефон", "ип-телефон", "ип телефон", "телефони", "пропущенн", "звонк")):
        return (
            f"{hello} Увидел ваш запрос по телефонии и звонкам. "
            "В BORIS телефония работает вместе с CRM: входящий звонок фиксируется, создаётся клиент, "
            "видны пропущенные, история работы и следующий шаг менеджера. "
            "Если актуально, могу коротко показать, как это выглядит в одной системе."
        )
    if any(x in text for x in ("ищу маркетолога", "нужен маркетолог", "ищу директолога", "нужен директолог", "подрядчик по рекламе")):
        return (
            f"{hello} Увидел, что вы ищете специалиста или подрядчика по рекламе. "
            "BORIS связывает рекламный контур с заявками, CRM и аналитикой, чтобы видеть не только трафик, а стоимость и качество обращений. "
            "Если задача ещё актуальна, напишите нишу и основной рекламный канал — отвечу предметно по вашему случаю."
        )
    if any(x in text for x in ("crm", "срм", "воронк", "карточк клиент", "контроль менеджер", "отдел продаж")):
        return (
            f"{hello} Увидел ваш запрос по CRM и контролю продаж. "
            "BORIS автоматически собирает обращения в CRM, квалифицирует клиентов, показывает зависшие сделки "
            "и подсказывает следующий шаг менеджеру. Если актуально, могу показать на простой схеме вашего процесса."
        )
    if any(x in text for x in ("реклама не окупается", "дорогие лиды", "лиды дорогие", "высокая стоимость лида", "высокий cpl", "высокий срl")):
        return (
            f"{hello} Увидел, что у вас вопрос по стоимости и окупаемости лидов. "
            "В BORIS мы разделяем проблему на стоимость привлечения, качество обращений и конверсию обработки, чтобы не пытаться лечить всё только ставками или бюджетом. "
            "Если актуально, напишите нишу, канал и примерный CPL — скажу, что проверил бы первым."
        )

    # Lead-generation demand is broader than Avito. Check it before the Avito
    # branch because repost/aggregator text can contain incidental Avito words.
    if any(x in text for x in (
        "лидогенерац", "привлекать клиентов", "поиск клиентов", "поток клиентов",
        "нужны лиды", "нужны клиенты", "нужны заявки", "нужны лиды/клиенты", "ищу лиды",
        "лиды/клиенты/заявки", "нажны лиды", "нажны клиенты", "нажны заявки",
    )):
        excluded=[]
        if avito_excluded: excluded.append('Авито')
        if yandex_excluded: excluded.append('Яндекс Директ')
        restriction=(" Учёл ограничение: " + " и ".join(excluded) + " не предлагаем.") if excluded else ""
        avito_note=(" Для Авито отдельно есть AI-маркетолог и Feed Factory, но этот канал не обязателен."
                    if not avito_excluded else "")
        return (
            f"{hello} Увидел, что вам нужен поток лидов/клиентов.{restriction} "
            "BORIS закрывает весь путь: ищет публичный спрос и подходящие точки контакта, AI-менеджер обрабатывает обращения, "
            "CRM не даёт потерять лиды, телефония фиксирует звонки, AI-РОП контролирует качество, аналитика показывает CPL и результат, "
            "а реактивация возвращает старую базу." + avito_note + " "
            "Смысл — убрать ручную рутину и не терять клиента между привлечением и продажей. Можем дать бесплатный тест BORIS на 4 дня."
        )
    if any(x in text for x in ("нет заявок", "нет лидов", "мало заявок", "мало лидов", "не работает авито", "авито не работает")):
        return (
            f"{hello} Увидел, что с Авито не хватает заявок. "
            "В BORIS мы сначала разбираем, где провал: показы, клики, первое фото, заголовки, тексты, ставки, CPL и обработка обращений, "
            "а затем усиливаем именно слабое место. Если актуально, могу быстро сказать, что бы я проверил у вас в первую очередь."
        )
    if (not avito_excluded) and any(x in text for x in ("автозагруз", "массовая загруз", "массовое размещ", "фид", "xml")) and any(x in text for x in ("авито", "avito")):
        return (
            f"{hello} Увидел ваш вопрос по массовому размещению на Авито. "
            "В BORIS есть Feed Factory: собираем структуру объявлений, характеристики, тексты, фото и проверяем фид перед выгрузкой, "
            "чтобы масштабировать размещение без ручной сборки каждого объявления. Если актуально, могу показать схему под вашу категорию."
        )
    if (not avito_excluded) and any(x in text for x in ("как сделать на авито", "как настроить авито", "как продвигать на авито", "как увеличить продажи на авито", "как поднять объявления", "как поднять объявление", "как на авито сделать", "как на авито настроить", "как на авито продвигать", "как улучшить авито")):
        return (
            f"{hello} Увидел ваш вопрос по работе с Авито. "
            "BORIS может провести короткий аудит объявлений и показать, что влияет на заявки: позиционирование, заголовки, фото, тексты, продвижение и обработка лидов. "
            "Если хотите, могу написать, с каких 3 проверок я бы начал в вашей ситуации."
        )
    if (not avito_excluded) and any(x in text for x in ("ищу авитолога", "нужен авитолог", "нужен специалист по авито", "подскажите авитолога")):
        return f"{hello} Увидел, что вы ищете специалиста по Авито.\n\n{BORIS_AVITO_OFFER}"
    if (not avito_excluded) and any(x in text for x in ("авито", "авитолог", "avito", "объявлен")):
        return f"{hello} Увидел ваш запрос по Авито.\n\n{BORIS_AVITO_OFFER}"
    if any(x in text for x in ("заявк", "лиды", "клиент", "продаж", "автоматизац")):
        return (
            f"{hello} Увидел, что вы ищете решение по потоку клиентов и продажам. "
            "BORIS объединяет привлечение, обработку обращений, AI-менеджера, CRM и аналитику результата. "
            "Если задача актуальна, могу коротко показать, какой участок можно автоматизировать первым."
        )
    return (
        f"{hello} Увидел ваш запрос по маркетингу и продажам. "
        "BORIS объединяет привлечение обращений, AI-менеджера, CRM и аналитику в одной системе. "
        "Если актуально, могу коротко показать, где он может быть полезен именно вам."
    )

###############################################################################
# CREATE QUEUE TABLE
###############################################################################

with engine.begin() as db:

    db.execute(
        text("""
            CREATE TABLE IF NOT EXISTS
            boris_sales_hot_leads
            (
                id BIGSERIAL PRIMARY KEY,

                monitor_message_id TEXT
                    NOT NULL UNIQUE,

                platform TEXT
                    NOT NULL DEFAULT 'telegram',

                contact_name TEXT,

                contact_username TEXT,

                contact_peer_id BIGINT,

                contact_access_hash BIGINT,

                source_name TEXT,

                source_username TEXT,

                original_text TEXT
                    NOT NULL,

                monitor_intent TEXT,

                monitor_score DOUBLE PRECISION,

                boris_fit INTEGER
                    NOT NULL,

                boris_bucket TEXT
                    NOT NULL,

                first_message_draft TEXT,

                status TEXT
                    NOT NULL DEFAULT 'new',

                approval_required BOOLEAN
                    NOT NULL DEFAULT TRUE,

                approved_at TIMESTAMPTZ,

                contacted_at TIMESTAMPTZ,

                source_posted_at TIMESTAMPTZ,

                discovered_at TIMESTAMPTZ
                    NOT NULL DEFAULT now(),

                updated_at TIMESTAMPTZ
                    NOT NULL DEFAULT now(),

                CHECK (
                    status IN
                    (
                        'new',
                        'approved',
                        'contacted',
                        'replied',
                        'qualified',
                        'lost',
                        'ignored',
                        'skipped',
                        'later',
                        'declined'
                    )
                )
            )
        """)
    )
    db.execute(text("ALTER TABLE boris_sales_hot_leads ADD COLUMN IF NOT EXISTS contact_access_hash BIGINT"))

###############################################################################
# SOURCE MESSAGES
###############################################################################

with engine.connect() as db:

    rows = db.execute(
        text(
            f"""
            SELECT
                m.id,
                m.external_message_id,
                m.external_author_id,
                m.payload AS message_payload,
                m.author_name,
                m.author_username,
                m.text,
                m.intent,
                m.priority,
                m.score_total,
                m.posted_at,
                m.collected_at,

                s.username AS source_username,
                s.title AS source_title,

                c.username AS contact_username,
                c.display_name AS contact_name

            FROM monitor_messages m

            JOIN monitor_sources s
              ON s.id=m.source_id

            LEFT JOIN contacts c
              ON c.id=m.contact_id

            WHERE
                m.platform='telegram'

                AND COALESCE(
                    m.posted_at,
                    m.collected_at
                ) >= now()
                   - interval '{LOOKBACK_DAYS} days'

                AND m.text IS NOT NULL

                AND length(
                    trim(m.text)
                ) >= 10

            ORDER BY
                COALESCE(
                    m.posted_at,
                    m.collected_at
                ) DESC

            LIMIT {MAX_ROWS}
            """
        )
    ).mappings().all()

###############################################################################
# QUALIFY / UPSERT
###############################################################################

inserted = 0
updated = 0
rejected = 0

top = []

with engine.begin() as db:

    for raw in rows:

        row = dict(raw)

        quality = quality_classify(
            row.get("text"),
            row.get("intent"),
            row.get("score_total"),
        )
        if quality.get("reject") or not quality.get("qualified"):
            db.execute(
                text("""
                    UPDATE boris_sales_hot_leads
                    SET status='skipped', skipped_at=COALESCE(skipped_at,NOW()),
                        approval_note='central_quality_disqualified', updated_at=NOW()
                    WHERE monitor_message_id=:mid AND status IN ('new','later','approved')
                      AND contacted_at IS NULL
                """),
                {"mid":str(row["id"])},
            )
            rejected += 1
            continue

        fit = calculate_boris_fit(
            row.get("text"),
            row.get("intent"),
            row.get("score_total"),
        )

        score = int(
            fit.get("score")
            or 0
        )

        if score < MIN_BORIS_FIT:
            # Re-qualification is authoritative for unsent leads. If a rule/fit
            # change disqualifies an old candidate, remove it from the outbound pool.
            db.execute(
                text("""
                    UPDATE boris_sales_hot_leads
                    SET status='skipped', skipped_at=COALESCE(skipped_at,NOW()),
                        approval_note='auto_disqualified_by_current_quality_gate',
                        updated_at=NOW()
                    WHERE monitor_message_id=:mid AND status IN ('new','later','approved')
                      AND contacted_at IS NULL
                """),
                {"mid":str(row["id"])},
            )
            rejected += 1
            continue

        contact = choose_real_contact(
            body=row.get("text"),
            contact_name=row.get("contact_name"),
            contact_username=row.get("contact_username"),
            author_name=row.get("author_name"),
            author_username=row.get("author_username"),
            source_username=row.get("source_username"),
        )

        person = compact(
            contact.get("person"),
            160,
        )

        username = compact(
            contact.get("username"),
            100,
        )

        body = compact(
            row.get("text"),
            3000,
        )
        payload=row.get('message_payload') or {}
        if isinstance(payload,str):
            try:
                import json as _json; payload=_json.loads(payload)
            except Exception: payload={}
        # external_author_id can be a publishing bot/channel, not the buyer.
        peer_id,access_hash=trusted_author_identity(row.get('external_author_id'),payload)
        # If contact extraction found an explicit username different from the
        # message author, the author is a publisher/intermediary. Never persist
        # that author's peer/hash as the buyer identity.
        explicit_u=str(username or '').strip().lstrip('@').lower()
        author_u=str(row.get('author_username') or '').strip().lstrip('@').lower()
        if explicit_u and author_u and explicit_u != author_u:
            peer_id,access_hash=None,None

        draft = first_message_draft(
            person,
            body,
            score,
        )

        existed = db.execute(
            text("""
                SELECT id
                FROM boris_sales_hot_leads
                WHERE monitor_message_id=:mid
            """),
            {
                "mid":str(row["id"]),
            },
        ).scalar()

        db.execute(
            text("""
                INSERT INTO
                    boris_sales_hot_leads
                (
                    monitor_message_id,
                    contact_name,
                    contact_username,
                    contact_peer_id,
                    contact_access_hash,
                    source_name,
                    source_username,
                    original_text,
                    monitor_intent,
                    monitor_score,
                    boris_fit,
                    boris_bucket,
                    first_message_draft,
                    approval_required,
                    source_posted_at,
                    updated_at
                )
                VALUES
                (
                    :mid,
                    :contact_name,
                    :contact_username,
                    :contact_peer_id,
                    :contact_access_hash,
                    :source_name,
                    :source_username,
                    :original_text,
                    :intent,
                    :monitor_score,
                    :boris_fit,
                    :bucket,
                    :draft,
                    :approval_required,
                    :posted_at,
                    now()
                )

                ON CONFLICT
                    (monitor_message_id)

                DO UPDATE
                SET
                    contact_name=
                        EXCLUDED.contact_name,

                    contact_username=
                        COALESCE(NULLIF(boris_sales_hot_leads.contact_username,''),NULLIF(EXCLUDED.contact_username,''),''),

                    contact_peer_id=COALESCE(boris_sales_hot_leads.contact_peer_id,EXCLUDED.contact_peer_id),

                    contact_access_hash=COALESCE(boris_sales_hot_leads.contact_access_hash,EXCLUDED.contact_access_hash),

                    source_name=
                        EXCLUDED.source_name,

                    source_username=
                        EXCLUDED.source_username,

                    original_text=
                        EXCLUDED.original_text,

                    monitor_intent=
                        EXCLUDED.monitor_intent,

                    monitor_score=
                        EXCLUDED.monitor_score,

                    boris_fit=
                        EXCLUDED.boris_fit,

                    boris_bucket=
                        EXCLUDED.boris_bucket,

                    first_message_draft=
                        CASE
                            WHEN
                                boris_sales_hot_leads.status='new'
                            THEN
                                EXCLUDED.first_message_draft
                            ELSE
                                boris_sales_hot_leads.first_message_draft
                        END,

                    approval_required=
                        CASE
                            WHEN boris_sales_hot_leads.status='new'
                            THEN EXCLUDED.approval_required
                            ELSE boris_sales_hot_leads.approval_required
                        END,

                    source_posted_at=
                        EXCLUDED.source_posted_at,

                    updated_at=now()
            """),
            {
                "mid":
                    str(row["id"]),

                "contact_name":
                    person,

                "contact_username":
                    username,

                "contact_peer_id": peer_id,

                "contact_access_hash": access_hash,

                "source_name":
                    compact(
                        row.get("source_title")
                        or row.get("source_username"),
                        200,
                    ),

                "source_username":
                    compact(
                        row.get("source_username"),
                        100,
                    ),

                "original_text":
                    body,

                "intent":
                    row.get("intent"),

                "monitor_score":
                    row.get("score_total"),

                "boris_fit":
                    score,

                "bucket":
                    fit.get("bucket")
                    or "HOT",

                "draft":
                    draft,

                "approval_required":
                    not bool(username or (peer_id and access_hash)),

                "posted_at":
                    row.get("posted_at")
                    or row.get("collected_at"),
            },
        )

        if existed:
            updated += 1
        else:
            inserted += 1

        top.append(
            {
                "fit":
                    score,

                "name":
                    person,

                "username":
                    username,

                "intent":
                    row.get("intent"),

                "text":
                    body[:400],

                "draft":
                    draft[:500],
            }
        )

###############################################################################
# RESULT
###############################################################################

top.sort(
    key=lambda x:x["fit"],
    reverse=True,
)

print(
    "source_messages=",
    len(rows),
)

print(
    "hot_inserted=",
    inserted,
)

print(
    "hot_updated=",
    updated,
)

print(
    "rejected_fit=",
    rejected,
)
print(
    "stale_external_skipped=",
    stale_external,
)

print()
print(
    "================ HOT SALES QUEUE ================"
)

for i,item in enumerate(
    top[:15],
    1,
):

    print()
    print(
        f"{i}. BORIS_FIT={item['fit']}"
    )

    print(
        "contact=",
        item["name"],
        item["username"],
    )

    print(
        "intent=",
        item["intent"],
    )

    print(
        "request=",
        item["text"],
    )

    print(
        "draft=",
        item["draft"],
    )

###############################################################################
# DB SUMMARY
###############################################################################

with engine.connect() as db:

    rows = db.execute(
        text("""
            SELECT
                status,
                count(*) AS n
            FROM boris_sales_hot_leads
            GROUP BY status
            ORDER BY status
        """)
    ).mappings().all()

    print()
    print(
        "================ QUEUE STATE ================"
    )

    for r in rows:
        print(dict(r))

    total = db.execute(
        text("""
            SELECT count(*)
            FROM boris_sales_hot_leads
        """)
    ).scalar()

    print(
        "queue_total=",
        total,
    )

print()
print(
    "PROSPECT_SEND=0"
)

print(
    "AI_CALLS=0"
)

print(
    "HOT_SALES_QUEUE=PASS"
)
