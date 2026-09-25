#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import datetime, timezone, timedelta

import psycopg2
from dotenv import load_dotenv

load_dotenv('/root/BORIS/backend/.env')

from app.crypto_utils import decrypt_secret
from app.monitoring.telegram import build_client
from app.monitoring.quality import classify as quality_classify


DEMAND=(
    "нужен",
    "нужна",
    "нужны",
    "ищу",
    "ищем",
    "посоветуйте",
    "порекомендуйте",
    "подскажите",
    "кто может",
    "кто занимается",
    "как ",
    "как?",
    "что выбрать",
    "чем пользуетесь",
    "кто пользуется",
    "есть ли",
)

COMMERCIAL=(
    "маркетолог",
    "авито",
    "avito",
    "авитолог",
    "реклама",
    "лиды",
    "заявки",
    "продажи",
    "подрядчик",
    "исполнитель",
    "продвижение",
    "smm",
    "crm",
    "срм",
    "сквозная аналитика",
    "romi",
    "коллтрекинг",
    "calltracking",
    "ip-телефония",
    "ip телефония",
    "ип-телефония",
    "ип телефония",
    "пропущенные звонки",
    "автоматизация отдела продаж",
    "контроль отдела продаж",
    "автозагрузка",
    "массовая загрузка",
    "нужно продвижение на авито",
    "нужно продвижение авито",
    "как на авито",
    "как продвигать на авито",
    "как поднять на авито",
    "как увеличить продажи на авито",
    "нет заявок с авито",
    "мало заявок с авито",
    "нет лидов с авито",
    "ai-менеджер",
    "ии-менеджер",
    "автоответы клиентам",
    "виртуальная атс",
    "атс",
    "отслеживать звонки",
    "источник звонков",
)

AVITO=(
    "авито",
    "avito",
    "авитолог",
    "автозагрузка",
    "массовая загрузка",
    "нужно продвижение на авито",
    "нужно продвижение авито",
    "как на авито",
    "как продвигать на авито",
    "как поднять на авито",
    "как увеличить продажи на авито",
    "нет заявок с авито",
    "мало заявок с авито",
    "нет лидов с авито",
)

NEGATIVE=(
    "вакансия",
    "резюме",
    "ищем сотрудника",
    "ищем в штат",
    "ищем в команду",
    "#помогу",
    "#визитка",
    "предлагаю услуги",
    "оказываю услуги",
    "я авитолог",
    "работаю авитологом",
    "мои услуги",
    "мой курс",
    "набираю на обучение",
    "служба по контракту",
    "службу по контракту",
    "набор на службу",
    "военная служба",
)

# Telegram global public-group discovery. Search results are never auto-joined:
# only public megagroups whose history is already readable are evaluated.
GLOBAL_QUERIES=(
    "ищу авитолога",
    "нужен авитолог",
    "нужно продвижение на авито",
    "продвижение авито",
    "как продвигать на авито",
    "как поднять объявления авито",
    "нет заявок с авито",
    "мало лидов с авито",
    "авито бизнес",
    "авито предприниматели",
    "предприниматели маркетинг",
    "бизнес чат предприниматели",
    "маркетологи чат",
    "лидогенерация",
    "поиск клиентов",
    "crm бизнес",
    "посоветуйте crm",
    "срм для бизнеса",
    "автоматизация бизнеса",
    "автоматизация продаж",
    "ai менеджер продажи",
    "ии менеджер продажи",
    "автоответы клиентам",
    "сквозная аналитика",
    "посоветуйте сквозную аналитику",
    "коллтрекинг",
    "посоветуйте коллтрекинг",
    "отслеживать звонки реклама",
    "отдел продаж",
    "телефония бизнес",
    "ip телефония",
    "виртуальная атс",
    "пропущенные звонки",
)


SOURCE_BUYER_MARKERS=(
    "ищу авитолога","нужен авитолог","нужна авитолог","авитолог нужен","ищем авитолога",
    "посоветуйте авитолога","порекомендуйте авитолога","кто ведет авито","кто ведёт авито","кто занимается авито",
    "нужен специалист по авито","ищу специалиста по авито","ищу подрядчика по авито","нужен подрядчик по авито",
    "нужно продвижение на авито","нужно продвигать на авито","как продвигать на авито","как поднять объявления на авито",
    "нет заявок с авито","мало заявок с авито","нет лидов с авито","мало лидов с авито",
    "объявления на авито есть заявок нет","на авито мало просмотров","на авито мало заявок","не идут заявки с авито",
    "нужно ведение авито","нужна настройка авито","кто настроит авито","кто поможет с авито",
    "посоветуйте crm","подскажите crm","порекомендуйте crm","посоветуйте срм","подскажите срм",
    "посоветуйте коллтрекинг","подскажите коллтрекинг","порекомендуйте коллтрекинг",
    "посоветуйте сквозную аналитику","подскажите сквозную аналитику","нужна сквозная аналитика",
    "посоветуйте телефонию","подскажите телефонию","нужна телефония","нужна ip телефония","нужна ип телефония",
    "посоветуйте виртуальную атс","подскажите виртуальную атс","нужна виртуальная атс",
    "как отслеживать звонки","как понять откуда звонки","как понять откуда лиды",
    "нужна автоматизация продаж","нужна автоматизация отдела продаж","нужен ai менеджер","нужен ии менеджер",
    "ищу подрядчика по лидогенерации","нужен подрядчик по лидогенерации","ищу специалиста по лидогенерации",
    "нужен специалист по лидогенерации","ищу подрядчика по интернет-продвижению","нужен подрядчик по интернет-продвижению",
    "ищу маркетолога","нужен маркетолог","ищу директолога","нужен директолог",
    "ищу подрядчика по рекламе","нужен подрядчик по рекламе","нужна помощь с рекламой",
    "нет заявок с рекламы","мало заявок с рекламы","нет лидов с рекламы","мало лидов с рекламы",
    "реклама не окупается","дорогие лиды","лиды дорогие","высокая стоимость лида","высокий cpl",
)

