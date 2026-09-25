#!/usr/bin/env python3

import argparse
import ast
import html
import json
import os
import re
import subprocess
from pathlib import Path

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)

###############################################################################
# CONFIG
###############################################################################

CHAT_ID = "-1003952038222"
THREAD_ID = 126

LOOKBACK_HOURS = 72
LIMIT = 5
POOL_LIMIT = 500

GOOD_INTENTS = {
    "buy_service",
}

SECONDARY_INTENTS = {
    "ask_advice",
}

BAD_INTENTS = {
    "offer_service",
    "discussion",
    "job_post",
    "other",
}

###############################################################################
# SERVER GATE
###############################################################################

host = subprocess.check_output(
    ["hostname"],
    text=True,
).strip()

if not host.startswith("8509713-oh794426"):
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

for item in Path(
    f"/proc/{pid}/environ"
).read_bytes().split(b"\0"):

    if b"=" not in item:
        continue

    k,v = item.split(b"=",1)

    try:
        os.environ.setdefault(
            k.decode(),
            v.decode(),
        )
    except Exception:
        pass

DB_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("SQLALCHEMY_DATABASE_URL")
    or os.getenv("DB_URL")
    or os.getenv("POSTGRES_URL")
)

if not DB_URL:
    raise SystemExit(
        "FAIL CLOSED: DB URL missing"
    )

engine = create_engine(
    DB_URL,
    pool_pre_ping=True,
)

###############################################################################
# HELPERS
###############################################################################

def qi(v):
    return '"' + str(v).replace('"','""') + '"'


def esc(v):
    return html.escape(
        str(v or ""),
        quote=False,
    )


def short(v, n=700):

    s = str(v or "").strip()

    s = re.sub(
        r"\s+",
        " ",
        s,
    )

    if len(s) > n:
        s = s[:n-1] + "…"

    return s


def numeric(v):

    try:
        if v in (None,""):
            return None
        return float(v)
    except Exception:
        return None


def truthy(v):

    return v in (
        True,
        1,
        "1",
        "true",
        "True",
        "yes",
        "YES",
    )


def parse_obj(v):

    if isinstance(v, dict):
        return v

    if v in (None,""):
        return {}

    s = str(v).strip()

    try:
        x = json.loads(s)
        return x if isinstance(x,dict) else {}
    except Exception:
        pass

    try:
        x = ast.literal_eval(s)
        return x if isinstance(x,dict) else {}
    except Exception:
        return {}


def parse_terms(v):

    if v is None:
        return []

    if isinstance(v,list):
        return [
            str(x)
            for x in v
        ]

    if isinstance(v,dict):
        return [
            str(x)
            for x in v.keys()
        ]

    s = str(v).strip()

    try:
        x = json.loads(s)

        if isinstance(x,list):
            return [
                str(y)
                for y in x
            ]

    except Exception:
        pass

    return [
        x.strip()
        for x in re.split(
            r"[,;|]",
            s,
        )
        if x.strip()
    ]


###############################################################################
# REAL PERSON / CONTACT EXTRACTION
###############################################################################

BOT_SUFFIX_RE = re.compile(
    r"(?:_bot|bot)$",
    re.I,
)

TG_USERNAME_RE = re.compile(
    r"(?<![A-Za-z0-9_])@([A-Za-z][A-Za-z0-9_]{4,31})"
)

TG_LINK_USER_RE = re.compile(
    r"(?:https?://)?t\.me/([A-Za-z][A-Za-z0-9_]{4,31})(?:/|\b)",
    re.I,
)

CONTACT_HINT_RE = re.compile(
    r"""
    (?:
        писать|
        пишите|
        написать|
        связь|
        связаться|
        лс|
        личк[ауе]|
        telegram|
        телеграм|
        tg|
        контакт|
        обращаться|
        отклик|
        резюме
    )
    [^\n@]{0,80}
    @([A-Za-z][A-Za-z0-9_]{4,31})
    """,
    re.I | re.X,
)


def normalize_username(v):

    s = str(
        v or ""
    ).strip()

    s = s.lstrip("@")

    if not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{4,31}",
        s,
    ):
        return ""

    return s


def looks_bot_username(v):

    u = normalize_username(v)

    if not u:
        return False

    return bool(
        BOT_SUFFIX_RE.search(u)
    )


def usernames_from_text(body):

    body = str(
        body or ""
    )

    found = []

    # Highest confidence:
    # username located near an explicit contact phrase.
    for m in CONTACT_HINT_RE.finditer(body):

        u = normalize_username(
            m.group(1)
        )

        if (
            u
            and not looks_bot_username(u)
            and u.lower()
               not in {
                   x.lower()
                   for x in found
               }
        ):
            found.append(u)

    # Normal @username mentions.
    for m in TG_USERNAME_RE.finditer(body):

        u = normalize_username(
            m.group(1)
        )

        if (
            u
            and not looks_bot_username(u)
            and u.lower()
               not in {
                   x.lower()
                   for x in found
               }
        ):
            found.append(u)

    # t.me/username links.
    for m in TG_LINK_USER_RE.finditer(body):

        u = normalize_username(
            m.group(1)
        )

        if (
            u
            and not looks_bot_username(u)
            and u.lower()
               not in {
                   x.lower()
                   for x in found
               }
        ):
            found.append(u)

    return found


def choose_real_contact(
    body,
    contact_name,
    contact_username,
    author_name,
    author_username,
    source_username,
):

    body_users = usernames_from_text(
        body
    )

    cuser = normalize_username(
        contact_username
    )

    auser = normalize_username(
        author_username
    )

    suser = normalize_username(
        source_username
    )

    ###########################################################################
    # 1. If DB contact/author is a normal human account,
    #    preserve it.
    ###########################################################################

    for user, name, origin in (
        (
            cuser,
            contact_name,
            "contacts",
        ),
        (
            auser,
            author_name,
            "message_author",
        ),
    ):

        if (
            user
            and not looks_bot_username(user)
        ):

            return {
                "person":
                    str(
                        name
                        or ("@" + user)
                    ).strip(),

                "username":
                    user,

                "origin":
                    origin,

                "aggregator":
                    False,
            }

    ###########################################################################
    # 2. DB author is bot/aggregator.
    #    Prefer a real @username embedded in message.
    ###########################################################################

    blocked = {
        x.lower()
        for x in (
            cuser,
            auser,
            suser,
        )
        if x
    }

    for user in body_users:

        if user.lower() in blocked:
            continue

        return {
            "person":
                "@" + user,

            "username":
                user,

            "origin":
                "message_text",

            "aggregator":
                bool(
                    looks_bot_username(cuser)
                    or looks_bot_username(auser)
                ),
        }

    ###########################################################################
    # 3. No reliable human contact.
    #
    #    IMPORTANT:
    #    never present a bot as a human lead.
    ###########################################################################

    if (
        looks_bot_username(cuser)
        or looks_bot_username(auser)
    ):

        return {
            "person":
                "Контакт не извлечён",

            "username":
                "",

            "origin":
                "aggregator_without_contact",

            "aggregator":
                True,
        }

    ###########################################################################
    # 4. Human name exists but username absent.
    ###########################################################################

    name = str(
        contact_name
        or author_name
        or ""
    ).strip()

    if name:

        return {
            "person":
                name,

            "username":
                "",

            "origin":
                "name_only",

            "aggregator":
                False,
        }

    ###########################################################################
    # 5. Last chance: embedded username.
    ###########################################################################

    if body_users:

        user = body_users[0]

        return {
            "person":
                "@" + user,

            "username":
                user,

            "origin":
                "message_text",

            "aggregator":
                False,
        }

    return {
        "person":
            "Автор сообщения",

        "username":
            "",

        "origin":
            "unknown",

        "aggregator":
            False,
    }