def buyer_source_message(value: str) -> bool:
    t=' '.join(str(value or '').lower().split())
    if any(x in t for x in SOURCE_BUYER_MARKERS):
        return True
    # Flexible advice pattern, but still requires one of BORIS's actual product domains.
    advice=any(x in t for x in ("подскажите","посоветуйте","порекомендуйте","кто может помочь","кто занимается"))
    domain=any(x in t for x in ("авито","авитолог","crm","срм","коллтрекинг","сквозн","телефони","атс","пропущенн","звонк"))
    return bool(advice and domain)


def hits(text,markers):
    t=text.lower()
    return [x for x in markers if x in t]


def main():

    ap=argparse.ArgumentParser()

    ap.add_argument(
        "--apply",
        action="store_true",
    )

    ap.add_argument(
        "--dialogs",
        type=int,
        default=200,
    )

    ap.add_argument(
        "--messages",
        type=int,
        default=50,
    )

    ap.add_argument(
        "--source",
        default="",
        help="Явно добавить доступную публичную Telegram-группу/канал: @username или https://t.me/username",
    )

    args=ap.parse_args()

    conn=psycopg2.connect(
        os.environ["DATABASE_URL"]
    )
    cur=conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS monitor_runtime_state (key TEXT PRIMARY KEY,value_text TEXT,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
    cur.execute("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_scan_cooldown_until'")
    row=cur.fetchone()
    if row and row[0]:
        try:
            until=datetime.fromisoformat(str(row[0]).replace('Z','+00:00'))
            if until.tzinfo is None: until=until.replace(tzinfo=timezone.utc)
            if until > datetime.now(timezone.utc):
                print(f"SOURCE_DISCOVERY=SKIP telegram_cooldown until={until.isoformat()}")
                conn.commit(); conn.close(); return
        except Exception:
            pass
    cur.execute("SELECT pg_try_advisory_lock(%s)",(884422990,))
    if not bool(cur.fetchone()[0]):
        print("SOURCE_DISCOVERY=SKIP telegram_session_busy")
        conn.close()
        return
    # Session-level advisory lock survives COMMIT; do not keep an idle DB
    # transaction open while Telegram scanning can take minutes.
    conn.commit()

    def _telegram_floodwait(exc):
        name=type(exc).__name__.lower()
        return 'floodwait' in name or 'flood_wait' in name

    def _persist_telegram_floodwait(exc):
        seconds=int(getattr(exc,'seconds',0) or getattr(exc,'value',0) or 0)
        seconds=max(300,seconds)
        target=datetime.now(timezone.utc)+timedelta(seconds=seconds)
        try:
            cur.execute("SELECT value_text FROM monitor_runtime_state WHERE key='telegram_scan_cooldown_until'")
            old=cur.fetchone()
            if old and old[0]:
                try:
                    old_dt=datetime.fromisoformat(str(old[0]).replace('Z','+00:00'))
                    if old_dt.tzinfo is None: old_dt=old_dt.replace(tzinfo=timezone.utc)
                    if old_dt>target: target=old_dt
                except Exception:
                    pass
            cur.execute("""INSERT INTO monitor_runtime_state(key,value_text,updated_at)
                           VALUES('telegram_scan_cooldown_until',%s,NOW())
                           ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()""",
                        (target.isoformat(),))
            conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass
        print(f"SOURCE_DISCOVERY=COOLDOWN floodwait={seconds}s until={target.isoformat()}")

    # Heavy Telegram discovery must respect the same global cooldown as the
    # scheduled scanner. It must not extend a multi-hour FloodWait by probing
    # public groups while the account is throttled.
    try:
        cur.execute("SELECT value_text FROM monitor_runtime_state WHERE key=%s",('telegram_scan_cooldown_until',))
        row=cur.fetchone()
        if row and row[0]:
            until=datetime.fromisoformat(str(row[0]).replace('Z','+00:00'))
            if until.tzinfo is None:
                until=until.replace(tzinfo=timezone.utc)
            now=datetime.now(timezone.utc)
            if until>now:
                print(f"SOURCE_DISCOVERY=SKIP telegram_cooldown remaining={int((until-now).total_seconds())}s")
                conn.close()
                return
    except Exception:
        conn.rollback()

    session=decrypt_secret(
        os.environ["MONITOR_TG_SESSION_ENC"]
    )

    client=build_client(
        session,
        int(os.environ["MONITOR_TG_API_ID"]),
        os.environ["MONITOR_TG_API_HASH"],
    )

    cur.execute("""
    SELECT external_id,lower(COALESCE(username,''))
    FROM monitor_sources
    WHERE platform='telegram'
    """)

    existing={
        (str(a or ""),str(b or ""))
        for a,b in cur.fetchall()
    }

    client.connect()

    if not client.is_user_authorized():
        raise SystemExit(
            "STOP Telegram session unauthorized"
        )

    candidates=[]

    try:
        explicit=str(args.source or '').strip()
        if explicit:
            explicit=explicit.replace('https://t.me/','').replace('http://t.me/','').strip('/').lstrip('@')
            explicit=explicit.split('/',1)[0].split('?',1)[0].strip()
            if not explicit:
                raise SystemExit('STOP invalid --source')
            try:
                from telethon.utils import get_peer_id
                entity=client.get_entity(explicit)
                external_id=str(get_peer_id(entity))
                access_hash=getattr(entity,'access_hash',None)
                title=str(getattr(entity,'title',None) or explicit)
                username=str(getattr(entity,'username',None) or explicit)
                is_group=bool(getattr(entity,'megagroup',False))
                is_channel=bool(getattr(entity,'broadcast',False))
                if not external_id.startswith('-100') or not (is_group or is_channel):
                    raise RuntimeError('source is not a Telegram group/channel')
                if not access_hash:
                    raise RuntimeError('telegram access_hash unavailable')
                latest=next(iter(client.iter_messages(entity,limit=1)),None)
                if latest is None:
                    raise RuntimeError('source is not readable')
                source_kind='group' if is_group else 'channel'
                if args.apply:
                    cur.execute("""
                      SELECT id FROM monitor_sources
                      WHERE platform='telegram' AND (external_id=%s OR lower(COALESCE(username,''))=lower(%s))
                      ORDER BY updated_at DESC LIMIT 1
                    """,(external_id,username))
                    found=cur.fetchone()
                    payload=json.dumps({'telegram_access_hash':str(access_hash)},ensure_ascii=False)
                    note='owner-added lead source: https://t.me/'+username
                    if found:
                        source_id=str(found[0])
                        cur.execute("""
                          UPDATE monitor_sources
                          SET source_kind=%s,external_id=%s,username=%s,title=%s,url=%s,is_active=TRUE,
                              weight=GREATEST(COALESCE(weight,10),20),
                              payload=COALESCE(payload,'{}'::jsonb) || %s::jsonb,
                              notes=%s,updated_at=NOW()
                          WHERE id=%s
                        """,(source_kind,external_id,username,title,'https://t.me/'+username,payload,note,source_id))
                        action='updated'
                    else:
                        source_id=str(uuid.uuid4())
                        cur.execute("""
                          INSERT INTO monitor_sources(
                            id,platform,source_kind,external_id,username,title,url,is_active,weight,payload,notes,created_at,updated_at
                          ) VALUES (%s,'telegram',%s,%s,%s,%s,%s,TRUE,20,%s::jsonb,%s,NOW(),NOW())
                        """,(source_id,source_kind,external_id,username,title,'https://t.me/'+username,payload,note))
                        action='inserted'
                    conn.commit()
                    print(f'EXPLICIT_SOURCE=PASS action={action} id={source_id} username=@{username} external_id={external_id} kind={source_kind} readable=1')
                else:
                    conn.rollback()
                    print(f'EXPLICIT_SOURCE=DRY username=@{username} external_id={external_id} kind={source_kind} readable=1 title={title}')
                print('SOURCE_DISCOVERY=PASS')
                return
            except Exception as exc:
                if _telegram_floodwait(exc):
                    _persist_telegram_floodwait(exc)
                    return
                raise

        for dialog in client.iter_dialogs(
            limit=args.dialogs
        ):

            entity=dialog.entity

            is_group=bool(
                getattr(dialog,"is_group",False)
                or getattr(entity,"megagroup",False)
            )

            if not is_group:
                continue

            external_id=str(dialog.id)

            title=(
                getattr(dialog,"name",None)
                or getattr(entity,"title",None)
                or "Telegram group"
            )

            username=(
                getattr(entity,"username",None)
                or ""
            )

            if (
                (external_id,username.lower())
                in existing
                or any(
                    external_id == x[0]
                    or (
                        username
                        and username.lower()==x[1]
                    )
                    for x in existing
                )
            ):
                access_hash=getattr(entity,"access_hash",None)
                if access_hash:
                    cur.execute("""UPDATE monitor_sources
                                   SET payload=COALESCE(payload,'{}'::jsonb) || jsonb_build_object('telegram_access_hash',%s),
                                       is_active=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN TRUE ELSE is_active END,
                                       notes=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN 'auto-recovered after telegram access_hash refresh' ELSE notes END,
                                       updated_at=NOW()
                                   WHERE platform='telegram' AND (external_id=%s OR (%s<>'' AND lower(COALESCE(username,''))=lower(%s)))""",
                                (str(access_hash),external_id,username,username))
                    conn.commit()
                continue

            demand=0
            commercial=0
            avito=0
            negative=0
            qualified_demand=0
            evidence=[]

            try:
                for msg in client.iter_messages(
                    entity,
                    limit=args.messages,
                ):

                    text=(
                        getattr(msg,"message",None)
                        or ""
                    ).strip()

                    if not text:
                        continue

                    d=hits(text,DEMAND)
                    c=hits(text,COMMERCIAL)
                    a=hits(text,AVITO)
                    n=hits(text,NEGATIVE)

                    if d:
                        demand += 1

                    if d and c:
                        commercial += 1

                    if d and a:
                        avito += 1

                    if n:
                        negative += 1

                    quality=quality_classify(text,None,75) if (d and c and not n) else None
                    if quality and quality.get("qualified") and not quality.get("reject") and buyer_source_message(text):
                        qualified_demand += 1
                        evidence.append({
                            "message_id":getattr(msg,"id",None),
                            "text":text[:500],
                            "quality_qualified":True,
                        })

            except Exception as e:
                if _telegram_floodwait(e):
                    _persist_telegram_floodwait(e)
                    return
                continue

            score=0

            score += min(60,demand*5)
            score += min(80,commercial*15)
            score += min(80,avito*20)

            score -= min(80,negative*15)

            score=max(0,score)

            if score < 20:
                continue

            row={
                "external_id":external_id,
                "username":username or None,
                "title":title,
                "telegram_access_hash":getattr(entity,"access_hash",None),
                "candidate_score":score,
                "demand_messages":demand,
                "commercial_messages":commercial,
                "avito_messages":avito,
                "negative_messages":negative,
                "qualified_demand_messages":qualified_demand,
                "evidence":evidence[:5],
            }

            candidates.append(row)

        # Source-name discovery is much heavier than exact buyer-message search.
        # Keep it on a multi-hour cadence while the 15-minute sales cycle focuses
        # on fresh buyer posts. This reduces Telegram pressure without losing
        # freshness of actual demand.
        heavy_source_due=True
        heavy_source_hours=max(1,int(os.getenv('TG_DISCOVERY_HEAVY_SOURCE_HOURS','3') or 3))
        try:
            cur.execute("SELECT updated_at FROM monitor_runtime_state WHERE key='tg_heavy_source_discovery_last_at'")
            last_heavy=cur.fetchone()
            if last_heavy and last_heavy[0] and last_heavy[0] > datetime.now(timezone.utc)-timedelta(hours=heavy_source_hours):
                heavy_source_due=False
            if heavy_source_due:
                cur.execute("""INSERT INTO monitor_runtime_state(key,value_text,updated_at) VALUES('tg_heavy_source_discovery_last_at','started',NOW())
                  ON CONFLICT(key) DO UPDATE SET value_text='started',updated_at=NOW()""")
                conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass
        global_queries=GLOBAL_QUERIES if heavy_source_due else ()
        source_probe_messages=max(10,min(50,int(os.getenv('TG_DISCOVERY_SOURCE_PROBE_MESSAGES','20') or 20)))
        if not heavy_source_due:
            print(f"GLOBAL_SOURCE_DISCOVERY=SKIP cadence={heavy_source_hours}h")

        # Expand beyond dialogs already present in the account by using Telegram's
        # own public search. We never join: only readable public megagroups qualify.
        try:
            from telethon.tl.functions.contacts import SearchRequest
            from telethon.utils import get_peer_id
            seen_global={x["external_id"] for x in candidates}
            seen_global.update(x[0] for x in existing if x[0])
            heavy_probe_budget=max(10,min(120,int(os.getenv('TG_DISCOVERY_HEAVY_PROBE_BUDGET','60') or 60)))
            heavy_group_probed=0
            for query in global_queries:
                try:
                    search=client(SearchRequest(q=query,limit=30))
                except Exception as e:
                    if _telegram_floodwait(e):
                        _persist_telegram_floodwait(e)
                        return
                    continue
                for entity in getattr(search,"chats",[]):
                    if not getattr(entity,"megagroup",False):
                        continue
                    username=(getattr(entity,"username",None) or "").strip()
                    if not username:
                        continue
                    external_id=str(get_peer_id(entity))
                    if external_id in seen_global:
                        access_hash=getattr(entity,'access_hash',None)
                        if access_hash:
                            cur.execute("""UPDATE monitor_sources
                                           SET payload=COALESCE(payload,'{}'::jsonb) || jsonb_build_object('telegram_access_hash',%s),
                                       is_active=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN TRUE ELSE is_active END,
                                       notes=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN 'auto-recovered after telegram access_hash refresh' ELSE notes END,
                                       updated_at=NOW()
                                           WHERE platform='telegram' AND external_id=%s""",
                                        (str(access_hash),external_id))
                            conn.commit()
                        continue
                    if heavy_group_probed>=heavy_probe_budget:
                        continue
                    heavy_group_probed += 1
                    demand=commercial=avito=negative=0
                    qualified_demand=0
                    evidence=[]
                    try:
                        for msg in client.iter_messages(entity,limit=source_probe_messages):
                            text=(getattr(msg,"message",None) or "").strip()
                            if not text:
                                continue
                            d=hits(text,DEMAND); c=hits(text,COMMERCIAL); a=hits(text,AVITO); n=hits(text,NEGATIVE)
                            if d: demand += 1
                            if d and c: commercial += 1
                            if d and a: avito += 1
                            if n: negative += 1
                            quality=quality_classify(text,None,75) if (d and c and not n) else None
                            if quality and quality.get("qualified") and not quality.get("reject") and buyer_source_message(text):
                                qualified_demand += 1
                                evidence.append({"message_id":getattr(msg,"id",None),"text":text[:500],"quality_qualified":True})
                    except Exception as e:
                        if _telegram_floodwait(e):
                            _persist_telegram_floodwait(e)
                            return
                        continue
                    score=min(60,demand*5)+min(80,commercial*15)+min(80,avito*20)-min(80,negative*15)
                    score=max(0,score)
                    if score < 20:
                        continue
                    seen_global.add(external_id)
                    candidates.append({
                        "external_id":external_id,
                        "username":username,
                        "title":getattr(entity,"title",None) or username,
                        "telegram_access_hash":getattr(entity,"access_hash",None),
                        "candidate_score":score,
                        "demand_messages":demand,
                        "commercial_messages":commercial,
                        "avito_messages":avito,
                        "negative_messages":negative,
                        "qualified_demand_messages":qualified_demand,
                        "evidence":evidence[:5],
                    })
        except Exception as e:
            if _telegram_floodwait(e):
                _persist_telegram_floodwait(e)
                return
            print("GLOBAL_DISCOVERY=DEGRADED",type(e).__name__)

        if heavy_source_due:
            print(f"GLOBAL_SOURCE_PROBE groups={locals().get('heavy_group_probed',0)} budget={locals().get('heavy_probe_budget',0)}")

        # Direct public-message demand discovery. Contact search finds groups by
        # name; this second pass finds groups because a real recent message contains
        # buyer intent. It is read-only and never joins a chat.
        try:
            from telethon.utils import get_peer_id
            direct_queries=(
                "#ищу #авитолога", "ищу авитолога", "нужен авитолог", "нужна авитолог", "авитолог нужен",
                "ищем авитолога", "посоветуйте авитолога", "порекомендуйте авитолога",
                "нужен специалист по авито", "ищу специалиста по авито", "нужен подрядчик по авито",
                "ищу подрядчика по авито", "нужна помощь с авито", "нужно продвижение на авито",
                "нет заявок с авито", "мало заявок с авито", "мало лидов с авито", "нет лидов с авито",
                "объявления на авито есть заявок нет", "на авито мало просмотров", "на авито мало заявок", "не идут заявки с авито",
                "нужно ведение авито", "нужна настройка авито", "кто настроит авито", "кто поможет с авито",
                "авито не работает", "кто ведет авито", "кто ведёт авито", "кто занимается авито",
                "посоветуйте crm", "подскажите crm", "нужна crm", "нужна срм",
                "посоветуйте коллтрекинг", "подскажите коллтрекинг",
                "сквозная аналитика", "нужна сквозная аналитика",
                "ip телефония", "виртуальная атс", "нужна телефония", "нужна виртуальная атс",
                "как отслеживать звонки", "как понять откуда лиды",
                "нужна автоматизация продаж", "нужна автоматизация отдела продаж",
                "нужен ai менеджер", "нужен ии менеджер",
                "ищу подрядчика по лидогенерации", "нужен специалист по лидогенерации",
                "ищу подрядчика по интернет-продвижению", "ищу маркетолога", "нужен маркетолог",
                "ищу директолога", "нужен директолог", "ищу подрядчика по рекламе", "нужен подрядчик по рекламе",
                "нет заявок с рекламы", "мало заявок с рекламы", "нет лидов с рекламы", "мало лидов с рекламы",
                "реклама не окупается", "дорогие лиды", "высокая стоимость лида", "высокий cpl",
            )
            configured_lookback=int(os.getenv('TG_DISCOVERY_LOOKBACK_HOURS','120') or 120)
            outbound_max_age=int(os.getenv('HOT_LEAD_AUTO_MAX_AGE_HOURS','72') or 72)
            direct_lookback=max(12,min(configured_lookback,outbound_max_age))
            cutoff=datetime.now(timezone.utc)-timedelta(hours=direct_lookback)
            print(f"GLOBAL_MESSAGE_LOOKBACK hours={direct_lookback}")
            seen_direct={x["external_id"] for x in candidates}
            seen_direct.update(x[0] for x in existing if x[0])
            direct_candidate_map={x["external_id"]:x for x in candidates}
            global_direct_added=0
            global_direct_message_saved=0
            direct_query_limit=max(10,min(60,int(os.getenv('TG_DISCOVERY_GLOBAL_QUERY_LIMIT','30'))))
            # Rank by actual novelty, not raw repeated hits. Telegram global search
            # often returns the same top messages for many cycles; a query with dozens
            # of qualified-looking hits but 10+ attempts and zero newly saved messages
            # or sources is saturated and must not consume a pinned slot every 15 min.
            try:
                cur.execute("SELECT query,qualified_hits,attempts,unique_sources,messages_saved,last_hit_at FROM telegram_discovery_query_stats")
                qstats={str(q):(int(h or 0),int(a or 0),int(u or 0),int(ms or 0),lh) for q,h,a,u,ms,lh in cur.fetchall()}
                direct_queries=tuple(sorted(direct_queries,key=lambda q:(
                    -(qstats.get(q,(0,0,0,0,None))[3]),
                    -(qstats.get(q,(0,0,0,0,None))[2]),
                    -((qstats.get(q,(0,0,0,0,None))[0]*1000)//max(1,qstats.get(q,(0,0,0,0,None))[1])),
                    direct_queries.index(q),
                )))
            except Exception:
                conn.rollback()
                qstats={}

            # Run only a bounded rotating slice every 15 minutes. A query may be
            # pinned only while it still has novelty evidence or is newly learning.
            # Saturated queries remain in rotation (roughly hourly) so fresh posts are
            # still caught without repeatedly burning the same search budget.
            ordered=list(direct_queries)
            batch_size=max(8,min(30,int(os.getenv('TG_DISCOVERY_QUERY_BATCH_SIZE','18') or 18)))
            def _core_query(q):
                q=q.lower()
                return any(x in q for x in ('авито','avito','crm','срм','коллтрекинг','телефони','аналитик','лидогенерац','автоматизац'))
            def _saturated(q):
                h,a,u,ms,_=qstats.get(q,(0,0,0,0,None))
                return a>=10 and u==0 and ms==0
            hot=[q for q in ordered if _core_query(q) and qstats.get(q,(0,0,0,0,None))[0]>0 and not _saturated(q)][:4]
            saturated_count=sum(1 for q in ordered if _saturated(q))
            rest=[q for q in ordered if q not in hot]
            cursor=0
            try:
                cur.execute("SELECT value_text FROM monitor_runtime_state WHERE key='tg_discovery_query_cursor'")
                cr=cur.fetchone()
                cursor=int((cr[0] if cr else 0) or 0)
            except Exception:
                try: conn.rollback()
                except Exception: pass
            take=max(0,batch_size-len(hot))
            rotated=[]
            if rest and take:
                cursor%=len(rest)
                for i in range(min(take,len(rest))):
                    rotated.append(rest[(cursor+i)%len(rest)])
                cursor=(cursor+len(rotated))%len(rest)
            direct_queries=tuple(dict.fromkeys(hot+rotated))
            try:
                cur.execute("""INSERT INTO monitor_runtime_state(key,value_text,updated_at) VALUES('tg_discovery_query_cursor',%s,NOW())
                  ON CONFLICT(key) DO UPDATE SET value_text=EXCLUDED.value_text,updated_at=NOW()""",(str(cursor),))
                conn.commit()
            except Exception:
                try: conn.rollback()
                except Exception: pass
            print(f"GLOBAL_MESSAGE_QUERY_BATCH size={len(direct_queries)} hot={len(hot)} saturated={saturated_count} cursor={cursor}")
            for query in direct_queries:
                raw_count=0
                qualified_count=0
                source_count_before=global_direct_added
                saved_before=global_direct_message_saved
                try:
                    found=client.iter_messages(None,search=query,limit=direct_query_limit)
                    for msg in found:
                        raw_count += 1
                        mdt=getattr(msg,"date",None)
                        if not mdt:
                            continue
                        if mdt.tzinfo is None:
                            mdt=mdt.replace(tzinfo=timezone.utc)
                        if mdt < cutoff:
                            continue
                        body=(getattr(msg,"message",None) or "").strip()
                        if not body:
                            continue
                        low_body=' '.join(body.lower().split())
                        # Global Telegram search is dominated by vacancies and
                        # self-promotion from Avito specialists. Reject those before
                        # scoring so a 30-result window is spent on buyer-like posts.
                        if any(x in low_body for x in (
                            '#помогу','вакансия','ищем сотрудника','помощник авитолога','менеджер авитолог',
                            'я авитолог','мы авитологи','услуги авитолога','предлагаю услуги',
                            'занимаюсь продвижением','помогаю бизнесам','агентство полного цикла',
                            'boris — виртуальная команда','boris — виртуальная команда маркетинга',
                        )):
                            continue
                        q=quality_classify(body,None,75)
                        if q.get("reject") or not q.get("qualified") or not buyer_source_message(body):
                            continue
                        chat=getattr(msg,"chat",None)
                        if chat is None:
                            continue
                        # Lead sources must be public discussion groups. Personal
                        # dialogs/bots and broadcast channels are not promoted by
                        # global search, even when the query text happens to match.
                        if not bool(getattr(chat,"megagroup",False)):
                            continue
                        username=(getattr(chat,"username",None) or "").strip()
                        if not username:
                            continue
                        # Count a query hit only after the result is a usable public
                        # discussion group. Personal dialogs/channels must not train
                        # the query ranker even if their text looks buyer-like.
                        qualified_count += 1
                        external_id=str(get_peer_id(chat))
                        if external_id in seen_direct:
                            access_hash=getattr(chat,'access_hash',None)
                            if access_hash:
                                cur.execute("""UPDATE monitor_sources
                                               SET payload=COALESCE(payload,'{}'::jsonb) || jsonb_build_object('telegram_access_hash',%s),
                                       is_active=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN TRUE ELSE is_active END,
                                       notes=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN 'auto-recovered after telegram access_hash refresh' ELSE notes END,
                                       updated_at=NOW()
                                               WHERE platform='telegram' AND external_id=%s""",
                                            (str(access_hash),external_id))
                            # Critical fast path: when global Telegram search finds a
                            # qualified buyer post in an already-known source, persist
                            # that exact message immediately instead of waiting for the
                            # normal source cursor to encounter it later.
                            cur.execute("SELECT id FROM monitor_sources WHERE platform='telegram' AND external_id=%s LIMIT 1",(external_id,))
                            source_row=cur.fetchone()
                            sender=getattr(msg,'sender',None)
                            author_username=(getattr(sender,'username',None) or '').strip() if sender else ''
                            first=(getattr(sender,'first_name',None) or '').strip() if sender else ''
                            last=(getattr(sender,'last_name',None) or '').strip() if sender else ''
                            author_name=' '.join(x for x in (first,last) if x).strip()
                            author_id=getattr(sender,'id',None) if sender else getattr(msg,'sender_id',None)
                            author_access_hash=getattr(sender,'access_hash',None) if sender else None
                            author_is_min=bool(getattr(sender,'min',False)) if sender else False
                            author_is_bot=bool(getattr(sender,'bot',False)) if sender else False
                            if source_row and getattr(msg,'id',None):
                                msg_url=f"https://t.me/{username}/{getattr(msg,'id')}"
                                cur.execute("""
                                    INSERT INTO monitor_messages (
                                        id,platform,source_id,external_message_id,external_author_id,
                                        author_name,author_username,text,posted_at,url,
                                        matched_terms,matched_rules,score_total,score_breakdown,
                                        language,intent,priority,ai,payload,status,collected_at
                                    ) VALUES (
                                        %s,'telegram',%s,%s,%s,%s,%s,%s,%s,%s,
                                        %s::jsonb,%s::jsonb,95,%s::jsonb,
                                        'ru','buy_service','hot','{}'::jsonb,%s::jsonb,'new',NOW()
                                    )
                                    ON CONFLICT (platform,source_id,external_message_id) DO NOTHING
                                """,(
                                    str(uuid.uuid4()),source_row[0],str(getattr(msg,'id')),
                                    str(author_id) if author_id is not None else None,
                                    author_name or None,author_username or None,body,mdt,msg_url,
                                    json.dumps([query],ensure_ascii=False),
                                    json.dumps(['global_buyer_hunt'],ensure_ascii=False),
                                    json.dumps({'global_buyer_hunt':True,'query':query},ensure_ascii=False),
                                    json.dumps({'global_direct':True,'query':query,'author_access_hash':str(author_access_hash) if author_access_hash else None,'author_is_min':author_is_min,'author_is_bot':author_is_bot},ensure_ascii=False),
                                ))
                                global_direct_message_saved += int(cur.rowcount or 0)
                            elif getattr(msg,'id',None):
                                # Source is known inside this discovery run but has not
                                # been promoted to monitor_sources yet. Keep every unique
                                # exact buyer post in the candidate evidence so promotion
                                # can persist them all in the same run.
                                c=direct_candidate_map.get(external_id)
                                if c is not None:
                                    evid=c.setdefault('evidence',[])
                                    mid=str(getattr(msg,'id'))
                                    if not any(str(e.get('message_id'))==mid for e in evid if isinstance(e,dict)):
                                        evid.append({
                                            'message_id':getattr(msg,'id'), 'text':body[:4000], 'query':query,
                                            'posted_at':mdt.isoformat(),
                                            'author_id':str(author_id) if author_id is not None else None,
                                            'author_username':author_username or None, 'author_name':author_name or None,
                                            'author_access_hash':str(author_access_hash) if author_access_hash else None, 'author_is_min':author_is_min, 'author_is_bot':author_is_bot,
                                        })
                                        c['global_direct_demand']=True
                                        c['qualified_demand_messages']=int(c.get('qualified_demand_messages',0) or 0)+1
                                        c['candidate_score']=max(100,int(c.get('candidate_score',0) or 0))
                            conn.commit()
                            continue
                        title=(getattr(chat,"title",None) or username)
                        avito_hit=1 if any(x in body.lower() for x in ("авито","avito","авитолог")) else 0
                        seen_direct.add(external_id)
                        global_direct_added += 1
                        sender=getattr(msg,'sender',None)
                        author_username=(getattr(sender,'username',None) or '').strip() if sender else ''
                        first=(getattr(sender,'first_name',None) or '').strip() if sender else ''
                        last=(getattr(sender,'last_name',None) or '').strip() if sender else ''
                        author_name=' '.join(x for x in (first,last) if x).strip()
                        author_id=getattr(sender,'id',None) if sender else getattr(msg,'sender_id',None)
                        author_access_hash=getattr(sender,'access_hash',None) if sender else None
                        author_is_min=bool(getattr(sender,'min',False)) if sender else False
                        author_is_bot=bool(getattr(sender,'bot',False)) if sender else False
                        new_direct_candidate={
                            "external_id":external_id,
                            "username":username,
                            "title":title,
                            "telegram_access_hash":getattr(chat,"access_hash",None),
                            "candidate_score":100,
                            "demand_messages":1,
                            "commercial_messages":1,
                            "avito_messages":avito_hit,
                            "negative_messages":0,
                            "qualified_demand_messages":1,
                            "global_direct_demand":True,
                            "evidence":[{
                                "message_id":getattr(msg,"id",None),
                                "text":body[:4000],
                                "query":query,
                                "posted_at":mdt.isoformat(),
                                "author_id":str(author_id) if author_id is not None else None,
                                "author_username":author_username or None,
                                "author_name":author_name or None,
                            }],
                        }
                        candidates.append(new_direct_candidate)
                        direct_candidate_map[external_id]=new_direct_candidate
                except Exception as e:
                    if _telegram_floodwait(e):
                        _persist_telegram_floodwait(e)
                        return
                    print("GLOBAL_MESSAGE_QUERY=DEGRADED",query,type(e).__name__)
                finally:
                    try:
                        delta_sources=max(0,global_direct_added-source_count_before)
                        delta_saved=max(0,global_direct_message_saved-saved_before)
                        cur.execute("""
                          INSERT INTO telegram_discovery_query_stats(query,attempts,raw_results,qualified_hits,unique_sources,messages_saved,last_hit_at,updated_at)
                          VALUES(%s,1,%s,%s,%s,%s,CASE WHEN %s>0 THEN NOW() ELSE NULL END,NOW())
                          ON CONFLICT(query) DO UPDATE SET
                            attempts=telegram_discovery_query_stats.attempts+1,
                            raw_results=telegram_discovery_query_stats.raw_results+EXCLUDED.raw_results,
                            qualified_hits=telegram_discovery_query_stats.qualified_hits+EXCLUDED.qualified_hits,
                            unique_sources=telegram_discovery_query_stats.unique_sources+EXCLUDED.unique_sources,
                            messages_saved=telegram_discovery_query_stats.messages_saved+EXCLUDED.messages_saved,
                            last_hit_at=CASE WHEN EXCLUDED.qualified_hits>0 THEN NOW() ELSE telegram_discovery_query_stats.last_hit_at END,
                            updated_at=NOW()
                        """,(query,raw_count,qualified_count,delta_sources,delta_saved,qualified_count))
                        conn.commit()
                    except Exception:
                        try: conn.rollback()
                        except Exception: pass
            print("global_direct_sources=",global_direct_added)
            print("global_direct_messages_saved=",global_direct_message_saved)
        except Exception as e:
            if _telegram_floodwait(e):
                _persist_telegram_floodwait(e)
                return
            print("GLOBAL_MESSAGE_DISCOVERY=DEGRADED",type(e).__name__)

        candidates.sort(
            key=lambda x:-x["candidate_score"]
        )

        print("CANDIDATES:")

        for row in candidates[:30]:
            print(
                "%3d | demand=%2d commercial=%2d avito=%2d | %-45s | @%s"
                % (
                    row["candidate_score"],
                    row["demand_messages"],
                    row["commercial_messages"],
                    row["avito_messages"],
                    row["title"][:45],
                    row["username"] or "-",
                )
            )

        if args.apply:

            # Telegram dialog scanning can take long enough for an idle SSL DB
            # connection to be closed by the server/network. Refresh the DB
            # connection before the write phase instead of failing the whole
            # discovery lifecycle after a successful scan.
            try:
                cur.execute("SELECT 1")
            except psycopg2.Error:
                try:
                    conn.close()
                except Exception:
                    pass
                conn=psycopg2.connect(os.environ["DATABASE_URL"])
                cur=conn.cursor()
                cur.execute("SELECT pg_try_advisory_lock(%s)",(884422990,))
                if not bool(cur.fetchone()[0]):
                    print("SOURCE_DISCOVERY=SKIP telegram_session_busy_after_reconnect")
                    conn.close()
                    return
                conn.commit()
                print("db_connection_refreshed=1")

            for row in candidates:

                status=(
                    "ready"
                    if (
                        row["candidate_score"] >= 60
                        and (
                            row.get("global_direct_demand",False)
                            or row.get("avito_messages",0) >= 1
                        )
                    )
                    else "candidate"
                )

                cur.execute("""
                INSERT INTO monitor_source_candidates (
                    id,
                    platform,
                    external_id,
                    username,
                    title,
                    candidate_score,
                    demand_messages,
                    commercial_messages,
                    avito_messages,
                    evidence,
                    status,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (
                    %s,
                    'telegram',
                    %s,%s,%s,%s,%s,%s,%s,
                    %s::jsonb,
                    %s,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (platform,external_id)
                DO UPDATE SET
                    username=EXCLUDED.username,
                    title=EXCLUDED.title,
                    candidate_score=EXCLUDED.candidate_score,
                    demand_messages=EXCLUDED.demand_messages,
                    commercial_messages=EXCLUDED.commercial_messages,
                    avito_messages=EXCLUDED.avito_messages,
                    evidence=EXCLUDED.evidence,
                    status=CASE
                        WHEN monitor_source_candidates.status
                             IN ('approved','rejected')
                        THEN monitor_source_candidates.status
                        ELSE EXCLUDED.status
                    END,
                    last_seen_at=NOW()
                """,(
                    str(uuid.uuid4()),
                    row["external_id"],
                    row["username"],
                    row["title"],
                    row["candidate_score"],
                    row["demand_messages"],
                    row["commercial_messages"],
                    row["avito_messages"],
                    json.dumps(
                        {
                            "messages":row["evidence"],
                            "negative_messages":
                                row["negative_messages"],
                            "qualified_demand_messages":int(row.get("qualified_demand_messages",0) or 0),
                            "already_accessible":True,
                            "auto_join":False,
                            "global_direct_demand":bool(row.get("global_direct_demand",False)),
                            "telegram_access_hash":str(row.get("telegram_access_hash")) if row.get("telegram_access_hash") else None,
                        },
                        ensure_ascii=False,
                    ),
                    status,
                ))

            promoted=0
            # Reconcile every persisted READY candidate, not only rows rediscovered in
            # this run. This prevents previously qualified accessible groups from
            # remaining stuck in READY forever.
            cur.execute("""
            SELECT external_id,username,title,candidate_score,evidence
            FROM monitor_source_candidates
            WHERE platform='telegram' AND status='ready'
              AND candidate_score>=60
              AND (
                    avito_messages>=1
                    OR COALESCE((evidence->>'global_direct_demand')::boolean,FALSE)=TRUE
                  )
              AND COALESCE((evidence->>'already_accessible')::boolean,FALSE)=TRUE
            ORDER BY candidate_score DESC
            """)
            ready_rows=cur.fetchall()
            for external_id,username,title,candidate_score,evidence_payload in ready_rows:
                # Only already-accessible dialogs are promoted. Never auto-join.
                evidence_payload=evidence_payload if isinstance(evidence_payload,dict) else {}
                access_hash=evidence_payload.get('telegram_access_hash')
                source_payload=json.dumps({'telegram_access_hash':str(access_hash)} if access_hash else {},ensure_ascii=False)
                cur.execute("""
                INSERT INTO monitor_sources (
                    id,platform,source_kind,external_id,username,title,is_active,weight,payload,notes,created_at,updated_at
                )
                SELECT %s,'telegram','group',%s,%s,%s,TRUE,%s,%s::jsonb,'auto-discovered from accessible Telegram dialogs',NOW(),NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM monitor_sources
                    WHERE platform='telegram' AND external_id=%s
                )
                """,(
                    str(uuid.uuid4()),
                    external_id,
                    username,
                    title,
                    min(20,max(10,int(candidate_score//10))),
                    source_payload,
                    external_id,
                ))
                inserted_rows=int(cur.rowcount or 0)
                if access_hash:
                    cur.execute("""UPDATE monitor_sources
                                   SET payload=COALESCE(payload,'{}'::jsonb) || jsonb_build_object('telegram_access_hash',%s),
                                       is_active=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN TRUE ELSE is_active END,
                                       notes=CASE WHEN notes='auto-paused: missing telegram access_hash' THEN 'auto-recovered after telegram access_hash refresh' ELSE notes END,
                                       updated_at=NOW()
                                   WHERE platform='telegram' AND external_id=%s""",
                                (str(access_hash),external_id))
                if inserted_rows:
                    promoted += inserted_rows

                # If this source was discovered from an exact recent buyer post,
                # persist that post immediately after source promotion. This avoids
                # waiting for the normal source cursor and lets the same production
                # cycle qualify the buyer even when the new source is scanned late.
                if bool(evidence_payload.get('global_direct_demand',False)):
                    cur.execute("SELECT id,username FROM monitor_sources WHERE platform='telegram' AND external_id=%s LIMIT 1",(external_id,))
                    promoted_source=cur.fetchone()
                    if promoted_source:
                        source_id,source_username=promoted_source
                        for ev in (evidence_payload.get('messages') or []):
                            if not isinstance(ev,dict) or not ev.get('message_id') or not ev.get('text'):
                                continue
                            posted_at=ev.get('posted_at')
                            try:
                                posted_dt=datetime.fromisoformat(str(posted_at).replace('Z','+00:00')) if posted_at else datetime.now(timezone.utc)
                                if posted_dt.tzinfo is None: posted_dt=posted_dt.replace(tzinfo=timezone.utc)
                            except Exception:
                                posted_dt=datetime.now(timezone.utc)
                            msg_url=(f"https://t.me/{source_username}/{ev.get('message_id')}" if source_username else None)
                            cur.execute("""
                              INSERT INTO monitor_messages (
                                id,platform,source_id,external_message_id,external_author_id,
                                author_name,author_username,text,posted_at,url,
                                matched_terms,matched_rules,score_total,score_breakdown,
                                language,intent,priority,ai,payload,status,collected_at
                              ) VALUES (
                                %s,'telegram',%s,%s,%s,%s,%s,%s,%s,%s,
                                %s::jsonb,%s::jsonb,95,%s::jsonb,
                                'ru','buy_service','hot','{}'::jsonb,%s::jsonb,'new',NOW()
                              )
                              ON CONFLICT (platform,source_id,external_message_id) DO NOTHING
                            """,(
                              str(uuid.uuid4()),source_id,str(ev.get('message_id')),ev.get('author_id'),
                              ev.get('author_name'),ev.get('author_username'),ev.get('text'),posted_dt,msg_url,
                              json.dumps([ev.get('query')] if ev.get('query') else [],ensure_ascii=False),
                              json.dumps(['global_buyer_hunt'],ensure_ascii=False),
                              json.dumps({'global_buyer_hunt':True,'query':ev.get('query')},ensure_ascii=False),
                              json.dumps({'global_direct':True,'query':ev.get('query'),'new_source_fastpath':True},ensure_ascii=False),
                            ))
                            global_direct_message_saved += int(cur.rowcount or 0)

                cur.execute("""
                UPDATE monitor_source_candidates
                SET status='approved',last_seen_at=NOW()
                WHERE platform='telegram' AND external_id=%s
                  AND EXISTS (
                    SELECT 1 FROM monitor_sources s
                    WHERE s.platform='telegram' AND s.external_id=%s
                  )
                """,(external_id,external_id))

            conn.commit()
            print("promoted_sources=",promoted)

        else:
            conn.rollback()

        print(
            "mode=",
            "APPLY" if args.apply else "DRY",
        )

        print(
            "candidate_count=",
            len(candidates),
        )

        print(
            "SOURCE_DISCOVERY=PASS"
        )

    finally:
        client.disconnect()
        conn.close()


if __name__=="__main__":
    main()