###############################################################################
# BORIS COMMERCIAL FIT
###############################################################################

BORIS_FIT_POSITIVE = {
    r"\bавито\b": 30,
    r"\bавитолог": 35,
    r"\bavito\b": 30,

    r"лидогенерац": 25,
    r"\bлиды?\b": 15,
    r"\bзаявк": 15,
    r"\bобращени": 12,
    r"\bпродаж": 18,
    r"отдел продаж": 25,
    r"\bменеджер.{0,20}продаж": 18,

    r"\bcrm\b": 20,
    r"\bсрм\b": 20,
    r"нужн[аы]?.{0,12}(?:crm|срм)": 30,
    r"(?:посоветуйте|подскажите|какую).{0,25}(?:crm|срм)": 30,
    r"сквозн.{0,25}аналит": 30,
    r"нужн[аы]?.{0,20}сквозн.{0,25}аналит": 25,
    r"(?:посоветуйте|подскажите).{0,30}сквозн.{0,25}аналит": 25,
    r"\bromi\b": 22,
    r"коллтрекинг": 30,
    r"call.?tracking": 30,
    r"(?:нужен|нужна|посоветуйте|подскажите).{0,25}коллтрекинг": 25,
    r"ip.?телефон": 25,
    r"ип.?телефон": 25,
    r"(?:нужна|посоветуйте|подскажите).{0,25}телефони": 25,
    r"пропущенн.{0,20}звон": 22,
    r"автозагруз.{0,20}авито": 25,
    r"массов.{0,20}(?:загруз|объявлен).{0,20}авито": 25,
    r"как.{0,30}(?:сделать|настроить|продвиг).{0,30}авито": 18,
    r"как.{0,20}(?:на|в).{0,10}авито.{0,30}(?:сделать|настроить|продвиг|поднять|увеличить)": 35,
    r"(?:ищу|нужен|нужна|нужны|посоветуйте|подскажите|порекомендуйте).{0,35}авитолог": 45,
    r"(?:ищу|нужен|нужна|нужны).{0,40}(?:специалист|подрядчик).{0,25}авито": 40,
    r"(?:нужно|нужен|нужна|ищу).{0,35}продвижен.{0,25}(?:авито|avito)": 40,
    r"(?:авито|avito).{0,35}(?:нет|мало).{0,20}(?:заяв|лид|обращен)": 35,
    r"(?:нет|мало).{0,20}(?:заяв|лид|обращен).{0,35}(?:авито|avito)": 35,
    r"(?:посоветуйте|подскажите|порекомендуйте).{0,40}(?:коллтрекинг|call.?tracking|crm|срм|телефони|сквозн.{0,15}аналит)": 35,
    r"(?:ищу|нужен|нужна|нужны).{0,35}(?:коллтрекинг|call.?tracking|crm|срм|ip.?телефон|ип.?телефон|сквозн.{0,15}аналит)": 35,
    r"как.{0,30}(?:отслеживать|понимать|узнать).{0,30}(?:звонк|источник|лид|заявк)": 25,
    r"автоматизац": 22,
    r"\bмоп\b": 30,
    r"\bроп\b": 30,

    r"\bмаркетинг": 10,
    r"\bреклам": 8,
    r"(?:ищу|нужен|нужна|нужны).{0,35}маркетолог": 40,
    r"(?:ищу|нужен|нужна|нужны).{0,35}директолог": 40,
    r"(?:ищу|нужен|нужна|нужны).{0,45}подрядчик.{0,35}(?:реклам|лидогенерац|продвиж)": 40,
    r"(?:реклама.{0,25}не окупа|не окупа.{0,25}реклам|дорогие лиды|лиды дорогие|высокая стоимость лида|высокий cpl|высокий срl)": 35,

    r"нет результата": 25,
    r"результата нет": 25,
    r"толку ноль": 25,
    r"тольку ноль": 25,
    r"слил.{0,30}бюджет": 22,
    r"потратил.{0,40}денег": 20,
    r"платим.{0,40}результат": 18,

    r"подрядчик": 10,
    r"агентств": 8,

    r"наш бизнес": 12,
    r"мо[йя] бизнес": 12,
    r"занимаюсь": 6,
    r"компания": 5,
    r"средний чек": 10,
    r"чист.{0,10}прибыл": 12,
    r"готовы работать": 10,
    r"можете нам помочь": 15,
    r"нужен человек.{0,40}помочь": 12,
}

BORIS_FIT_NEGATIVE = {
    r"\bваканси": -20,
    r"\bрезюме\b": -15,
    r"набор.{0,30}служб": -100,
    r"служб.{0,30}контракт": -100,
    r"по контракту": -80,
    r"военн": -80,
    r"в штат": -25,
    r"полная занятость": -20,
    r"предлагаю.{0,30}услуг": -55,
    r"оказываю.{0,30}услуг": -55,
    r"#визитка": -55,
    r"\bя.{0,15}авитолог": -50,
    r"работаю.{0,20}авитолог": -50,
    r"\bя.{0,15}маркетолог": -55,
    r"\bя.{0,15}директолог": -55,
    r"\bмы.{0,20}(?:маркетолог|директолог|авитолог)": -55,
    r"\bкурс\b": -20,
    r"обучени": -20,
    r"\bпрокси\b": -45,
    r"\bdolphin\b": -35,
    r"\bдолфин\b": -35,

    r"\bмонтаж.{0,20}reels": -20,
    r"\breels\b": -10,
    r"\btiktok\b": -8,
    r"\bshorts\b": -8,
    r"\bдизайн": -6,
    r"создание креатив": -8,

    r"таргетолог.{0,30}instagram": -12,
    r"таргетолог.{0,30}инстаграм": -12,
    r"таргетолог.{0,30}вк": -10,
    r"таргетолог.{0,30}telegram": -10,
    r"таргетолог.{0,30}телеграм": -10,
}


def calculate_boris_fit(
    body,
    intent,
    monitor_score,
):

    body = str(
        body or ""
    ).lower()

    score = 0
    signals = []

    for pattern,value in (
        BORIS_FIT_POSITIVE.items()
    ):

        if re.search(
            pattern,
            body,
            re.I,
        ):

            score += value

            signals.append(
                "+" + str(value)
                + ":"
                + pattern
            )

    for pattern,value in (
        BORIS_FIT_NEGATIVE.items()
    ):

        if re.search(
            pattern,
            body,
            re.I,
        ):

            score += value

            signals.append(
                str(value)
                + ":"
                + pattern
            )

    intent = str(
        intent or ""
    ).lower()

    if intent == "buy_service":

        score += 15

        signals.append(
            "+15:intent"
        )

    elif intent == "ask_advice":

        score += 3

    elif intent in {
        "offer_service",
        "discussion",
        "job_post",
        "other",
    }:

        score -= 50

    try:

        original = float(
            monitor_score or 0
        )

    except Exception:

        original = 0

    score += min(
        15,
        original * 0.15,
    )

    direct = any(
        x in body
        for x in (
            "авито",
            "авитолог",
            "avito",
            "лидогенерац",
            "отдел продаж",
            "моп",
            "роп",
            "crm",
            "автоматизац",
        )
    )

    if direct:

        score += 12

        signals.append(
            "+12:direct"
        )

    score = max(
        0,
        min(
            100,
            round(score),
        )
    )

    if score >= 75:
        bucket = "HOT"

    elif score >= 55:
        bucket = "WARM"

    elif score >= 35:
        bucket = "MAYBE"

    else:
        bucket = "LOW"

    return {
        "score":
            score,

        "bucket":
            bucket,

        "signals":
            signals,
    }



def telegram_link(
    source_username,
    external_message_id,
    direct_url,
):

    direct = str(
        direct_url or ""
    ).strip()

    if direct.startswith("http"):
        return direct

    username = str(
        source_username or ""
    ).strip().lstrip("@")

    mid = str(
        external_message_id or ""
    ).strip()

    if username and mid.isdigit():

        return (
            "https://t.me/"
            + username
            + "/"
            + mid
        )

    return ""

###############################################################################
# SCHEMA
###############################################################################

with engine.connect() as db:

    ins = inspect(db)

    tables = set(
        ins.get_table_names()
    )

    for t in (
        "monitor_messages",
        "monitor_sources",
    ):
        if t not in tables:
            raise SystemExit(
                f"FAIL CLOSED: {t} missing"
            )

    msg_cols = [
        c["name"]
        for c in ins.get_columns(
            "monitor_messages"
        )
    ]

    src_cols = [
        c["name"]
        for c in ins.get_columns(
            "monitor_sources"
        )
    ]

    contact_cols = []

    if "contacts" in tables:

        contact_cols = [
            c["name"]
            for c in ins.get_columns(
                "contacts"
            )
        ]

print(
    "monitor_messages cols =",
    msg_cols,
)

print(
    "monitor_sources cols =",
    src_cols,
)

print(
    "contacts cols =",
    contact_cols,
)

###############################################################################
# HARD EXPECTATIONS FROM REAL BORIS SCHEMA
###############################################################################

required_msg = {
    "id",
    "source_id",
    "text",
    "intent",
    "score_total",
}

missing = (
    required_msg
    - set(msg_cols)
)

if missing:
    raise SystemExit(
        "FAIL CLOSED:"
        " required monitor_messages columns missing: "
        + repr(sorted(missing))
    )

DATE_COL = None

if "posted_at" in msg_cols:
    DATE_COL = "posted_at"
elif "collected_at" in msg_cols:
    DATE_COL = "collected_at"

if not DATE_COL:
    raise SystemExit(
        "FAIL CLOSED:"
        " no posted_at/collected_at"
    )

print()
print("DATE_COL =",DATE_COL)
print("SCORE_COL = score_total")
print("INTENT_COL = intent")

###############################################################################
# COMMITTED DEDUP TABLE
###############################################################################

with engine.begin() as db:

    db.execute(
        text("""
            CREATE TABLE IF NOT EXISTS
            monitor_lead_digest_sent
            (
                id BIGSERIAL PRIMARY KEY,

                monitor_message_id TEXT
                    NOT NULL,

                target_chat_id TEXT
                    NOT NULL,

                target_thread_id INTEGER
                    NOT NULL,

                status TEXT
                    NOT NULL,

                telegram_message_id BIGINT,

                created_at TIMESTAMPTZ
                    NOT NULL DEFAULT now(),

                sent_at TIMESTAMPTZ,

                UNIQUE
                (
                    monitor_message_id,
                    target_chat_id,
                    target_thread_id
                )
            )
        """)
    )

print("DEDUP_SCHEMA_COMMIT=PASS")

###############################################################################
# ARGS
###############################################################################

parser = argparse.ArgumentParser()

parser.add_argument(
    "--send",
    action="store_true",
)

args = parser.parse_args()

###############################################################################
# QUERY
###############################################################################

with engine.connect() as db:

    contact_join = ""

    contact_name_expr = "NULL"
    contact_username_expr = "NULL"
    contact_type_expr = "NULL"

    if (
        "contacts" in tables
        and "contact_id" in msg_cols
        and "id" in contact_cols
    ):

        contact_join = """
            LEFT JOIN contacts c
              ON c.id = m.contact_id
        """

        if "display_name" in contact_cols:
            contact_name_expr = "c.display_name"

        if "username" in contact_cols:
            contact_username_expr = "c.username"

        if "contact_type" in contact_cols:
            contact_type_expr = "c.contact_type"

    rows = db.execute(
        text(
            f"""
            SELECT
                m.id AS message_id,
                m.external_message_id,
                m.external_author_id,
                m.author_name,
                m.author_username,

                m.text,
                m.{qi(DATE_COL)} AS message_time,

                m.url AS direct_url,

                m.intent,
                m.priority,
                m.score_total,
                m.score_breakdown,

                m.summary,
                m.suggested_reply,
                m.ai,

                m.matched_terms,
                m.status,
                m.budget_block,

                s.id AS source_id,
                s.external_id AS source_external_id,
                s.username AS source_username,
                s.title AS source_title,
                s.url AS source_url,
                s.source_kind,
                s.topic_id,
                s.topic_title,

                {contact_name_expr}
                    AS contact_name,

                {contact_username_expr}
                    AS contact_username,

                {contact_type_expr}
                    AS contact_type

            FROM monitor_messages m

            JOIN monitor_sources s
              ON s.id=m.source_id

            {contact_join}

            WHERE
                m.platform='telegram'

                AND m.{qi(DATE_COL)}
                    >= now()
                       - interval '{LOOKBACK_HOURS} hours'

                AND m.text IS NOT NULL

                AND length(
                    trim(m.text)
                ) >= 10

                AND NOT EXISTS
                (
                    SELECT 1

                    FROM monitor_lead_digest_sent d

                    WHERE
                        d.monitor_message_id
                            = CAST(m.id AS TEXT)

                        AND
                        d.target_chat_id=:chat

                        AND
                        d.target_thread_id=:thread

                        AND
                        d.status='sent'
                )

            ORDER BY
                m.score_total DESC NULLS LAST,
                m.{qi(DATE_COL)} DESC

            LIMIT {POOL_LIMIT}
            """
        ),
        {
            "chat":CHAT_ID,
            "thread":THREAD_ID,
        },
    ).mappings().all()

###############################################################################
# QUALIFICATION
###############################################################################

accepted = []

rejected = {
    "bad_intent":0,
    "low_score":0,
    "weak_message":0,
    "blocked":0,
    "not_person_enough":0,
}

for r in rows:

    body = short(
        r["text"],
        1200,
    )

    if len(body) < 10:
        rejected[
            "weak_message"
        ] += 1
        continue

    intent = str(
        r["intent"] or ""
    ).strip().lower()

    score = numeric(
        r["score_total"]
    )

    priority = str(
        r["priority"] or ""
    ).strip().lower()

    if r["budget_block"]:
        rejected["blocked"] += 1
        continue

    if intent in BAD_INTENTS:
        rejected[
            "bad_intent"
        ] += 1
        continue

    ###########################################################################
    # QUALIFY
    ###########################################################################

    positive = False

    why = []

    if intent == "buy_service":

        positive = True

        why.append(
            "человек ищет услугу"
        )

    if (
        intent == "ask_advice"
        and score is not None
        and score >= 70
    ):

        positive = True

        why.append(
            "ищет решение и высокий score"
        )

    if (
        score is not None
        and score >= 75
    ):

        positive = True

        why.append(
            f"score {round(score,1)}"
        )

    if priority in (
        "high",
        "hot",
        "urgent",
    ):

        positive = True

        why.append(
            f"priority={priority}"
        )

    if not positive:

        rejected[
            "low_score"
        ] += 1

        continue

    ###########################################################################
    # AUTHOR
    ###########################################################################

    real_contact = choose_real_contact(
        body=body,
        contact_name=r["contact_name"],
        contact_username=r["contact_username"],
        author_name=r["author_name"],
        author_username=r["author_username"],
        source_username=r["source_username"],
    )

    person = real_contact["person"]
    username = real_contact["username"]
    contact_origin = real_contact["origin"]
    aggregator = real_contact["aggregator"]

    ###########################################################################
    # AI DETAILS
    ###########################################################################

    ai = parse_obj(
        r["ai"]
    )

    ai_reason = ""

    for key in (
        "reason",
        "why",
        "lead_reason",
        "summary",
    ):

        if ai.get(key):

            ai_reason = short(
                ai[key],
                500,
            )

            break

    summary = short(
        r["summary"],
        500,
    )

    reason = (
        ai_reason
        or summary
        or "; ".join(why)
    )

    terms = parse_terms(
        r["matched_terms"]
    )

    source = (
        r["source_title"]
        or r["source_username"]
        or "Telegram"
    )

    link = telegram_link(
        r["source_username"],
        r["external_message_id"],
        r["direct_url"]
        or r["source_url"],
    )

    boris_fit = calculate_boris_fit(
        body=body,
        intent=intent,
        monitor_score=score,
    )

    # Commercial relevance gate:
    # do not deliver generic service-hiring messages
    # merely because monitor_score is high.
    if boris_fit["score"] < 55:
        rejected[
            "low_score"
        ] += 1
        continue

    accepted.append(
        {
            "message_id":
                str(
                    r["message_id"]
                ),

            "person":
                short(
                    person
                    or "Автор сообщения",
                    120,
                ),

            "username":
                short(
                    username,
                    100,
                ),

            "contact_origin":
                contact_origin,

            "aggregator":
                aggregator,

            "text":
                body,

            "source":
                short(
                    source,
                    180,
                ),

            "source_kind":
                short(
                    r["source_kind"],
                    80,
                ),

            "topic":
                short(
                    r["topic_title"],
                    100,
                ),

            "intent":
                intent or "—",

            "priority":
                priority or "—",

            "score":
                score,

            "boris_fit":
                boris_fit["score"],

            "boris_bucket":
                boris_fit["bucket"],

            "boris_signals":
                boris_fit["signals"][:10],

            "reason":
                reason,

            "terms":
                terms[:8],

            "link":
                link,

            "message_time":
                r["message_time"],
        }
    )

###############################################################################
# SORT + LIMIT
###############################################################################

def rank(x):

    total = (
        x.get("boris_fit")
        or 0
    ) * 100

    if x["intent"] == "buy_service":
        total += 1000

    if x["priority"] in (
        "high",
        "hot",
        "urgent",
    ):
        total += 200

    if x["score"] is not None:
        total += x["score"]

    return total


accepted.sort(
    key=rank,
    reverse=True,
)

accepted = accepted[
    :LIMIT
]

###############################################################################
# PREVIEW
###############################################################################

print()
print(
    "raw_recent_messages =",
    len(rows),
)

print(
    "qualified_leads =",
    len(accepted),
)

print(
    "rejected =",
    rejected,
)

print()
print(
    "========== PREVIEW =========="
)

for i,lead in enumerate(
    accepted,
    1,
):

    print()
    print(
        f"LEAD {i}"
    )

    print(
        "person =",
        lead["person"],
    )

    print(
        "username =",
        lead["username"],
    )

    print(
        "contact_origin =",
        lead["contact_origin"],
    )

    print(
        "aggregator =",
        lead["aggregator"],
    )

    print(
        "source =",
        lead["source"],
    )

    print(
        "intent =",
        lead["intent"],
    )

    print(
        "priority =",
        lead["priority"],
    )

    print(
        "score =",
        lead["score"],
    )

    print(
        "boris_fit =",
        lead["boris_fit"],
    )

    print(
        "boris_bucket =",
        lead["boris_bucket"],
    )

    print(
        "boris_signals =",
        lead["boris_signals"],
    )

    print(
        "reason =",
        lead["reason"],
    )

    print(
        "terms =",
        lead["terms"],
    )

    print(
        "text =",
        lead["text"],
    )

    print(
        "link =",
        lead["link"],
    )

###############################################################################
# PREVIEW ONLY
###############################################################################

if not args.send:

    print()
    print("MODE=PREVIEW")
    print("TELEGRAM_SEND=0")

    # Normal return, not SystemExit from inside transaction.
    raise SystemExit(0)

###############################################################################
# NO EMPTY DIGEST
###############################################################################

if not accepted:

    print(
        "NO_QUALIFIED_LEADS"
    )

    raise SystemExit(0)

###############################################################################
# FORMAT
###############################################################################

parts = [
    "🔥 <b>Новые потенциальные клиенты из Telegram</b>",
    "",
]

for i,lead in enumerate(
    accepted,
    1,
):

    parts.append(
        f"<b>{i}. {esc(lead['person'])}</b>"
    )

    if lead["username"]:

        parts.append(
            "👤 @"
            + esc(
                lead[
                    "username"
                ].lstrip("@")
            )
        )

    if lead["aggregator"]:

        parts.append(
            "📨 Контакт извлечён из текста объявления"
        )

    parts.append(
        "💬 <b>Что написал:</b>"
    )

    parts.append(
        esc(
            lead["text"]
        )
    )

    parts.append("")

    parts.append(
        "📍 <b>Где:</b> "
        + esc(
            lead["source"]
        )
    )

    if lead["topic"]:

        parts.append(
            "Тема: "
            + esc(
                lead["topic"]
            )
        )

    parts.append(
        "🎯 Intent: "
        + esc(
            lead["intent"]
        )
    )

    if lead["score"] is not None:

        parts.append(
            "⭐ Monitor score: "
            + str(
                round(
                    lead["score"],
                    1,
                )
            )
        )

    parts.append(
        "🎯 <b>BORIS FIT: "
        + str(
            lead["boris_fit"]
        )
        + "/100 — "
        + esc(
            lead["boris_bucket"]
        )
        + "</b>"
    )

    if lead["priority"] != "—":

        parts.append(
            "🔥 Priority: "
            + esc(
                lead[
                    "priority"
                ]
            )
        )

    if lead["terms"]:

        parts.append(
            "🔎 Сигналы: "
            + esc(
                ", ".join(
                    lead[
                        "terms"
                    ]
                )
            )
        )

    if lead["reason"]:

        parts.append(
            "🧠 <b>Почему это лид:</b> "
            + esc(
                lead["reason"]
            )
        )

    if lead["link"]:

        parts.append(
            "🔗 "
            + esc(
                lead["link"]
            )
        )

    parts.append("")

message = "\n".join(
    parts
).strip()

if len(message) > 3900:

    message = (
        message[:3880]
        + "\n…"
    )

###############################################################################
# SEND INTERNAL ONLY
###############################################################################

from app.telegram_bot import (
    send_telegram_message
)

result = send_telegram_message(
    CHAT_ID,
    message,
    THREAD_ID,
)

print(
    "SEND_RESULT=",
    result,
)

ok = (
    isinstance(
        result,
        dict,
    )
    and result.get("ok")
)

if not ok:
    raise SystemExit(
        "FAIL: Telegram send failed"
    )

tg_mid = (
    result
    .get("result",{})
    .get("message_id")
)

###############################################################################
# COMMIT DEDUP AFTER SUCCESS
###############################################################################

with engine.begin() as db:

    for lead in accepted:

        db.execute(
            text("""
                INSERT INTO
                    monitor_lead_digest_sent
                (
                    monitor_message_id,
                    target_chat_id,
                    target_thread_id,
                    status,
                    telegram_message_id,
                    sent_at
                )
                VALUES
                (
                    :mid,
                    :chat,
                    :thread,
                    'sent',
                    :tg,
                    now()
                )
                ON CONFLICT
                (
                    monitor_message_id,
                    target_chat_id,
                    target_thread_id
                )
                DO UPDATE
                SET
                    status='sent',
                    telegram_message_id=
                        EXCLUDED.telegram_message_id,
                    sent_at=now()
            """),
            {
                "mid":
                    lead[
                        "message_id"
                    ],

                "chat":
                    CHAT_ID,

                "thread":
                    THREAD_ID,

                "tg":
                    tg_mid,
            },
        )

print(
    "LEAD_DIGEST_SEND=PASS"
)
