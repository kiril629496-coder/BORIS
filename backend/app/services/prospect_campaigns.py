"""BORIS universal B2B prospecting/outreach campaigns.
Reuses prospecting + prospect_discovery + canonical email_queue.
No second crawler, mail transport, or scheduler.
"""
from __future__ import annotations
import base64, hashlib, json, os, re, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import prospect_discovery, prospecting
from app.services.email_queue import enqueue_email
from app.services.owner_outreach_policy import (
    OWNER_OUTREACH_HARD_MAX_DAILY,
    canonical_policy_state,
)

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS prospect_campaigns (
 id BIGSERIAL PRIMARY KEY,
 owner_id BIGINT NOT NULL,
 name VARCHAR(300) NOT NULL,
 niche VARCHAR(300) NOT NULL,
 regions JSONB NOT NULL DEFAULT '[]'::jsonb,
 status VARCHAR(32) NOT NULL DEFAULT 'draft',
 daily_limit INTEGER NOT NULL DEFAULT 50,
 per_domain_daily_limit INTEGER NOT NULL DEFAULT 1,
 min_quality_score INTEGER NOT NULL DEFAULT 55,
 subject_template TEXT NOT NULL,
 body_template TEXT NOT NULL,
 ab_variants JSONB NOT NULL DEFAULT '[]'::jsonb,
 attachment_path TEXT,
 discovered_count INTEGER NOT NULL DEFAULT 0,
 ready_count INTEGER NOT NULL DEFAULT 0,
 created_at TIMESTAMP NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
 activated_at TIMESTAMP,
 paused_at TIMESTAMP,
 copy_revision_status VARCHAR(32),
 copy_revision_version VARCHAR(16),
 copy_revision_needed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS prospect_campaign_members (
 id BIGSERIAL PRIMARY KEY,
 campaign_id BIGINT NOT NULL REFERENCES prospect_campaigns(id) ON DELETE CASCADE,
 company_id BIGINT NOT NULL REFERENCES prospect_companies(id) ON DELETE CASCADE,
 contact_id BIGINT REFERENCES prospect_contacts(id) ON DELETE SET NULL,
 email VARCHAR(320),
 email_domain VARCHAR(255),
 quality_score INTEGER,
 status VARCHAR(32) NOT NULL DEFAULT 'ready',
 skip_reason TEXT,
 email_queue_id BIGINT,
 queued_at TIMESTAMP,
 sent_at TIMESTAMP,
 reply_status VARCHAR(32),
 replied_at TIMESTAMP,
 ab_variant VARCHAR(8),
 copy_version VARCHAR(16),
 created_at TIMESTAMP NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
 UNIQUE(campaign_id, company_id),
 UNIQUE(campaign_id, contact_id)
);
CREATE INDEX IF NOT EXISTS ix_pcm_campaign_status ON prospect_campaign_members(campaign_id,status);
CREATE INDEX IF NOT EXISTS ix_pcm_domain ON prospect_campaign_members(campaign_id,email_domain,status);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pcm_campaign_email_active
ON prospect_campaign_members(campaign_id, lower(email))
WHERE email IS NOT NULL AND (sent_at IS NOT NULL OR status IN ('ready','queued','sent'));
CREATE TABLE IF NOT EXISTS prospect_owner_daily_limits (
 owner_id BIGINT NOT NULL,
 service_date DATE NOT NULL,
 daily_cap INTEGER NOT NULL CHECK (daily_cap > 0 AND daily_cap <= 1000),
 timezone VARCHAR(64) NOT NULL DEFAULT 'Europe/Moscow',
 created_at TIMESTAMP NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
 PRIMARY KEY(owner_id,service_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_prospect_suppression_kind_value ON prospect_suppression(kind,normalized_value);
"""

BAD_SEARCH_DOMAINS = prospect_discovery.BAD_DOMAINS | {
    "hh.ru","2gis.ru","zoon.ru","promportal.su","pulscen.ru","all.biz",
    "cataloxy.ru","yell.ru","orgpage.ru","spr.ru","tiu.ru","blizko.ru","flagma.ru",
    # Content/media/directory sources are not prospect companies. Their generic
    # site email must never consume owner outreach quota.
    "rutube.ru","youtube.com","youtu.be","wikipedia.org","dzen.ru","zen.yandex.ru",
    "stroy-podskazka.ru","bedon.ru",
}

# Canonical owner-outreach policy. The hard ceiling is 20/day:
# 10/day for the first 7 days, then at most 20/day. A lower stored campaign
# limit is allowed; no code/config/schema drift may raise owner outreach above 20.
OWNER_OUTREACH_MAX_DAILY = OWNER_OUTREACH_HARD_MAX_DAILY

# OWNER_OUTREACH_COPY_ROTATION_V2
# Six owner-approved BORIS cold-email copies. They rotate strictly as A→F.
# Every body ends with the only approved visible URL, which _html_email renders
# as a real clickable <a href=...> link. Development outreach is separate.
OWNER_OUTREACH_COPY_VARIANTS = (
    {
        "label": "A",
        "subject": "Не теряются ли у вас обращения?",
        "body": """Добрый день!

Пишу по простой причине: у многих компаний проблема уже не только в том, чтобы получить заявку, а в том, что происходит с ней дальше.

Кому-то не ответили вовремя, кому-то обещали перезвонить, кто-то написал «подумаю» и пропал.

BORIS помогает собирать обращения в CRM, контролировать следующий шаг и возвращать незавершённые диалоги в работу.

Если у вас тоже бывает такая проблема — могу показать, как это работает на практике.

Кирилл
BORIS AI
MAX / WhatsApp: 8 981 967-37-87

https://boris-ai.pro/go/boris""",
    },
    {
        "label": "B",
        "subject": "Идея по работе с Avito",
        "body": """Добрый день!

Если Avito приносит вам обращения, возможно, будет полезна одна идея.

Мы сделали BORIS: он помогает не только работать с объявлениями, но и связывает рекламу с дальнейшей обработкой клиента.

Новое сообщение → ответ менеджера → карточка в CRM → следующий шаг → контроль, чем всё закончилось.

То есть можно видеть не только просмотры и заявки, а что произошло с каждым обращением дальше.

Если интересно — могу коротко показать.

Кирилл
BORIS AI
MAX / WhatsApp: 8 981 967-37-87

https://boris-ai.pro/go/boris""",
    },
    {
        "label": "C",
        "subject": "Что происходит со старыми клиентами?",
        "body": """Добрый день!

Хотел задать один вопрос.

Что сейчас происходит с людьми, которые уже обращались к вам, спрашивали цену или условия, но тогда ничего не купили?

В BORIS есть отдельная реактивация: система находит такие диалоги, учитывает историю общения и помогает вернуть подходящих клиентов в работу.

Иногда в старой базе уже есть продажи, за которые не нужно заново платить рекламой.

Если у вас накопилась история обращений — могу показать принцип работы.

Кирилл
BORIS AI
MAX / WhatsApp: 8 981 967-37-87

https://boris-ai.pro/go/boris""",
    },
    {
        "label": "D",
        "subject": "Как меньше контролировать продажи вручную",
        "body": """Добрый день!

Если приходится постоянно спрашивать менеджеров «ответили клиенту?», «перезвонили?», «что с этой заявкой?» — возможно, BORIS будет полезен.

Мы собираем обращения, переписки, звонки и задачи в одной системе.

BORIS показывает, где клиент завис, кому не ответили, что менеджер обещал и какой следующий шаг должен быть по сделке.

Задача не заменить команду, а убрать часть постоянного ручного контроля у собственника.

Если актуально — покажу на коротком примере.

Кирилл
BORIS AI
MAX / WhatsApp: 8 981 967-37-87

https://boris-ai.pro/go/boris""",
    },
    {
        "label": "E",
        "subject": "Кто отвечает клиентам, когда менеджер занят?",
        "body": """Добрый день!

Одна из причин потери заявок довольно банальная: клиент написал сейчас, а ответ получил через час или уже на следующий день.

В BORIS есть AI-менеджер, которого сначала обучают на информации конкретной компании и тренировочных диалогах.

Он может отвечать на типовые обращения, уточнять необходимые данные и передавать менеджеру уже понятный запрос.

При этом вся переписка сохраняется и контролируется.

Если скорость ответа для вас актуальна — могу показать, как мы это настраиваем.

Кирилл
BORIS AI
MAX / WhatsApp: 8 981 967-37-87

https://boris-ai.pro/go/boris""",
    },
    {
        "label": "F",
        "subject": "Можно покажу одну идею?",
        "body": """Добрый день!

Не буду отправлять длинную презентацию.

Я занимаюсь системой BORIS для маркетинга и продаж. Она помогает собирать обращения из разных каналов, не терять клиентов и автоматизировать часть работы, которую обычно приходится контролировать вручную.

Сейчас ищу несколько компаний, где можно показать BORIS на реальном процессе продаж и посмотреть, где он действительно может быть полезен.

Если тема вам близка — ответьте на письмо, я задам пару вопросов и покажу только то, что относится к вашему бизнесу.

Кирилл
BORIS AI
MAX / WhatsApp: 8 981 967-37-87

https://boris-ai.pro/go/boris""",
    },
)
OWNER_OUTREACH_APPROVED_BODY = OWNER_OUTREACH_COPY_VARIANTS[0]["body"]
OWNER_OUTREACH_APPROVED_BODIES = tuple(x["body"] for x in OWNER_OUTREACH_COPY_VARIANTS)
OWNER_OUTREACH_APPROVED_VARIANTS_JSON = json.dumps(
    list(OWNER_OUTREACH_COPY_VARIANTS), ensure_ascii=False, separators=(",", ":")
)

OWNER_OUTREACH_BANNER_DIR = "/root/BORIS/frontend/public/banners-demo/boris_email_20260910"
OWNER_OUTREACH_BANNERS = (
    # Same order as the four banners approved by the owner in chat.
    ("competitors", f"{OWNER_OUTREACH_BANNER_DIR}/boris_competitors_email650.jpg"),
    ("routine", f"{OWNER_OUTREACH_BANNER_DIR}/boris_routine_email650.jpg"),
    ("scale", f"{OWNER_OUTREACH_BANNER_DIR}/boris_scale_email650.jpg"),
    ("complex", f"{OWNER_OUTREACH_BANNER_DIR}/boris_complex_email650.jpg"),
)
OWNER_OUTREACH_BANNER_FILENAMES = frozenset(os.path.basename(path) for _, path in OWNER_OUTREACH_BANNERS)


def _owner_outreach_banner(member_id:int)->tuple[dict|None,str,str]:
    """Deterministic 1-of-4 owner banner rotation. Never falls back to arbitrary files."""
    idx=(max(1,int(member_id or 1))-1) % len(OWNER_OUTREACH_BANNERS)
    label,path=OWNER_OUTREACH_BANNERS[idx]
    if not os.path.isfile(path):
        return None,"",label
    cid=f"boris-owner-banner-{label}-{int(member_id or 0)}"
    return {
        "path": path,
        "filename": os.path.basename(path),
        "mime": "image/jpeg",
        "inline": True,
        "cid": cid,
    },cid,label


def _owner_outreach_banner_attachment_valid(attachments:list|None, html:str="")->bool:
    items=list(attachments or [])
    if len(items)!=1 or not isinstance(items[0],dict):
        return False
    item=items[0]
    path=os.path.realpath(str(item.get("path") or ""))
    root=os.path.realpath(OWNER_OUTREACH_BANNER_DIR)+os.sep
    filename=str(item.get("filename") or "")
    cid=str(item.get("cid") or "").strip()
    if not path.startswith(root):
        return False
    if filename not in OWNER_OUTREACH_BANNER_FILENAMES or os.path.basename(path)!=filename:
        return False
    if str(item.get("mime") or "").lower()!="image/jpeg":
        return False
    if item.get("inline") is not True or not cid.startswith("boris-owner-banner-"):
        return False
    if not os.path.isfile(path):
        return False
    if html and f"cid:{cid}" not in str(html):
        return False
    return True


class ProspectSearchUnavailable(RuntimeError):
    pass


_SEARCH_LOCK = threading.Lock()
_SEARCH_LAST_AT = 0.0
_SEARCH_HEALTH = {
    "state": "unknown",
    "provider": None,
    "fallback_used": False,
    "last_error": None,
    "checked_at": None,
}


_DDG_BLOCKED_UNTIL = 0.0
_SEARCH_GENERIC_TOKENS = {
    "официальный","сайт","компания","компании","поставщик","услуги",
    "производитель","оптом","купить","цены","цена","каталог","контакты",
}


def _search_query_relevant(query: str, item: dict) -> bool:
    qtokens={
        t for t in prospect_discovery.tokens(query)
        if t not in _SEARCH_GENERIC_TOKENS and len(t) >= 4
    }
    if not qtokens:
        return True
    hay=" ".join(str(item.get(k) or "") for k in ("title","snippet","domain")).lower().replace("ё","е")
    return any(t in hay for t in qtokens)


def get_search_health() -> dict:
    return dict(_SEARCH_HEALTH)


def _set_search_health(state: str, provider: str | None, *, fallback_used: bool = False, last_error: str | None = None) -> None:
    global _SEARCH_HEALTH
    _SEARCH_HEALTH = {
        "state": state,
        "provider": provider,
        "fallback_used": bool(fallback_used),
        "last_error": last_error,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


def _respect_search_interval() -> None:
    """Serialize public-search requests and avoid hammering anti-bot endpoints."""
    global _SEARCH_LAST_AT
    min_interval = max(0.5, float(os.getenv("PROSPECT_SEARCH_MIN_INTERVAL_SEC", "1.2") or 1.2))
    with _SEARCH_LOCK:
        now = time.monotonic()
        wait = min_interval - (now - _SEARCH_LAST_AT)
        if wait > 0:
            time.sleep(wait)
        _SEARCH_LAST_AT = time.monotonic()


def _search_domain_blocked(domain:str)->bool:
    d=(domain or '').lower().removeprefix('www.').rstrip('.')
    return any(d==blocked or d.endswith('.'+blocked) for blocked in BAD_SEARCH_DOMAINS)


# Search-result blocking and recipient-email blocking are related but not
# identical. yandex.ru / ya.ru are bad *search result* hosts (search/map pages),
# yet perfectly valid public mailbox domains used by real Russian businesses.
_OUTREACH_EMAIL_DOMAIN_EXCEPTIONS = {"yandex.ru", "ya.ru"}


def _outreach_email_domain_blocked(domain:str)->bool:
    d=(domain or '').lower().removeprefix('www.').rstrip('.')
    if d in _OUTREACH_EMAIL_DOMAIN_EXCEPTIONS:
        return False
    return _search_domain_blocked(d)


def _accept_search_result(url: str | None, seen: set[str], root_domain, bad_domain) -> tuple[str | None, str]:
    domain = root_domain(url or "")
    if (not url or not domain or domain in seen or bad_domain(domain)
            or _search_domain_blocked(domain)):
        return None, domain
    seen.add(domain)
    return url, domain


def _parse_ddg_results(html: str, max_results: int, *, unwrap_result_url, root_domain, bad_domain) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    out=[]; seen=set()
    for node in soup.select(".result"):
        a=node.select_one(".result__a")
        if not a:
            continue
        url=unwrap_result_url(a.get("href"))
        url,domain=_accept_search_result(url,seen,root_domain,bad_domain)
        if not url:
            continue
        sn=node.select_one(".result__snippet")
        out.append({
            "url":url,
            "domain":domain,
            "title":a.get_text(" ",strip=True)[:500],
            "snippet":sn.get_text(" ",strip=True)[:1000] if sn else "",
            "search_provider":"duckduckgo",
        })
        if len(out)>=max_results:
            break
    return out


def _parse_ddg_lite_results(html: str, max_results: int, *, unwrap_result_url, root_domain, bad_domain) -> list[dict]:
    """Parse DuckDuckGo Lite, used only when the primary DDG HTML endpoint is challenged.

    Lite is the same public provider with a smaller HTML surface and currently
    remains available when html.duckduckgo.com returns the anti-bot 202 page.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "lxml")
    out=[]; seen=set()
    for a in soup.select("a.result-link"):
        href=str(a.get("href") or "").strip()
        url=unwrap_result_url(href) or (href if href.startswith(("http://","https://")) else None)
        url,domain=_accept_search_result(url,seen,root_domain,bad_domain)
        if not url:
            continue
        title=a.get_text(" ",strip=True)[:500]
        # Lite markup changes more often than the primary endpoint. Title/domain
        # are sufficient for the existing relevance gate; snippet is optional.
        out.append({
            "url":url,
            "domain":domain,
            "title":title,
            "snippet":"",
            "search_provider":"duckduckgo_lite",
        })
        if len(out)>=max_results:
            break
    return out


def _decode_bing_result_url(href: str | None) -> str | None:
    href=str(href or "").strip()
    if not href:
        return None
    try:
        p=urlparse(href)
        host=(p.hostname or "").lower()
        if host.endswith("bing.com"):
            raw=(parse_qs(p.query).get("u") or [""])[0]
            if raw.startswith("a1"):
                raw=raw[2:]
            if raw:
                raw += "=" * ((4 - len(raw) % 4) % 4)
                decoded=base64.urlsafe_b64decode(raw.encode()).decode("utf-8","ignore")
                if decoded.startswith(("http://","https://")):
                    return decoded
        if href.startswith(("http://","https://")) and not host.endswith("bing.com"):
            return href
    except Exception:
        return None
    return None


def _parse_bing_results(html: str, max_results: int, *, root_domain, bad_domain) -> list[dict]:
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(html or "","lxml")
    out=[]; seen=set()
    for node in soup.select("li.b_algo"):
        a=node.select_one("h2 a")
        if not a:
            continue
        url=_decode_bing_result_url(a.get("href"))
        url,domain=_accept_search_result(url,seen,root_domain,bad_domain)
        if not url:
            continue
        sn=node.select_one(".b_caption p") or node.select_one("p")
        out.append({
            "url":url,
            "domain":domain,
            "title":a.get_text(" ",strip=True)[:500],
            "snippet":sn.get_text(" ",strip=True)[:1000] if sn else "",
            "search_provider":"bing",
        })
        if len(out)>=max_results:
            break
    return out


def _parse_bing_rss_results(xml_text: str, max_results: int, *, root_domain, bad_domain) -> list[dict]:
    """Parse Bing's public RSS search representation.

    The HTML result page can be geo-poisoned on datacenter IPs while the RSS
    representation still returns the correct query results. Keep the same
    domain/relevance gates as every other discovery source.
    """
    import xml.etree.ElementTree as ET
    try:
        root=ET.fromstring((xml_text or "").encode("utf-8"))
    except Exception:
        return []
    out=[]; seen=set()
    for item in root.findall(".//item"):
        url=str(item.findtext("link") or "").strip()
        url,domain=_accept_search_result(url,seen,root_domain,bad_domain)
        if not url:
            continue
        out.append({
            "url":url,
            "domain":domain,
            "title":str(item.findtext("title") or "").strip()[:500],
            "snippet":str(item.findtext("description") or "").strip()[:1000],
            "search_provider":"bing_rss",
        })
        if len(out)>=max_results:
            break
    return out


def _ensure_owner_copy_db_locks(db):
    """Self-heal DB-level fail-closed locks for the six approved owner copies."""
    body=OWNER_OUTREACH_APPROVED_BODY
    body_literal="'" + body.replace("'", "''") + "'"
    variants_json=OWNER_OUTREACH_APPROVED_VARIANTS_JSON
    variants_literal="'" + variants_json.replace("'", "''") + "'::jsonb"
    body_literals=",".join("'" + x.replace("'", "''") + "'" for x in OWNER_OUTREACH_APPROVED_BODIES)

    db.execute(text("""
      ALTER TABLE prospect_campaigns
      DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_body_exact_v1
    """))

    db.execute(text("""
      UPDATE prospect_campaigns
      SET body_template=:body,
          ab_variants=CAST(:variants AS jsonb),
          attachment_path=NULL,
          copy_revision_status=NULL,
          copy_revision_version=NULL,
          copy_revision_needed_at=NULL,
          updated_at=NOW()
      WHERE account_id='__owner_outreach__'
        AND (
          body_template IS DISTINCT FROM :body
          OR attachment_path IS NOT NULL
          OR ab_variants IS DISTINCT FROM CAST(:variants AS jsonb)
        )
    """),{"body":body,"variants":variants_json})

    db.execute(text(f"""
      ALTER TABLE prospect_campaigns
      ADD CONSTRAINT prospect_campaigns_owner_body_exact_v1
      CHECK (
        account_id IS DISTINCT FROM '__owner_outreach__'
        OR (
          body_template = {body_literal}
          AND attachment_path IS NULL
          AND ab_variants = {variants_literal}
        )
      ) NOT VALID
    """))
    db.execute(text("""
      ALTER TABLE prospect_campaigns
      VALIDATE CONSTRAINT prospect_campaigns_owner_body_exact_v1
    """))

    db.execute(text("""
      DROP TRIGGER IF EXISTS trg_owner_outreach_queue_copy_guard_v1 ON email_queue
    """))
    db.execute(text("""
      DROP FUNCTION IF EXISTS boris_owner_outreach_queue_copy_guard_v1()
    """))
    db.execute(text(f"""
      CREATE FUNCTION boris_owner_outreach_queue_copy_guard_v1()
      RETURNS trigger
      LANGUAGE plpgsql
      AS $guard$
      DECLARE is_owner boolean;
      BEGIN
        IF NEW.ref_type='prospect_campaign_member' AND NEW.ref_id IS NOT NULL THEN
          SELECT EXISTS(
            SELECT 1
            FROM prospect_campaign_members m
            JOIN prospect_campaigns c ON c.id=m.campaign_id
            WHERE m.id=CASE WHEN NEW.ref_id ~ '^[0-9]+$' THEN NEW.ref_id::bigint ELSE -1 END
              AND c.account_id='__owner_outreach__'
          ) INTO is_owner;
          IF is_owner THEN
            IF COALESCE(NEW.text_body,'') NOT IN ({body_literals}) THEN
              RAISE EXCEPTION 'OWNER_OUTREACH_BODY_LOCK_V2';
            END IF;
            IF NEW.attachments IS NULL
               OR jsonb_typeof(NEW.attachments) <> 'array'
               OR jsonb_array_length(NEW.attachments) <> 1
               OR COALESCE(NEW.attachments->0->>'inline','false') <> 'true'
               OR COALESCE(NEW.attachments->0->>'mime','') <> 'image/jpeg'
               OR COALESCE(NEW.attachments->0->>'filename','') NOT IN (
                    'boris_routine_email650.jpg',
                    'boris_scale_email650.jpg',
                    'boris_complex_email650.jpg',
                    'boris_competitors_email650.jpg'
                  )
               OR COALESCE(NEW.attachments->0->>'path','') NOT IN (
                    '/root/BORIS/frontend/public/banners-demo/boris_email_20260910/boris_routine_email650.jpg',
                    '/root/BORIS/frontend/public/banners-demo/boris_email_20260910/boris_scale_email650.jpg',
                    '/root/BORIS/frontend/public/banners-demo/boris_email_20260910/boris_complex_email650.jpg',
                    '/root/BORIS/frontend/public/banners-demo/boris_email_20260910/boris_competitors_email650.jpg'
                  )
               OR COALESCE(NEW.attachments->0->>'cid','') NOT LIKE 'boris-owner-banner-%'
               OR position('cid:'||COALESCE(NEW.attachments->0->>'cid','') in COALESCE(NEW.html_body,''))=0
            THEN
              RAISE EXCEPTION 'OWNER_OUTREACH_ATTACHMENTS_LOCK_V1';
            END IF;
            IF char_length(trim(COALESCE(NEW.subject,''))) < 2
               OR char_length(trim(COALESCE(NEW.subject,''))) > 60
               OR position('+' in COALESCE(NEW.subject,'')) > 0
               OR lower(COALESCE(NEW.subject,'')) ~ '(boris|email|рассыл|сбор баз|база контакт|лидогенерац|холодн|коммерческ|предложение для вашей компании)'
            THEN
              RAISE EXCEPTION 'OWNER_OUTREACH_SUBJECT_LOCK_V1';
            END IF;
            IF position('href="https://boris-ai.pro/go/boris"' in COALESCE(NEW.html_body,''))=0
               OR position('cid:boris-owner-banner-' in COALESCE(NEW.html_body,''))=0
               OR position('https://boris-ai.pro/go/software' in COALESCE(NEW.html_body,''))>0
            THEN
              RAISE EXCEPTION 'OWNER_OUTREACH_LINK_LOCK_V2';
            END IF;
          END IF;
        END IF;
        RETURN NEW;
      END
      $guard$
    """))
    db.execute(text("""
      CREATE TRIGGER trg_owner_outreach_queue_copy_guard_v1
      BEFORE INSERT OR UPDATE OF subject,text_body,html_body,attachments,ref_type,ref_id
      ON email_queue
      FOR EACH ROW EXECUTE FUNCTION boris_owner_outreach_queue_copy_guard_v1()
    """))


def ensure_schema(db):
    # Hot path: no DDL when the owner campaign already has the exact six-copy
    # rotation, strict banner/link trigger and the canonical daily cap.
    ready=db.execute(text("""SELECT
      to_regclass('public.prospect_campaigns') IS NOT NULL
      AND to_regclass('public.prospect_campaign_members') IS NOT NULL
      AND to_regclass('public.prospect_owner_daily_limits') IS NOT NULL
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_campaigns' AND column_name='account_id')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_campaigns' AND column_name='mailbox_id')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_campaigns' AND column_name='ab_variants')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_campaign_members' AND column_name='ab_variant')
      AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='prospect_campaign_members' AND column_name='copy_version')
      AND to_regclass('public.ux_pcm_campaign_email_active') IS NOT NULL
      AND EXISTS(
        SELECT 1 FROM pg_constraint
        WHERE conrelid='public.prospect_campaigns'::regclass
          AND conname='prospect_campaigns_owner_outreach_daily_limit_max20'
          AND convalidated
          AND pg_get_constraintdef(oid) LIKE '%daily_limit <= 20%'
      )
      AND NOT EXISTS(
        SELECT 1 FROM pg_constraint
        WHERE conrelid='public.prospect_campaigns'::regclass
          AND conname IN (
            'prospect_campaigns_owner_outreach_daily_limit_max30',
            'prospect_campaigns_owner_outreach_daily_limit_min30'
          )
      )
      AND NOT EXISTS(
        SELECT 1 FROM prospect_campaigns
        WHERE account_id='__owner_outreach__' AND daily_limit > :cap
      )
      AND EXISTS(
        SELECT 1 FROM pg_constraint
        WHERE conrelid='public.prospect_campaigns'::regclass
          AND conname='prospect_campaigns_owner_body_exact_v1'
          AND convalidated
      )
      AND EXISTS(
        SELECT 1
        FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.email_queue'::regclass
          AND t.tgname='trg_owner_outreach_queue_copy_guard_v1'
          AND t.tgenabled <> 'D' AND NOT t.tgisinternal
          AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_BODY_LOCK_V2%'
          AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_LINK_LOCK_V2%'
          AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_ATTACHMENTS_LOCK_V1%'
          AND pg_get_functiondef(p.oid) LIKE '%boris_routine_email650.jpg%'
      )
      AND NOT EXISTS(
        SELECT 1 FROM prospect_campaigns
        WHERE account_id='__owner_outreach__'
          AND (
            body_template IS DISTINCT FROM :approved_body
            OR attachment_path IS NOT NULL
            OR ab_variants IS DISTINCT FROM CAST(:approved_variants AS jsonb)
          )
      )
    """),{
        'cap':OWNER_OUTREACH_MAX_DAILY,
        'approved_body':OWNER_OUTREACH_APPROVED_BODY,
        'approved_variants':OWNER_OUTREACH_APPROVED_VARIANTS_JSON,
    }).scalar()
    if ready:
        return

    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        db.execute(text(stmt))
    db.execute(text("ALTER TABLE prospect_campaigns ADD COLUMN IF NOT EXISTS account_id VARCHAR(255)"))
    db.execute(text("ALTER TABLE prospect_campaigns ADD COLUMN IF NOT EXISTS mailbox_id BIGINT"))
    db.execute(text("ALTER TABLE prospect_campaigns ADD COLUMN IF NOT EXISTS ab_variants JSONB NOT NULL DEFAULT '[]'::jsonb"))
    db.execute(text("ALTER TABLE prospect_campaigns ADD COLUMN IF NOT EXISTS copy_revision_status VARCHAR(32)"))
    db.execute(text("ALTER TABLE prospect_campaigns ADD COLUMN IF NOT EXISTS copy_revision_version VARCHAR(16)"))
    db.execute(text("ALTER TABLE prospect_campaigns ADD COLUMN IF NOT EXISTS copy_revision_needed_at TIMESTAMP"))
    db.execute(text("ALTER TABLE prospect_campaign_members ADD COLUMN IF NOT EXISTS ab_variant VARCHAR(8)"))
    db.execute(text("ALTER TABLE prospect_campaign_members ADD COLUMN IF NOT EXISTS copy_version VARCHAR(16)"))

    db.execute(text("ALTER TABLE prospect_campaigns DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_outreach_daily_limit_max30"))
    db.execute(text("ALTER TABLE prospect_campaigns DROP CONSTRAINT IF EXISTS prospect_campaigns_owner_outreach_daily_limit_min30"))
    db.execute(text("""UPDATE prospect_campaigns SET daily_limit=LEAST(daily_limit,:cap),updated_at=NOW()
      WHERE account_id='__owner_outreach__' AND daily_limit>:cap"""),{'cap':OWNER_OUTREACH_MAX_DAILY})
    cap_guard=db.execute(text("""SELECT 1 FROM pg_constraint
      WHERE conrelid='public.prospect_campaigns'::regclass
        AND conname='prospect_campaigns_owner_outreach_daily_limit_max20'""")).first()
    if not cap_guard:
        db.execute(text(f"""ALTER TABLE prospect_campaigns
          ADD CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max20
          CHECK (account_id IS DISTINCT FROM '__owner_outreach__' OR daily_limit <= {OWNER_OUTREACH_MAX_DAILY}) NOT VALID"""))
    db.execute(text("ALTER TABLE prospect_campaigns VALIDATE CONSTRAINT prospect_campaigns_owner_outreach_daily_limit_max20"))
    _ensure_owner_copy_db_locks(db)
    db.commit()


def _clean_company_title(title, domain):
    title=(title or "").strip()
    title=re.split(r"\s+[—|–|-]\s+",title,1)[0].strip()
    if not title or len(title)>160: title=(domain or "Компания").split(".")[0]
    return title[:300]

def _raw_search(query, max_results=25):
    """Public search with respectful DDG retry and an independent Bing HTML fallback."""
    import requests
    from app.services.prospect_discovery import (
        SEARCH_URL, USER_AGENT, TIMEOUT, unwrap_result_url, root_domain, bad_domain,
    )

    headers={"User-Agent":USER_AGENT,"Accept-Language":"ru,en;q=0.7"}
    diagnostics=[]
    ddg_had_success=False

    global _DDG_BLOCKED_UNTIL
    if time.time() < float(_DDG_BLOCKED_UNTIL or 0):
        diagnostics.append("ddg_cooldown")
    else:
        _respect_search_interval()
        try:
            r=requests.post(
                SEARCH_URL,
                data={"q":query},
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            low=(r.text or "").lower()
            challenged=(
                r.status_code in (202, 403, 429)
                or "unfortunately, bots use duckduckgo too" in low
                or "select all squares containing a duck" in low
            )
            if challenged:
                _DDG_BLOCKED_UNTIL=time.time()+max(
                    60.0,
                    float(os.getenv("PROSPECT_DDG_COOLDOWN_SEC","300") or 300),
                )
                diagnostics.append(f"ddg_challenge:{r.status_code}")
            elif 200 <= r.status_code < 300:
                parsed=_parse_ddg_results(
                    r.text,max_results,
                    unwrap_result_url=unwrap_result_url,
                    root_domain=root_domain,
                    bad_domain=bad_domain,
                )
                out=[x for x in parsed if _search_query_relevant(query,x)]
                if out:
                    _set_search_health("ready","duckduckgo",fallback_used=False)
                    return out
                if parsed:
                    diagnostics.append("ddg_irrelevant")
                else:
                    ddg_had_success=True
                    diagnostics.append("ddg_empty")
            else:
                diagnostics.append(f"ddg_http:{r.status_code}")
        except Exception as exc:
            diagnostics.append(f"ddg_{type(exc).__name__}")

    # Same-provider low-bandwidth fallback. The primary DDG HTML endpoint can
    # return its anti-bot 202 page while DuckDuckGo Lite is still available.
    # Trying Lite before a different engine preserves relevance and avoids
    # turning an external anti-bot page into a stalled prospecting pipeline.
    _respect_search_interval()
    try:
        r=requests.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q":query},
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        low=(r.text or "").lower()
        challenged=(
            r.status_code in (202, 403, 429)
            or "unfortunately, bots use duckduckgo too" in low
            or "select all squares containing a duck" in low
        )
        if challenged:
            diagnostics.append(f"ddg_lite_challenge:{r.status_code}")
        elif 200 <= r.status_code < 300:
            parsed=_parse_ddg_lite_results(
                r.text,max_results,
                unwrap_result_url=unwrap_result_url,
                root_domain=root_domain,
                bad_domain=bad_domain,
            )
            out=[x for x in parsed if _search_query_relevant(query,x)]
            if out:
                _set_search_health(
                    "ready","duckduckgo_lite",fallback_used=True,
                    last_error=";".join(diagnostics[-4:]) or None,
                )
                return out
            if parsed:
                diagnostics.append("ddg_lite_irrelevant")
            else:
                ddg_had_success=True
                diagnostics.append("ddg_lite_empty")
        else:
            diagnostics.append(f"ddg_lite_http:{r.status_code}")
    except Exception as exc:
        diagnostics.append(f"ddg_lite_{type(exc).__name__}")

    _respect_search_interval()
    bing_had_success=False
    try:
        r=requests.get(
            "https://www.bing.com/search",
            params={"q":query,"format":"rss","setlang":"ru","count":max(10,min(30,int(max_results)))},
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        if 200 <= r.status_code < 300:
            parsed=_parse_bing_rss_results(
                r.text,max_results,
                root_domain=root_domain,
                bad_domain=bad_domain,
            )
            out=[x for x in parsed if _search_query_relevant(query,x)]
            if out:
                _set_search_health(
                    "ready","bing_rss",fallback_used=True,
                    last_error=";".join(diagnostics[-4:]) or None,
                )
                return out
            if parsed:
                diagnostics.append("bing_rss_irrelevant")
            else:
                bing_had_success=True
                diagnostics.append("bing_rss_empty")
        else:
            diagnostics.append(f"bing_rss_http:{r.status_code}")
    except Exception as exc:
        diagnostics.append(f"bing_rss_{type(exc).__name__}")

    _respect_search_interval()
    try:
        r=requests.get(
            "https://www.bing.com/search",
            params={"q":query,"setlang":"ru","mkt":"ru-RU","cc":"ru","count":max(10,min(30,int(max_results)))},
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        low=(r.text or "").lower()
        challenged=(
            r.status_code in (202, 403, 429)
            or "verify you are a human" in low
            or "unusual traffic" in low
        )
        if challenged:
            diagnostics.append(f"bing_challenge:{r.status_code}")
        elif 200 <= r.status_code < 300:
            parsed=_parse_bing_results(
                r.text,max_results,
                root_domain=root_domain,
                bad_domain=bad_domain,
            )
            out=[x for x in parsed if _search_query_relevant(query,x)]
            if out:
                _set_search_health(
                    "ready","bing",fallback_used=True,
                    last_error=";".join(diagnostics[-4:]) or None,
                )
                return out
            if parsed:
                diagnostics.append("bing_irrelevant")
            else:
                bing_had_success=True
                diagnostics.append("bing_empty")
        else:
            diagnostics.append(f"bing_http:{r.status_code}")
    except Exception as exc:
        diagnostics.append(f"bing_{type(exc).__name__}")

    if ddg_had_success or bing_had_success:
        _set_search_health(
            "no_results",
            "duckduckgo" if ddg_had_success else "bing",
            fallback_used=bing_had_success,
            last_error=";".join(diagnostics[-4:]) or None,
        )
        return []

    detail=";".join(diagnostics[-6:]) or "search_providers_unavailable"
    _set_search_health("degraded",None,fallback_used=True,last_error=detail)
    raise ProspectSearchUnavailable(detail)


def _niche_search_result_relevant(niche:str,item:dict)->bool:
    """Fail closed for directories/content and obvious semantic noise."""
    domain=str(item.get('domain') or '')
    if _search_domain_blocked(domain):
        return False
    low=(niche or '').lower().replace('ё','е')
    if not ('сыпуч' in low and ('материал' in low or 'строит' in low)):
        return True
    hay=' '.join(str(item.get(k) or '') for k in ('title','snippet','domain')).lower().replace('ё','е')
    directory_noise=(
        'ваканси','резюме','тендер','госзакуп','закупки.моск','закупки моск',
        'каталог компаний','список компаний','поставщиков найдено','поставщиков в москве',
        'рейтинг компаний','объявления','доска объявлений','маркетплейс',
        'строительных экспертиз','центр экспертиз','справочник компаний',
    )
    if any(x in hay for x in directory_noise):
        return False
    content_noise=(
        'фильм','википед','энциклопед','обзор','что такое','как выбрать',
        'виды и фракции','классификация, характеристики',' фото)',' фото:',
    )
    commercial=(
        'купить','цена','цены','достав','продаж','оптом','постав',
        'производ','заказ','компан','контакт','карьер',
    )
    if any(x in hay for x in content_noise) and not any(x in hay for x in commercial):
        return False
    positive=(
        'сыпуч','песок','щеб','неруд','пгс','грунт','черноз','керамзит','отсев',
        'асфальт','крошк','карьер','бетон','цемент','сухие смеси','строительные смеси',
        'перевалк','инертн','гравий','доставка стройматериал','доставка материалов',
    )
    return any(x in hay for x in positive)


def _company_outreach_block_reason(niche:str,item:dict)->str|None:
    """High-confidence final gate for already discovered companies."""
    domain=str(item.get('domain') or item.get('company_domain') or '')
    if _search_domain_blocked(domain):
        return 'blocked_domain'
    title=str(item.get('title') or item.get('company') or item.get('name') or '').lower().replace('ё','е')
    hard_noise=(
        'фильм','википед','энциклопед','справочник компаний',
        'виды и фракции','виды щебня и его применение',
        'классификация, характеристики','строительных экспертиз','центр экспертиз',
    )
    if any(x in title for x in hard_noise):
        return 'content_or_directory'
    return None


def _niche_discovery_terms(niche:str)->list[str]:
    """Expand only well-known broad niches; generic niches keep one universal term."""
    base=(niche or '').strip()
    terms=[base] if base else []
    low=base.lower().replace('ё','е')
    if 'сыпуч' in low and ('материал' in low or 'строит' in low):
        terms += [
            'песок строительный', 'песок карьерный', 'щебень', 'щебень гранитный',
            'щебень известняковый', 'щебень вторичный', 'пгс', 'нерудные материалы',
            'грунт с доставкой', 'плодородный грунт', 'керамзит', 'отсев',
            'асфальтная крошка', 'чернозем', 'гравий', 'щебень гравийный',
            'песчано-гравийная смесь', 'торф с доставкой', 'скальный грунт',
        ]
    return list(dict.fromkeys(x for x in terms if x))


def discover_niche(owner_id:int, niche:str, regions:list[str], target:int=100, max_queries:int|None=None, query_offset:int=0)->dict:
    """Discover public company websites without holding DB transactions during network search."""
    db=SessionLocal()
    try:
        ensure_schema(db)
    finally:
        db.close()
    inserted=[]; existing=[]; queries=[]; semantic_rejected=0; search_failures=[]; search_providers=set()
    regions=[x.strip() for x in (regions or ["Россия"]) if x and x.strip()] or ["Россия"]
    primary_variants=(
        "официальный сайт компания",
        "поставщик официальный сайт",
        "услуги компания официальный сайт",
        "производитель официальный сайт",
        "оптом официальный сайт",
        "доставка официальный сайт",
        "купить официальный сайт",
        "цены официальный сайт",
        "каталог официальный сайт",
        "компания контакты официальный сайт",
    )
    expansion_variants=(
        "поставщик официальный сайт",
        "доставка официальный сайт",
        "цены официальный сайт",
        "компания контакты официальный сайт",
    )
    query_plan=[]
    for region in regions:
        for term in _niche_discovery_terms(niche):
            variants=primary_variants if term==niche else expansion_variants
            for suffix in variants:
                query_plan.append((region,term,suffix))
    if query_plan:
        offset=max(0,int(query_offset or 0)) % len(query_plan)
        query_plan=query_plan[offset:]+query_plan[:offset]
    query_budget=len(query_plan) if max_queries is None else max(0,min(len(query_plan),int(max_queries)))
    for region,term,suffix in query_plan[:query_budget]:
        if len(inserted)>=target: break
        q=f"{term} {region} {suffix}"; queries.append(q)
        try:
            results=_raw_search(q,max_results=min(30,max(10,target-len(inserted))))
        except Exception as exc:
            search_failures.append({"query":q,"error":type(exc).__name__,"detail":str(exc)[:180]})
            continue
        db=SessionLocal()
        try:
            for item in results:
                if len(inserted)>=target: break
                if not _niche_search_result_relevant(niche,item):
                    semantic_rejected+=1
                    continue
                provider=str(item.get("search_provider") or "unknown")
                search_providers.add(provider)
                prospecting.lock_company_domain(db,owner_id,item["domain"])
                found=db.execute(text("""SELECT id FROM prospect_companies
                  WHERE owner_id=:o AND lower(domain)=lower(:d)
                  ORDER BY id LIMIT 1"""),{"o":owner_id,"d":item["domain"]}).scalar()
                if found:
                    db.commit()
                    existing.append(int(found))
                    continue
                name=_clean_company_title(item["title"],item["domain"]); norm=prospecting.normalize_company_name(name)
                search_tag=f"{niche} | {q}"
                discovery_provider="bing_html" if provider=="bing" else ("ddg_html" if provider=="duckduckgo" else provider[:64])
                cid=db.execute(text("""INSERT INTO prospect_companies(owner_id,name,normalized_name,city,website,domain,status,source,source_url,discovery_status,discovery_provider,discovered_at,search_query,discovery_score)
                  VALUES(:o,:n,:nn,:city,:w,:d,'new','niche_discovery',:w,'selected',:provider,NOW(),:q,70) RETURNING id"""),{"o":owner_id,"n":name,"nn":norm,"city":region,"w":item["url"],"d":item["domain"],"q":search_tag,"provider":discovery_provider}).scalar_one()
                db.commit()
                inserted.append(int(cid))
            db.commit()
        except Exception:
            db.rollback(); raise
        finally:
            db.close()
    search_health=get_search_health()
    search_degraded=bool(search_failures) and search_health.get("state")=="degraded"
    next_offset=(max(0,int(query_offset or 0)) if search_degraded else ((max(0,int(query_offset or 0))+len(queries)) % len(query_plan))) if query_plan else 0
    return {
        "inserted":inserted,
        "existing":sorted(set(existing)),
        "queries":queries,
        "inserted_count":len(inserted),
        "semantic_rejected":semantic_rejected,
        "next_offset":next_offset,
        "query_plan_size":len(query_plan),
        "search_failure_count":len(search_failures),
        "search_failures":search_failures[-5:],
        "search_health":search_health,
        "search_providers":sorted(search_providers),
    }

def parse_batch(owner_id:int, company_ids:list[int], max_pages:int=8, max_companies:int=100)->dict:
    db=SessionLocal(); ensure_schema(db); done=[]; failed=[]
    try:
        for cid in company_ids[:max_companies]:
            try:
                r=prospecting.run_company(db,owner_id,int(cid),max_pages=max_pages); prospecting.rank_company_contacts(db,int(cid)); db.commit()
                done.append({"company_id":int(cid),"emails":len(r.get("emails") or []),"phones":len(r.get("phones") or [])})
            except Exception as e:
                db.rollback(); failed.append({"company_id":int(cid),"error":type(e).__name__})
        return {"done":done,"failed":failed}
    finally: db.close()

def create_campaign(owner_id:int, *, name:str,niche:str,regions:list[str],subject_template:str,body_template:str,ab_variants:list[dict]|None=None,attachment_path:str|None=None,daily_limit:int=50,per_domain_daily_limit:int=1,min_quality_score:int=55,account_id:str|None=None,mailbox_id:int|None=None)->int:
    db=SessionLocal(); ensure_schema(db)
    try:
        if account_id:
            if account_id == '__owner_outreach__':
                exists=db.execute(text("SELECT 1 FROM users WHERE id=:o AND role='owner'"),{"o":owner_id}).first()
            else:
                exists=db.execute(text("SELECT 1 FROM accounts WHERE account_id=:a"),{"a":account_id}).first()
            if not exists: raise ValueError("account not found")
        if mailbox_id and not db.execute(text("SELECT 1 FROM client_mailboxes WHERE id=:m AND (:a IS NULL OR account_id=:a) AND status='active'"),{"m":mailbox_id,"a":account_id}).first(): raise ValueError("mailbox not found for account")
        variants=(ab_variants or [])[:6]
        effective_daily_limit=max(1,min(int(daily_limit),1000))
        if account_id == '__owner_outreach__':
            # Stored owner limit is capped at the canonical 20/day ceiling.
            # _owner_daily_state() still applies warm-up ramp + pacing below it.
            owner_cap=OWNER_OUTREACH_MAX_DAILY
            effective_daily_limit=owner_cap
        cid=db.execute(text("""INSERT INTO prospect_campaigns(owner_id,name,niche,regions,status,daily_limit,per_domain_daily_limit,min_quality_score,subject_template,body_template,ab_variants,attachment_path,account_id,mailbox_id)
          VALUES(:o,:n,:ni,CAST(:r AS JSONB),'draft',:dl,:pl,:qs,:s,:b,CAST(:ab AS JSONB),:att,:account,:mb) RETURNING id"""),{"o":owner_id,"n":name,"ni":niche,"r":json.dumps(regions or [],ensure_ascii=False),"dl":effective_daily_limit,"pl":max(1,min(int(per_domain_daily_limit),10)),"qs":max(0,min(int(min_quality_score),100)),"s":subject_template,"b":body_template,"ab":json.dumps(variants,ensure_ascii=False),"att":attachment_path,"account":account_id,"mb":mailbox_id}).scalar_one(); db.commit(); return int(cid)
    finally: db.close()

def build_audience(owner_id:int,campaign_id:int)->dict:
    db=SessionLocal(); ensure_schema(db)
    try:
        c=db.execute(text("SELECT * FROM prospect_campaigns WHERE id=:c AND owner_id=:o"),{"c":campaign_id,"o":owner_id}).mappings().first()
        if not c: raise ValueError("campaign not found")
        rows=db.execute(text("""SELECT c.id company_id,c.domain company_domain,pc.id contact_id,pc.normalized_value email,pc.quality_score
          FROM prospect_companies c JOIN LATERAL (
            SELECT pc.* FROM prospect_contacts pc
            WHERE pc.company_id=c.id AND pc.kind='email' AND pc.selected_for_outreach=true AND COALESCE(pc.quality_score,0)>=:q
            AND NOT EXISTS(SELECT 1 FROM prospect_suppression s WHERE s.kind='email' AND s.normalized_value=pc.normalized_value)
            ORDER BY pc.quality_score DESC,pc.id LIMIT 1) pc ON true
          WHERE c.owner_id=:o
          AND c.search_query ILIKE '%' || :niche || '%'
          AND (jsonb_array_length(CAST(:regions AS JSONB))=0 OR c.city IN (SELECT jsonb_array_elements_text(CAST(:regions AS JSONB))))
          ORDER BY c.id"""),{"o":owner_id,"q":int(c["min_quality_score"]),"niche":c["niche"],"regions":json.dumps(c["regions"] or [],ensure_ascii=False)}).mappings().all()
        active_company_domains={str(x[0] or "").strip().lower() for x in db.execute(text("""SELECT DISTINCT c.domain
          FROM prospect_campaign_members m JOIN prospect_companies c ON c.id=m.company_id
          WHERE m.campaign_id=:ca AND COALESCE(c.domain,'')<>''
            AND (m.sent_at IS NOT NULL OR m.status IN ('ready','queued','sent'))"""),{"ca":campaign_id}).all()}
        best_by_domain={}
        company_domain_skipped=0
        for raw in rows:
            row=dict(raw)
            domain_key=str(row.get("company_domain") or "").strip().lower()
            if domain_key and domain_key in active_company_domains:
                company_domain_skipped+=1
                continue
            key=domain_key or f"company:{int(row['company_id'])}"
            prev=best_by_domain.get(key)
            if prev is None or (int(row.get("quality_score") or 0),-int(row["company_id"])) > (int(prev.get("quality_score") or 0),-int(prev["company_id"])):
                if prev is not None:
                    company_domain_skipped+=1
                best_by_domain[key]=row
            else:
                company_domain_skipped+=1
        rows=sorted(best_by_domain.values(),key=lambda x:int(x["company_id"]))
        added=invalid_skipped=suppressed_skipped=deduped=0
        for r in rows:
            email=prospecting.normalize_email(r["email"])
            if not email or not prospecting.EMAIL_RE.fullmatch(email):
                db.execute(text("""UPDATE prospect_contacts
                  SET selected_for_outreach=false
                  WHERE id=:ct AND selected_for_outreach=true"""),{"ct":int(r["contact_id"])})
                invalid_skipped+=1
                continue
            if db.execute(text("""SELECT 1 FROM prospect_suppression
              WHERE kind='email' AND normalized_value=:e LIMIT 1"""),{"e":email}).first():
                suppressed_skipped+=1
                continue
            dom=email.split("@",1)[1].lower()
            res=db.execute(text("""INSERT INTO prospect_campaign_members(campaign_id,company_id,contact_id,email,email_domain,quality_score,status)
              VALUES(:ca,:co,:ct,:e,:d,:q,'ready') ON CONFLICT DO NOTHING"""),{"ca":campaign_id,"co":r["company_id"],"ct":r["contact_id"],"e":email,"d":dom,"q":r["quality_score"]})
            changed=int(res.rowcount or 0)
            added+=changed
            if not changed:
                deduped+=1
        total=db.execute(text("SELECT count(*) FROM prospect_campaign_members WHERE campaign_id=:c AND status='ready'"),{"c":campaign_id}).scalar() or 0
        db.execute(text("UPDATE prospect_campaigns SET ready_count=:r,updated_at=NOW() WHERE id=:c"),{"r":total,"c":campaign_id})
        db.commit()
        return {"added":added,"ready":int(total),"invalid_skipped":invalid_skipped,
                "suppressed_skipped":suppressed_skipped,"deduped":deduped,
                "company_domain_skipped":company_domain_skipped}
    finally: db.close()

def set_status(owner_id:int,campaign_id:int,status:str):
    if status not in {"draft","active","paused","completed"}: raise ValueError("bad status")
    db=SessionLocal(); ensure_schema(db)
    try:
        if status=="active":
            row=db.execute(text("""SELECT mailbox_id,account_id,subject_template,body_template,ab_variants,attachment_path
              FROM prospect_campaigns WHERE id=:c AND owner_id=:o"""),
              {"c":campaign_id,"o":owner_id}).mappings().first()
            if not row: raise ValueError("campaign not found")
            if not row.get("mailbox_id"): raise ValueError("mailbox_required")
            if str(row.get("account_id") or "") == "__owner_outreach__":
                errors=validate_owner_outreach_copy_set(
                    str(row.get("subject_template") or ""),
                    str(row.get("body_template") or ""),
                    list(row.get("ab_variants") or []),
                    campaign_id=campaign_id,
                )
                if row.get("attachment_path"):
                    errors.append("base:attachment_path_forbidden")
                if errors:
                    raise ValueError("owner_outreach_content_contract:"+",".join(errors))
        n=db.execute(text("""UPDATE prospect_campaigns SET status=:s,updated_at=NOW(),activated_at=CASE WHEN :s='active' AND activated_at IS NULL THEN NOW() ELSE activated_at END,paused_at=CASE WHEN :s='paused' THEN NOW() ELSE paused_at END WHERE id=:c AND owner_id=:o"""),{"s":status,"c":campaign_id,"o":owner_id}).rowcount; db.commit(); return bool(n)
    finally: db.close()

def suppress_in_db(db,kind:str,value:str,reason:str="opt_out"):
    norm=prospecting.normalize_email(value) if kind=="email" else prospecting.normalize_phone(value)
    if not norm: raise ValueError("invalid value")
    db.execute(text("INSERT INTO prospect_suppression(kind,value,normalized_value,reason) VALUES(:k,:v,:n,:r) ON CONFLICT(kind,normalized_value) DO UPDATE SET reason=EXCLUDED.reason"),{"k":kind,"v":value,"n":norm,"r":reason})
    if kind=="email":
        # Stop not-yet-sent canonical queue rows too. Merely changing the campaign
        # member would not prevent email_queue from delivering an already queued row.
        db.execute(text("""UPDATE email_queue q SET status='cancelled',last_error=:r,next_attempt_at=NULL,updated_at=NOW()
          FROM prospect_campaign_members m WHERE m.email_queue_id=q.id AND lower(m.email)=lower(:n) AND q.status='queued'"""),{"r":reason,"n":norm})
        db.execute(text("UPDATE prospect_campaign_members SET status='suppressed',skip_reason=:r,updated_at=NOW() WHERE lower(email)=lower(:n) AND status IN ('ready','queued')"),{"r":reason,"n":norm})
    return norm

def suppress(kind:str,value:str,reason:str="opt_out"):
    db=SessionLocal(); ensure_schema(db)
    try:
        norm=suppress_in_db(db,kind,value,reason)
        db.commit(); return norm
    finally: db.close()

def _copy_version(subject_template:str,body_template:str,label:str='base')->str:
    """Stable short id for attributing replies to the exact approved copy."""
    raw='\n'.join([
        str(label or 'base').strip(),
        str(subject_template or '').strip(),
        str(body_template or '').strip(),
    ])
    return 'cp'+hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]


def owner_copy_revision_state(db,campaign:dict,*,apply:bool=False)->dict:
    """Durable, no-paid-AI revision backlog for the exact owner copy version."""
    if str(campaign.get("account_id") or "") != "__owner_outreach__":
        return {"status":None,"version":None,"sent":0,"useful_replies":0,"changed":False}
    version=_copy_version(
        str(campaign.get("subject_template") or ""),
        str(campaign.get("body_template") or ""),
        "base",
    )
    stats=db.execute(text("""SELECT
      count(*) FILTER (WHERE m.sent_at IS NOT NULL)::int AS sent,
      count(*) FILTER (
        WHERE m.reply_status='replied'
          AND EXISTS(
            SELECT 1 FROM prospect_inbound_replies pr
            JOIN manager_leads ml ON ml.id=pr.crm_lead_id
            WHERE pr.member_id=m.id
              AND COALESCE(pr.message_kind,'human')='human'
              AND ml.status IN ('квалифицирован','целевое действие','пробный','оплатил','сделка')
          )
      )::int AS useful_replies
      FROM prospect_campaign_members m
      WHERE m.campaign_id=:c AND m.copy_version=:v"""),
      {"c":int(campaign["id"]),"v":version}).mappings().one()
    sent=int(stats.get("sent") or 0)
    useful=int(stats.get("useful_replies") or 0)
    old_status=str(campaign.get("copy_revision_status") or "").strip() or None
    old_version=str(campaign.get("copy_revision_version") or "").strip() or None
    desired=old_status
    if sent>=30 and useful==0:
        desired="pending"
    elif old_status=="pending" and old_version==version and useful>0:
        desired="resolved_positive"
    changed=bool(apply and (desired!=old_status or (desired=="pending" and old_version!=version)))
    if changed:
        if desired=="pending":
            db.execute(text("""UPDATE prospect_campaigns
              SET copy_revision_status='pending',copy_revision_version=:v,
                  copy_revision_needed_at=CASE
                    WHEN copy_revision_version=:v THEN COALESCE(copy_revision_needed_at,NOW())
                    ELSE NOW()
                  END,
                  updated_at=NOW()
              WHERE id=:c"""),{"v":version,"c":int(campaign["id"])})
        else:
            db.execute(text("""UPDATE prospect_campaigns
              SET copy_revision_status=:s,copy_revision_version=:v,updated_at=NOW()
              WHERE id=:c"""),{"s":desired,"v":version,"c":int(campaign["id"])})
    return {
        "status":desired,"version":version,"sent":sent,"useful_replies":useful,
        "changed":changed,"owner_action_required":False,
        "next_action":("safe_copy_revision_backlog" if desired=="pending" else "keep_collecting"),
    }

def _render(t:str,row:dict)->str:
    vals={k:str(row.get(k) or "") for k in ("company","city","website","niche")}
    for k,v in vals.items(): t=t.replace("{"+k+"}",v)
    return t

def _render_subject(template:str,row:dict,member_id:int)->str:
    variants=[x.strip() for x in (template or '').split('||') if x.strip()]
    company=str(row.get("company") or "").strip()
    # Stable pseudo-random choice: different recipients get different subjects,
    # while retries for the same recipient keep the same subject.
    seed="|".join((
        str(int(member_id or 0)),
        company.lower(),
        str(row.get("email") or "").strip().lower(),
        str(row.get("website") or "").strip().lower(),
    ))
    if variants:
        idx=int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],16) % len(variants)
        chosen=variants[idx]
    else:
        chosen=template
    # SEO page titles and missing company names make poor subject lines.
    # Prefer a neutral alternative instead of exposing scraped/empty company data.
    if "{company}" in chosen and (not company or len(company)>42) and variants:
        clean=[v for v in variants if "{company}" not in v]
        if clean:
            idx=int(hashlib.sha256((seed+"|clean").encode("utf-8")).hexdigest()[:16],16) % len(clean)
            chosen=clean[idx]
    rendered=_render(chosen,row).strip()
    return rendered[:120].rstrip()

def _ab_variant(campaign:dict,member_id:int)->dict|None:
    raw=campaign.get("ab_variants") if hasattr(campaign,"get") else None
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception: raw=[]
    if not isinstance(raw,list):
        return None
    clean=[]
    for item in raw[:6]:
        if not isinstance(item,dict):
            continue
        subject=str(item.get("subject") or "").strip()
        body=str(item.get("body") or "").strip()
        banner_path=str(item.get("banner_path") or "").strip()
        label=str(item.get("label") or chr(65+len(clean))).strip()[:8]
        if len(subject)>=2 and len(body)>=20:
            clean.append({"label":label,"subject":subject,"body":body,"banner_path":banner_path})
    if not clean:
        return None
    idx=(int(member_id or 0)-1) % len(clean)
    return clean[idx]


def _ab_variant_by_label(campaign:dict,label:str|None)->dict|None:
    wanted=str(label or '').strip()
    if not wanted:
        return None
    raw=campaign.get("ab_variants") if hasattr(campaign,"get") else None
    if isinstance(raw,str):
        try: raw=json.loads(raw)
        except Exception: raw=[]
    for item in (raw or [])[:6]:
        if not isinstance(item,dict):
            continue
        if str(item.get('label') or '').strip() != wanted:
            continue
        subject=str(item.get('subject') or '').strip()
        body=str(item.get('body') or '').strip()
        if len(subject)>=2 and len(body)>=20:
            return {
                'label':wanted,
                'subject':subject,
                'body':body,
                'banner_path':str(item.get('banner_path') or '').strip(),
            }
    return None


def _owner_next_rotation_variant(db,campaign:dict)->dict:
    """Strict A→F rotation based on successful sends, not contact ids."""
    sent=int(db.execute(text("""SELECT count(*)
      FROM prospect_campaign_members
      WHERE campaign_id=:c AND sent_at IS NOT NULL
        AND ab_variant IN ('A','B','C','D','E','F')"""),
      {'c':int(campaign['id'])}).scalar() or 0)
    variants=list(OWNER_OUTREACH_COPY_VARIANTS)
    return dict(variants[sent % len(variants)])


def _owner_banner_slot_for_label(label:str|None)->int:
    labels=[str(x['label']) for x in OWNER_OUTREACH_COPY_VARIANTS]
    try:
        idx=labels.index(str(label or '').strip())
    except ValueError:
        idx=0
    return (idx % len(OWNER_OUTREACH_BANNERS)) + 1


def _expected_owner_copy_version(campaign:dict, member_id:int, label:str|None=None)->tuple[str,dict|None]:
    """Return the exact stored-copy version assigned to this owner member."""
    ab=_ab_variant_by_label(campaign,label) if label else _ab_variant(campaign,int(member_id))
    subject_src=str((ab or {}).get("subject") or campaign.get("subject_template") or "")
    body_src=str((ab or {}).get("body") or campaign.get("body_template") or "")
    effective_label=str((ab or {}).get("label") or "base")
    return _copy_version(subject_src,body_src,effective_label),ab


def _requeue_stale_owner_copy(db,campaign:dict)->int:
    """Cancel stale unsent owner outreach payloads before they can reach SMTP."""
    if str(campaign.get("account_id") or "") != "__owner_outreach__":
        return 0
    rows=db.execute(text("""SELECT m.id member_id,m.copy_version,m.ab_variant,q.id queue_id
      FROM prospect_campaign_members m
      JOIN email_queue q ON q.id=m.email_queue_id
      WHERE m.campaign_id=:c AND m.status='queued' AND q.status='queued'
      ORDER BY m.id FOR UPDATE OF m,q SKIP LOCKED"""),{"c":int(campaign["id"])}).mappings().all()
    requeued=0
    for row in rows:
        expected,_ab=_expected_owner_copy_version(campaign,int(row["member_id"]),row.get("ab_variant"))
        if str(row.get("copy_version") or "") == expected:
            continue
        changed=db.execute(text("""UPDATE email_queue
          SET status='cancelled',
              idempotency_key=LEFT(COALESCE(idempotency_key,'prospect')||':sup:'||id::text,160),
              last_error='COPY_VERSION_SUPERSEDED',
              next_attempt_at=NULL,updated_at=NOW()
          WHERE id=:q AND status='queued' RETURNING id"""),{"q":int(row["queue_id"])}).first()
        if not changed:
            continue
        db.execute(text("""UPDATE prospect_campaign_members
          SET status='ready',email_queue_id=NULL,queued_at=NULL,sent_at=NULL,
              skip_reason=NULL,ab_variant=NULL,copy_version=NULL,updated_at=NOW()
          WHERE id=:m AND status='queued'"""),{"m":int(row["member_id"])})
        requeued+=1
    return requeued


def owner_queue_pre_send_guard(db, member_id:int, queue_id:int, subject:str, body:str, html:str, attachments)->dict:
    """Final fail-closed owner outreach guard executed after queue claim, before SMTP."""
    member=db.execute(text("""SELECT id,campaign_id,status,email_queue_id,copy_version,ab_variant
      FROM prospect_campaign_members WHERE id=:m FOR UPDATE"""),{"m":int(member_id)}).mappings().first()
    if not member:
        return {"allowed":False,"reason":"prospect_member_missing","requeue":False}
    campaign=db.execute(text("SELECT * FROM prospect_campaigns WHERE id=:c"),
                        {"c":int(member["campaign_id"])}).mappings().first()
    if not campaign:
        return {"allowed":False,"reason":"prospect_campaign_missing","requeue":False}
    c=dict(campaign)
    if str(c.get("account_id") or "") != "__owner_outreach__":
        return {"allowed":True,"reason":"not_owner_outreach"}
    if str(member.get("status") or "")=="sent":
        return {"allowed":False,"reason":"prospect_member_already_sent","requeue":False}

    # Final quota truth is checked after queue claim and immediately before SMTP.
    # This protects against stale feeder code or a mid-day policy/config change.
    daily=_owner_daily_state(db,int(c["owner_id"]))
    tz_name=str(daily.get("timezone") or "Europe/Moscow")
    today=daily.get("today")
    successful=int(db.execute(text("""SELECT count(*)
      FROM prospect_campaign_members m
      JOIN prospect_campaigns c2 ON c2.id=m.campaign_id
      WHERE c2.owner_id=:o AND c2.account_id='__owner_outreach__'
        AND m.sent_at IS NOT NULL
        AND timezone(:tz,m.sent_at)::date=:today"""),{
            "o":int(c["owner_id"]),"tz":tz_name,"today":today,
        }).scalar() or 0)
    attempted=int(db.execute(text("""SELECT count(*)
      FROM prospect_campaign_members m
      JOIN prospect_campaigns c2 ON c2.id=m.campaign_id
      WHERE c2.owner_id=:o AND c2.account_id='__owner_outreach__'
        AND m.queued_at IS NOT NULL
        AND timezone(:tz,m.queued_at)::date=:today"""),{
            "o":int(c["owner_id"]),"tz":tz_name,"today":today,
        }).scalar() or 0)
    # Independent final-volume boundary. Do not trust the mutable feeder ramp
    # or UI policy modules: PostgreSQL V3 and this helper are the backend source
    # of truth for real outbound traffic.
    from app.services.owner_outreach_volume_safety import canonical_cap as _backend_volume_cap
    canonical_cap=_backend_volume_cap(int(daily.get("age") or 0))
    effective_cap=min(
        max(1,int(daily.get("cap") or canonical_cap)),
        canonical_cap,
        OWNER_OUTREACH_HARD_MAX_DAILY,
    )
    raw_attempt_cap=int(daily.get("attempt_cap") or 0)
    effective_attempt_cap=min(raw_attempt_cap,effective_cap*3) if raw_attempt_cap>0 else 0
    # Multiple queue workers may claim different rows at the same time. Claims
    # are committed as status='sending' before this guard runs, so rank the
    # current row among today's owner rows already in-flight. This reserves
    # deterministic daily slots without holding a DB transaction across SMTP.
    inflight_position=int(db.execute(text("""SELECT count(*)
      FROM email_queue q
      JOIN prospect_campaign_members m ON m.id=CASE
        WHEN q.ref_type='prospect_campaign_member' AND q.ref_id ~ '^[0-9]+$'
        THEN q.ref_id::bigint ELSE -1 END
      JOIN prospect_campaigns c2 ON c2.id=m.campaign_id
      WHERE c2.owner_id=:o AND c2.account_id='__owner_outreach__'
        AND q.status='sending' AND q.id<=:qid
        AND m.queued_at IS NOT NULL
        AND timezone(:tz,m.queued_at)::date=:today"""),{
            "o":int(c["owner_id"]),"qid":int(queue_id),
            "tz":tz_name,"today":today,
        }).scalar() or 0)
    quota_reason=None
    if successful + max(1,inflight_position) > effective_cap:
        quota_reason="owner_daily_cap_reached_pre_smtp"
    elif effective_attempt_cap>0 and attempted > effective_attempt_cap:
        quota_reason="owner_attempt_cap_exceeded_pre_smtp"
    if quota_reason:
        db.execute(text("""UPDATE prospect_campaign_members
          SET status='ready',email_queue_id=NULL,queued_at=NULL,sent_at=NULL,
              skip_reason=NULL,ab_variant=NULL,copy_version=NULL,updated_at=NOW()
          WHERE id=:m AND status<>'sent'"""),{"m":int(member_id)})
        return {
            "allowed":False,"reason":quota_reason,"requeue":True,
            "daily_cap":effective_cap,
            "feeder_daily_cap":int(daily.get("cap") or 0),
            "canonical_daily_cap":canonical_cap,
            "successful":successful,"attempted":attempted,
            "attempt_cap":effective_attempt_cap,
            "service_date":today,
        }

    expected,ab=_expected_owner_copy_version(c,int(member_id),member.get("ab_variant"))
    actual=str(member.get("copy_version") or "")
    errors=_owner_outreach_contract(str(subject or ""),str(body or ""),str(html or ""),attachments or [])
    # Final A→F / banner 1→4→1→2 invariant immediately before SMTP.
    expected_slot=_owner_banner_slot_for_label(member.get("ab_variant"))
    expected_banner,_,_= _owner_outreach_banner(expected_slot)
    actual_filename=str(((attachments or [{}])[0] or {}).get('filename') or '') if attachments else ''
    if not expected_banner or actual_filename != str(expected_banner.get('filename') or ''):
        errors.append('owner_banner_rotation_mismatch')
    active=str(c.get("status") or "")=="active"
    if active and actual==expected and not errors:
        # Repair a crash between enqueue and member-link persistence without
        # allowing a duplicate send.
        db.execute(text("""UPDATE prospect_campaign_members
          SET status='queued',email_queue_id=:q,queued_at=COALESCE(queued_at,NOW()),
              copy_version=:cv,ab_variant=:ab,updated_at=NOW()
          WHERE id=:m AND status<>'sent'"""),
          {"q":int(queue_id),"cv":expected,"ab":((ab or {}).get("label") if ab else None),"m":int(member_id)})
        return {"allowed":True,"reason":"owner_content_verified","copy_version":expected}

    reason=(
        "campaign_not_active" if not active else
        ("copy_version_mismatch" if actual!=expected else "content_contract_failed")
    )
    if str(member.get("status") or "")!="sent":
        db.execute(text("""UPDATE prospect_campaign_members
          SET status='ready',email_queue_id=NULL,queued_at=NULL,sent_at=NULL,
              skip_reason=NULL,ab_variant=NULL,copy_version=NULL,updated_at=NOW()
          WHERE id=:m AND status<>'sent'"""),{"m":int(member_id)})
    return {
        "allowed":False,"reason":reason,"requeue":True,
        "expected_copy_version":expected,"actual_copy_version":actual,
        "errors":errors[:20],
    }


def owner_copy_integrity_health(owner_id:int, *, self_heal:bool=True)->dict:
    """Production truth for the locked owner email copy."""
    from app.services import email_tracking as _tracking
    _tracking.ensure_schema()
    db=SessionLocal()
    healed=[]
    try:
        # DETECT + schema/campaign SELF-HEAL.
        ensure_schema(db)

        campaigns=[dict(r) for r in db.execute(text("""
          SELECT id,status,subject_template,body_template,ab_variants,attachment_path
          FROM prospect_campaigns
          WHERE owner_id=:o AND account_id='__owner_outreach__' AND status='active'
          ORDER BY id
        """),{"o":int(owner_id)}).mappings().all()]
        if not campaigns:
            return {
                "state":"ok","status":"idle","owner_id":int(owner_id),
                "active_campaigns":0,"owner_action_required":False,
                "healed":[],"pending_copy_drift":0,"pending_tracking_drift":0,
            }

        if self_heal:
            # Only queued/retrying rows are safe to rewrite here. A sending row
            # is left to owner_queue_pre_send_guard() to avoid racing SMTP.
            drift=[dict(r) for r in db.execute(text("""
              SELECT q.id AS queue_id,m.id AS member_id,m.campaign_id
              FROM email_queue q
              JOIN prospect_campaign_members m ON m.email_queue_id=q.id
              JOIN prospect_campaigns c ON c.id=m.campaign_id
              WHERE c.owner_id=:o
                AND c.account_id='__owner_outreach__'
                AND c.status='active'
                AND q.status IN ('queued','retrying')
                AND (
                  NOT (
                    q.text_body = c.body_template
                    OR EXISTS (
                      SELECT 1
                      FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                      WHERE v->>'body' = q.text_body
                    )
                  )
                  OR position('href="https://boris-ai.pro/go/boris"' in COALESCE(q.html_body,''))=0
                  OR position('cid:boris-owner-banner-' in COALESCE(q.html_body,''))=0
                  OR position('https://boris-ai.pro/go/software' in COALESCE(q.html_body,''))>0
                  OR CASE WHEN jsonb_typeof(q.attachments)='array' THEN jsonb_array_length(q.attachments) ELSE -1 END <> 1
                  OR COALESCE(q.attachments->0->>'inline','false') <> 'true'
                  OR COALESCE(q.attachments->0->>'mime','') <> 'image/jpeg'
                  OR COALESCE(q.attachments->0->>'filename','') NOT IN ('boris_routine_email650.jpg','boris_scale_email650.jpg','boris_complex_email650.jpg','boris_competitors_email650.jpg')
                  OR COALESCE(q.attachments->0->>'path','') NOT LIKE '/root/BORIS/frontend/public/banners-demo/boris_email_20260910/%'
                  OR COALESCE(q.attachments->0->>'cid','') NOT LIKE 'boris-owner-banner-%'
                  OR position('cid:'||COALESCE(q.attachments->0->>'cid','') in COALESCE(q.html_body,''))=0
                  OR char_length(trim(COALESCE(q.subject,''))) < 2
                  OR char_length(trim(COALESCE(q.subject,''))) > 60
                  OR position('+' in COALESCE(q.subject,'')) > 0
                  OR lower(COALESCE(q.subject,'')) ~ '(boris|email|рассыл|сбор баз|база контакт|лидогенерац|холодн|коммерческ|предложение для вашей компании)'
                )
              ORDER BY q.id
              LIMIT 200
            """),{"o":int(owner_id)}).mappings().all()]
            campaign_ids=set()
            repaired=0
            for row in drift:
                changed=db.execute(text("""
                  UPDATE email_queue
                  SET status='cancelled',
                      idempotency_key=LEFT(COALESCE(idempotency_key,'prospect')||':copyheal:'||id::text,160),
                      last_error='OWNER_COPY_POLICY_DRIFT',
                      next_attempt_at=NULL,updated_at=NOW()
                  WHERE id=:q AND status IN ('queued','retrying')
                  RETURNING id
                """),{"q":int(row["queue_id"])}).first()
                if not changed:
                    continue
                db.execute(text("""
                  UPDATE prospect_campaign_members
                  SET status='ready',email_queue_id=NULL,queued_at=NULL,sent_at=NULL,
                      skip_reason=NULL,ab_variant=NULL,copy_version=NULL,updated_at=NOW()
                  WHERE id=:m AND status<>'sent'
                """),{"m":int(row["member_id"])})
                campaign_ids.add(int(row["campaign_id"]))
                repaired+=1
            for cid in campaign_ids:
                db.execute(text("""
                  UPDATE prospect_campaigns
                  SET ready_count=(
                    SELECT count(*) FROM prospect_campaign_members
                    WHERE campaign_id=:c AND status='ready'
                  ),updated_at=NOW()
                  WHERE id=:c
                """),{"c":cid})
            if repaired:
                db.commit()
                healed.append(f"requeued_copy_drift:{repaired}")
            else:
                db.rollback()

        lock_state=dict(db.execute(text("""
          SELECT
            EXISTS(
              SELECT 1 FROM pg_constraint
              WHERE conrelid='public.prospect_campaigns'::regclass
                AND conname='prospect_campaigns_owner_body_exact_v1'
                AND convalidated
            ) AS campaign_lock_ok,
            EXISTS(
              SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
              WHERE t.tgrelid='public.email_queue'::regclass
                AND t.tgname='trg_owner_outreach_queue_copy_guard_v1'
                AND t.tgenabled<>'D' AND NOT t.tgisinternal
                AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_BODY_LOCK_V2%'
                AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_LINK_LOCK_V2%'
                AND pg_get_functiondef(p.oid) LIKE '%OWNER_OUTREACH_ATTACHMENTS_LOCK_V1%'
                AND pg_get_functiondef(p.oid) LIKE '%boris_routine_email650.jpg%'
            ) AS queue_lock_ok
        """)).mappings().one())

        campaign_drift=int(db.execute(text("""
          SELECT count(*)
          FROM prospect_campaigns
          WHERE owner_id=:o AND account_id='__owner_outreach__' AND status='active'
            AND (
              body_template IS DISTINCT FROM :body
              OR attachment_path IS NOT NULL
              OR ab_variants IS DISTINCT FROM CAST(:variants AS jsonb)
            )
        """),{
            "o":int(owner_id),
            "body":OWNER_OUTREACH_APPROVED_BODY,
            "variants":OWNER_OUTREACH_APPROVED_VARIANTS_JSON,
        }).scalar() or 0)

        pending=dict(db.execute(text("""
          SELECT
            count(*) FILTER (
              WHERE q.status IN ('queued','retrying','sending') AND (
                NOT (
                  q.text_body = c.body_template
                  OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                    WHERE v->>'body' = q.text_body
                  )
                )
                OR position('href="https://boris-ai.pro/go/boris"' in COALESCE(q.html_body,''))=0
                OR position('cid:boris-owner-banner-' in COALESCE(q.html_body,''))=0
                OR position('https://boris-ai.pro/go/software' in COALESCE(q.html_body,''))>0
                OR CASE WHEN jsonb_typeof(q.attachments)='array' THEN jsonb_array_length(q.attachments) ELSE -1 END <> 1
                OR COALESCE(q.attachments->0->>'inline','false') <> 'true'
                OR COALESCE(q.attachments->0->>'mime','') <> 'image/jpeg'
                OR COALESCE(q.attachments->0->>'filename','') NOT IN ('boris_routine_email650.jpg','boris_scale_email650.jpg','boris_complex_email650.jpg','boris_competitors_email650.jpg')
                OR COALESCE(q.attachments->0->>'path','') NOT LIKE '/root/BORIS/frontend/public/banners-demo/boris_email_20260910/%'
                OR COALESCE(q.attachments->0->>'cid','') NOT LIKE 'boris-owner-banner-%'
                OR position('cid:'||COALESCE(q.attachments->0->>'cid','') in COALESCE(q.html_body,''))=0
                OR char_length(trim(COALESCE(q.subject,''))) < 2
                OR char_length(trim(COALESCE(q.subject,''))) > 60
                OR position('+' in COALESCE(q.subject,'')) > 0
                OR lower(COALESCE(q.subject,'')) ~ '(boris|email|рассыл|сбор баз|база контакт|лидогенерац|холодн|коммерческ|предложение для вашей компании)'
              )
            )::int AS copy_drift,
            count(*) FILTER (
              WHERE q.status IN ('queued','retrying','sending')
                AND (
                  t.id IS NULL
                  OR position('data-boris-open-tracking="1"' in COALESCE(q.html_body,''))=0
                )
            )::int AS tracking_drift,
            count(*) FILTER (WHERE q.status='sending')::int AS sending
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.email_queue_id=q.id
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__' AND c.status='active'
        """),{"o":int(owner_id)}).mappings().one())

        first_locked=db.execute(text("""
          SELECT min(q.sent_at)
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.email_queue_id=q.id
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'
            AND q.status='sent' AND q.sent_at IS NOT NULL
            AND (
              q.text_body=c.body_template
              OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                WHERE v->>'body'=q.text_body
              )
            )
            AND t.id IS NOT NULL
            AND position('data-boris-open-tracking="1"' in COALESCE(q.html_body,''))>0
            AND position('href="https://boris-ai.pro/go/boris"' in COALESCE(q.html_body,''))>0
            AND position('cid:boris-owner-banner-' in COALESCE(q.html_body,''))>0
            AND position('https://boris-ai.pro/go/software' in COALESCE(q.html_body,''))=0
            AND CASE WHEN jsonb_typeof(q.attachments)='array' THEN jsonb_array_length(q.attachments) ELSE -1 END=1
            AND COALESCE(q.attachments->0->>'filename','') IN ('boris_routine_email650.jpg','boris_scale_email650.jpg','boris_complex_email650.jpg','boris_competitors_email650.jpg')
        """),{"o":int(owner_id)}).scalar()

        wrong_after_lock=0
        correct_after_lock=0
        if first_locked is not None:
            after=dict(db.execute(text("""
              SELECT
                count(*) FILTER (
                  WHERE q.sent_at>=:start AND NOT (
                    (
                      q.text_body=c.body_template
                      OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                        WHERE v->>'body'=q.text_body
                      )
                    )
                    AND position('href="https://boris-ai.pro/go/boris"' in COALESCE(q.html_body,''))>0
                    AND position('cid:boris-owner-banner-' in COALESCE(q.html_body,''))>0
                    AND position('https://boris-ai.pro/go/software' in COALESCE(q.html_body,''))=0
                    AND CASE WHEN jsonb_typeof(q.attachments)='array' THEN jsonb_array_length(q.attachments) ELSE -1 END=1
                    AND COALESCE(q.attachments->0->>'filename','') IN ('boris_routine_email650.jpg','boris_scale_email650.jpg','boris_complex_email650.jpg','boris_competitors_email650.jpg')
                  )
                )::int AS wrong,
                count(*) FILTER (
                  WHERE q.sent_at>=:start
                    AND (
                      q.text_body=c.body_template
                      OR EXISTS (
                        SELECT 1 FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                        WHERE v->>'body'=q.text_body
                      )
                    )
                    AND position('cid:boris-owner-banner-' in COALESCE(q.html_body,''))>0
                )::int AS correct
              FROM email_queue q
              JOIN prospect_campaign_members m ON m.email_queue_id=q.id
              JOIN prospect_campaigns c ON c.id=m.campaign_id
              WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'
                AND q.status='sent' AND q.sent_at IS NOT NULL
            """),{"o":int(owner_id),"start":first_locked}).mappings().one())
            wrong_after_lock=int(after.get("wrong") or 0)
            correct_after_lock=int(after.get("correct") or 0)

        historical_wrong=int(db.execute(text("""
          SELECT count(*)
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.email_queue_id=q.id
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'
            AND q.status='sent' AND q.sent_at IS NOT NULL
            AND NOT (
              q.text_body=c.body_template
              OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                WHERE v->>'body'=q.text_body
              )
            )
            AND (:start IS NULL OR q.sent_at<:start)
        """),{"o":int(owner_id),"start":first_locked}).scalar() or 0)

        latest_row=db.execute(text("""
          SELECT q.id,q.subject,q.sent_at,
                 (
                   q.text_body=c.body_template
                   OR EXISTS (
                     SELECT 1 FROM jsonb_array_elements(COALESCE(c.ab_variants,'[]'::jsonb)) v
                     WHERE v->>'body'=q.text_body
                   )
                 ) AS body_exact,
                 (position('data-boris-open-tracking="1"' in COALESCE(q.html_body,''))>0) AS tracking_marker,
                 (t.id IS NOT NULL) AS tracker_row
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.email_queue_id=q.id
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'
            AND q.status='sent' AND q.sent_at IS NOT NULL
          ORDER BY q.sent_at DESC,q.id DESC
          LIMIT 1
        """),{"o":int(owner_id)}).mappings().first()
        latest=dict(latest_row) if latest_row else None

        critical=[]
        if not bool(lock_state.get("campaign_lock_ok")):
            critical.append("campaign_db_lock_missing")
        if not bool(lock_state.get("queue_lock_ok")):
            critical.append("queue_db_lock_missing")
        if campaign_drift:
            critical.append(f"campaign_copy_drift:{campaign_drift}")
        if int(pending.get("copy_drift") or 0):
            critical.append(f"pending_copy_drift:{int(pending.get('copy_drift') or 0)}")
        if int(pending.get("tracking_drift") or 0):
            critical.append(f"pending_tracking_drift:{int(pending.get('tracking_drift') or 0)}")
        if wrong_after_lock:
            critical.append(f"wrong_sent_after_lock:{wrong_after_lock}")
        if latest and (
            not bool(latest.get("body_exact"))
            or not bool(latest.get("tracking_marker"))
            or not bool(latest.get("tracker_row"))
        ):
            critical.append("latest_sent_not_locked")

        return {
            "state":"critical" if critical else "ok",
            "status":"copy_integrity_failed" if critical else "copy_integrity_ok",
            "owner_id":int(owner_id),
            "active_campaigns":len(campaigns),
            "campaign_lock_ok":bool(lock_state.get("campaign_lock_ok")),
            "queue_lock_ok":bool(lock_state.get("queue_lock_ok")),
            "campaign_copy_drift":campaign_drift,
            "pending_copy_drift":int(pending.get("copy_drift") or 0),
            "pending_tracking_drift":int(pending.get("tracking_drift") or 0),
            "sending":int(pending.get("sending") or 0),
            "first_locked_sent_at":first_locked.isoformat() if first_locked else None,
            "correct_sent_after_lock":correct_after_lock,
            "wrong_sent_after_lock":wrong_after_lock,
            "historical_prelock_wrong_sent":historical_wrong,
            "latest_sent":latest,
            "critical":critical,
            "healed":healed,
            "owner_action_required":bool(wrong_after_lock),
            "self_heal":"DB locks + safe queued-row requeue + final pre-SMTP guard",
        }
    except Exception as exc:
        db.rollback()
        return {
            "state":"critical","status":"copy_integrity_exception",
            "owner_id":int(owner_id),"active_campaigns":0,
            "critical":[type(exc).__name__],"healed":healed,
            "owner_action_required":False,
        }
    finally:
        db.close()


def _generate_openai_email_copy(*, company:str, niche:str, city:str='', website:str='', account_id:str='', idempotency_key:str='')->tuple[str,str]:
    """Personalized prospecting copy through the canonical exactly-once paid text guard."""
    if not str(account_id or '').strip():
        raise RuntimeError("PROSPECT_COPY_ACCOUNT_REQUIRED")
    if not str(idempotency_key or '').strip():
        raise RuntimeError("PROSPECT_COPY_IDEMPOTENCY_REQUIRED")
    prompt=f"""Компания: {company or 'компания'}
Ниша: {niche}
Город: {city}
Сайт: {website}

Напиши персональное первое B2B email-письмо от BORIS AI на русском. Главная цель — вызвать у предпринимателя желание попробовать BORIS и ответить на письмо или написать в MAX/WhatsApp.
Тема: до 60 символов, естественная и похожая на обычное деловое письмо. Не используй в теме слова BORIS, email, рассылка, сбор базы, база контактов, лидогенерация, холодная рассылка, коммерческое предложение. Делай тему конкретной и по возможности с цифрой: часы в день, число менеджеров, количество заявок, дни, стоимость сотрудника или стоимость потерь. Отдельная сильная линия — «цифровой отдел продаж и маркетинга»: сравнивай его с наймом 2–3 сотрудников, несколькими отдельными сервисами, ручной обработкой 10–100 обращений и потерями времени. Предпочитай форматы вроде «2 менеджера по 80 000 ₽ или автоматизация?», «3 часа в день × 22 дня — сколько стоит рутина?», «Цифровой отдел продаж вместо 2 менеджеров?», «Цифровой отдел продаж и маркетинга вместо 3 сотрудников?». Любая цифра должна быть вопросом, примером или сценарием, а не неподтверждённым обещанием результата.
Тело: 900–1500 знаков, короткие абзацы, человеческий язык, конкретика по нише. Не делай тяжёлую простыню.

Обязательно коротко и простыми словами раскрой ключевые возможности BORIS, не превращая письмо в длинный каталог:
- AI-Авитолог: объявления, ставки, заголовки, описания, А/Б-тесты, KPI и красная стоимость обращения;
- AI-менеджер продаж: отвечает, квалифицирует и передаёт горячего клиента человеку;
- CRM: автоматически создаёт карточки клиентов, сделки и следующие задачи;
- телефония и AI-анализ звонков: связывает звонок с рекламой и карточкой CRM, находит пропуски и потерянные договорённости;
- AI-звонилка: работает с новой, тёплой и старой базой по заданным сценариям;
- виртуальный РОП: контролирует переписки, звонки, CRM и выполнение задач менеджерами;
- реактивация: находит забытых клиентов и возвращает их повторными касаниями;
- сквозная аналитика: реклама → обращение → целевой клиент → сделка → выручка;
- AI-фотостудия и копирайтер: баннеры, изображения, заголовки и продающие тексты;
- личный кабинет, память и журнал действий: владелец видит работу онлайн, а BORIS запоминает результаты тестов и использует лучший опыт дальше.

Позиционирование относительно живого специалиста: BORIS не «увольняет человека». Он забирает рутину, массовые операции, регулярный мониторинг и аналитику, работает с большим объёмом данных и не забывает проверки. Живой специалист нужен для контроля всей рекламной кампании, стратегии, качества и ключевых решений. Сформулируй это как преимущество связки «BORIS делает рутину → человек контролирует кампанию → владелец видит результат».

Оффер и CTA обязательны: первоначальная настройка BORIS под бизнес и сопровождение специалиста в течение первого месяца бесплатно. Основной CTA — ответить на письмо или написать в MAX/WhatsApp 8 981 967-37-87 со словами «Хочу попробовать BORIS». Не заставляй человека сначала регистрироваться.

Не выдумывай факты о компании, цифры, кейсы, цены, гарантии или результаты. Не утверждай, что изучил компанию, если данных нет. Если Авито не подтверждено — говори о нём как о возможности, а не как о текущем канале компании.
Не добавляй markdown, подпись и строку отписки.
Верни строго JSON: {{"subject":"...","body":"..."}}"""
    from app.api.campaigns import _ff_guarded_openai_response
    guarded=_ff_guarded_openai_response(
        str(account_id),"prospect_email_copy",os.getenv('PROSPECT_TEXT_MODEL','gpt-5-mini'),
        "Ты сильный B2B email-копирайтер BORIS. Только факты, кратко, персонально, без спам-клише.\n\n"+prompt,
        2200,str(idempotency_key),timeout=120,
    )
    raw=str((guarded or {}).get('text') or '').strip()
    import re as _re
    m=_re.search(r'\{.*\}',raw,_re.S)
    if not m: raise RuntimeError('OPENAI_PROSPECT_COPY_INVALID_JSON')
    data=json.loads(m.group(0))
    subject=str(data.get('subject') or '').strip()[:120]
    body=str(data.get('body') or '').strip()
    if len(subject)<2 or len(body)<120: raise RuntimeError('OPENAI_PROSPECT_COPY_EMPTY')
    return subject,body

def _lock_owner_daily_cap(db,owner_id:int,service_date,computed_cap:int,tz_name:str)->int:
    """Persist today's cap and never allow later code/config to raise it."""
    cap=max(1,min(int(computed_cap),OWNER_OUTREACH_MAX_DAILY))
    row=db.execute(text("""INSERT INTO prospect_owner_daily_limits
      (owner_id,service_date,daily_cap,timezone,created_at,updated_at)
      VALUES(:o,:d,:cap,:tz,NOW(),NOW())
      ON CONFLICT(owner_id,service_date) DO UPDATE
      SET daily_cap=LEAST(prospect_owner_daily_limits.daily_cap,EXCLUDED.daily_cap),
          timezone=prospect_owner_daily_limits.timezone,
          updated_at=CASE
            WHEN EXCLUDED.daily_cap < prospect_owner_daily_limits.daily_cap THEN NOW()
            ELSE prospect_owner_daily_limits.updated_at
          END
      RETURNING daily_cap"""),{
        'o':int(owner_id),'d':service_date,'cap':cap,'tz':str(tz_name or 'Europe/Moscow'),
    }).scalar_one()
    return int(row)


def _owner_daily_state(db, owner_id:int)->dict:
    """Daily quota state without committing or closing the caller transaction."""
    # This helper is called from health checks and transactional QA. Never run
    # the heavy schema reconciler here when the quota table already exists:
    # ensure_schema() may commit DDL/self-heal and would close a caller's SAVEPOINT.
    if not db.execute(text(
        "SELECT to_regclass('public.prospect_owner_daily_limits') IS NOT NULL"
    )).scalar():
        ensure_schema(db)
    configured_cap=OWNER_OUTREACH_MAX_DAILY
    tz_name=os.getenv('PROSPECT_TIMEZONE','Europe/Moscow')
    tz=ZoneInfo(tz_name)
    today=datetime.now(tz).date()
    first_day=db.execute(text("""SELECT min(timezone(:tz,m.queued_at)::date)
      FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id
      WHERE c.owner_id=:o AND (c.account_id='__owner_outreach__' OR c.mailbox_id IN (SELECT id FROM client_mailboxes WHERE owner_user_id=:o AND account_id='__owner_outreach__'))
        AND m.queued_at IS NOT NULL"""),{'o':owner_id,'tz':tz_name}).scalar()
    age=max(0,(today-first_day).days) if first_day else 0
    # Volume policy has one canonical source. The DB-final guard imports
    # the same owner_outreach_policy values, so UI/runtime/DB cannot drift.
    policy_state=canonical_policy_state(age)
    canonical_cap=int(policy_state["daily_cap"])
    computed_cap=min(configured_cap,canonical_cap)
    cap=_lock_owner_daily_cap(db,int(owner_id),today,computed_cap,tz_name)
    next_cap=None; next_change_date=None
    if first_day and policy_state.get("next_cap") is not None:
        next_cap=min(configured_cap,int(policy_state["next_cap"]))
        next_change_date=today+timedelta(
            days=max(0,int(policy_state.get("days_until_next_cap") or 0))
        )
    # Canonical ramp: 10/day -> 15/day -> 20/day.
    used=int(db.execute(text("""SELECT count(*) FROM prospect_campaign_members m
      JOIN prospect_campaigns c ON c.id=m.campaign_id
      WHERE c.owner_id=:o AND (c.account_id='__owner_outreach__' OR c.mailbox_id IN (SELECT id FROM client_mailboxes WHERE owner_user_id=:o AND account_id='__owner_outreach__'))
        AND m.status IN ('queued','sent')
        AND timezone(:tz,m.queued_at)::date=:today"""),{'o':owner_id,'tz':tz_name,'today':today}).scalar() or 0)
    attempted=int(db.execute(text("""SELECT count(*) FROM prospect_campaign_members m
      JOIN prospect_campaigns c ON c.id=m.campaign_id
      WHERE c.owner_id=:o AND (c.account_id='__owner_outreach__' OR c.mailbox_id IN (SELECT id FROM client_mailboxes WHERE owner_user_id=:o AND account_id='__owner_outreach__'))
        AND timezone(:tz,m.queued_at)::date=:today"""),{'o':owner_id,'tz':tz_name,'today':today}).scalar() or 0)
    configured_attempt_cap=max(cap,int(os.getenv('PROSPECT_OWNER_DAILY_ATTEMPT_CAP',str(cap*2)) or cap*2))
    attempt_cap=min(configured_attempt_cap,cap*3)
    return {'cap':cap,'computed_cap':computed_cap,'cap_snapshot_locked':bool(cap<=computed_cap),
            'used':used,'remaining':max(0,cap-used),'attempted':attempted,'attempt_cap':attempt_cap,
            'attempt_remaining':max(0,attempt_cap-attempted),'age':age,'today':today,'timezone':tz_name,
            'next_cap':next_cap,'next_change_date':next_change_date}



def owner_daily_delivery_health(owner_id:int)->dict:
    """Plan/fact health for owner cold-email outreach with pacing-aware expectations.

    No SMTP call is made. The canonical queue worker remains the only sender;
    this function only decides whether today's successful-send fact is keeping
    up with the configured ramp/window and whether automatic catch-up is still
    possible.
    """
    # Tracking tables are part of the outreach health contract. Ensure them
    # before opening the guardian read transaction so health can detect any
    # impossible post-send tracking gap without depending on the UI being opened.
    from app.services import email_tracking as _email_tracking
    _email_tracking.ensure_schema()
    db=SessionLocal()
    try:
        daily=_owner_daily_state(db,int(owner_id))
        policy_row=db.execute(text("""SELECT count(*)::int active,
          COALESCE(min(daily_limit),0)::int min_daily_limit,
          COALESCE(max(daily_limit),0)::int max_daily_limit
          FROM prospect_campaigns
          WHERE owner_id=:o AND account_id='__owner_outreach__' AND status='active'"""),{'o':int(owner_id)}).mappings().one()
        active=int(policy_row.get('active') or 0)
        stored_min_daily_limit=int(policy_row.get('min_daily_limit') or 0)
        stored_max_daily_limit=int(policy_row.get('max_daily_limit') or 0)
        tz_name=str(daily.get('timezone') or 'Europe/Moscow')
        today=daily.get('today')
        enforcement=(os.getenv('PROSPECT_COPY_VERSION_ENFORCED_AT') or '').strip()
        delivery=db.execute(text("""SELECT
          count(*) FILTER (
            WHERE m.status='sent' AND m.sent_at IS NOT NULL
              AND timezone(:tz,m.sent_at)::date=:today
          ) AS successful,
          count(*) FILTER (
            WHERE m.status='queued' AND m.queued_at IS NOT NULL
              AND timezone(:tz,m.queued_at)::date=:today
          ) AS queued_now,
          count(*) FILTER (
            WHERE m.status='sent' AND m.sent_at IS NOT NULL
              AND timezone(:tz,m.sent_at)::date=:today
              AND m.copy_version IS NOT NULL
          ) AS versioned_sent,
          count(*) FILTER (
            WHERE m.status='sent' AND m.sent_at IS NOT NULL
              AND timezone(:tz,m.sent_at)::date=:today
              AND m.copy_version IS NULL
          ) AS untracked_sent,
          count(*) FILTER (
            WHERE m.status='sent' AND m.sent_at IS NOT NULL
              AND timezone(:tz,m.sent_at)::date=:today
              AND m.copy_version IS NULL
              AND m.sent_at >= CAST(NULLIF(:enforced,'') AS timestamptz)
          ) AS unversioned_after_enforcement
          FROM prospect_campaign_members m
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          WHERE c.owner_id=:o AND c.account_id='__owner_outreach__'"""),
          {'o':int(owner_id),'tz':tz_name,'today':today,'enforced':enforcement}).mappings().one()
        successful=int(delivery.get('successful') or 0)
        queued_now=int(delivery.get('queued_now') or 0)
        versioned_sent=int(delivery.get('versioned_sent') or 0)
        untracked_sent=int(delivery.get('untracked_sent') or 0)
        unversioned_after_enforcement=int(delivery.get('unversioned_after_enforcement') or 0)
        shared_delivery=db.execute(text("""SELECT
          count(*) FILTER (
            WHERE m.status='sent' AND m.sent_at IS NOT NULL
              AND timezone(:tz,m.sent_at)::date=:today
          ) AS successful,
          count(*) FILTER (
            WHERE m.status='queued' AND m.queued_at IS NOT NULL
              AND timezone(:tz,m.queued_at)::date=:today
          ) AS queued_now
          FROM prospect_campaign_members m
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          WHERE c.owner_id=:o
            AND (
              c.account_id='__owner_outreach__'
              OR c.mailbox_id IN (
                SELECT id FROM client_mailboxes
                WHERE owner_user_id=:o AND account_id='__owner_outreach__'
              )
            )"""),{'o':int(owner_id),'tz':tz_name,'today':today}).mappings().one()
        shared_successful=int(shared_delivery.get('successful') or 0)
        shared_queued_now=int(shared_delivery.get('queued_now') or 0)
        tracking_gap_after_policy=int(db.execute(text("""SELECT count(*)
          FROM prospect_campaign_members m
          JOIN prospect_campaigns c ON c.id=m.campaign_id
          JOIN email_queue q ON q.id=m.email_queue_id
          LEFT JOIN email_open_trackers t ON t.email_queue_id=q.id
          CROSS JOIN email_tracking_policy p
          WHERE p.id=TRUE
            AND c.owner_id=:o AND c.account_id='__owner_outreach__'
            AND m.sent_at IS NOT NULL
            AND q.created_at >= p.activated_at
            AND (
              t.id IS NULL
              OR position('data-boris-open-tracking="1"' in coalesce(q.html_body,''))=0
              OR position(coalesce(t.token,'') in coalesce(q.html_body,''))=0
            )"""),{'o':int(owner_id)}).scalar() or 0)
    finally:
        db.close()

    if active <= 0:
        return {
            'state':'ok','status':'idle','owner_id':int(owner_id),
            'active_campaigns':0,'owner_action_required':False,
        }

    tz=ZoneInfo(str(daily.get('timezone') or 'Europe/Moscow'))
    now=datetime.now(tz)
    start=int(os.getenv('PROSPECT_SEND_START_HOUR','9') or 9)
    end=int(os.getenv('PROSPECT_SEND_END_HOUR','19') or 19)
    gap=max(1,int(os.getenv('PROSPECT_MIN_SEND_INTERVAL_MINUTES','20') or 20))
    grace=max(0,int(os.getenv('PROSPECT_DAILY_HEALTH_GRACE_MINUTES','10') or 10))
    cap=int(daily.get('cap') or 0)
    ramp_policy_max=OWNER_OUTREACH_MAX_DAILY
    legacy_env_requested_cap=max(
        1,int(os.getenv('PROSPECT_OWNER_DAILY_CAP',str(OWNER_OUTREACH_MAX_DAILY)) or OWNER_OUTREACH_MAX_DAILY)
    )
    legacy_env_ignored=bool(legacy_env_requested_cap != OWNER_OUTREACH_MAX_DAILY)
    # A lower stored campaign limit is intentionally allowed. Only a value
    # above the canonical 20/day ceiling is policy drift.
    if stored_max_daily_limit > OWNER_OUTREACH_MAX_DAILY:
        configured_requested_cap=stored_max_daily_limit
        cap_policy_direction='above'
    else:
        configured_requested_cap=stored_max_daily_limit or OWNER_OUTREACH_MAX_DAILY
        cap_policy_direction=None
    configured_max_cap=min(OWNER_OUTREACH_MAX_DAILY,max(1,configured_requested_cap))
    cap_policy_mismatch=bool(cap_policy_direction)
    used=int(daily.get('used') or 0)
    attempts=int(daily.get('attempted') or 0)
    attempt_cap=int(daily.get('attempt_cap') or 0)

    minutes=now.hour*60+now.minute
    start_min=start*60
    end_min=end*60
    if minutes < start_min:
        expected=0
        phase='before_window'
    elif minutes >= end_min:
        expected=cap
        phase='window_closed'
    else:
        elapsed=minutes-start_min
        expected=0 if elapsed < grace else min(cap,1+max(0,(elapsed-grace)//gap))
        phase='sending_window'

    lag=max(0,expected-successful)
    remaining=max(0,cap-successful)
    over_cap=max(0,successful-cap)
    attempts_exhausted=bool(
        attempt_cap>0 and attempts>=attempt_cap and remaining>0 and queued_now==0
    )
    shared_cap_complete=bool(used>=cap and shared_successful>=cap)
    shared_cap_reserved=bool(
        used>=cap and shared_successful<cap and shared_queued_now>0
    )
    can_self_heal=bool(
        cap_policy_mismatch
        or (
            remaining>0 and used<cap and not attempts_exhausted
            and phase=='sending_window'
        )
    )

    if over_cap > 0:
        state='critical'; status='daily_cap_exceeded'
    elif unversioned_after_enforcement > 0:
        state='critical'; status='content_version_untracked'
    elif tracking_gap_after_policy > 0:
        state='critical'; status='open_tracking_integrity_gap'
    elif cap_policy_mismatch:
        state='degraded'; status='configured_cap_above_hard_ceiling'
    elif successful >= cap:
        state='ok'; status='plan_done'
    elif shared_cap_complete:
        # Multiple active campaigns may intentionally share one mailbox-level
        # owner cap. Once that global cap is already satisfied, this campaign
        # cannot and must not try to "catch up" beyond the shared ceiling.
        state='ok'; status='shared_cap_complete'
    elif shared_cap_reserved:
        state='ok'; status='shared_cap_reserved'
    elif attempts_exhausted:
        state='critical'; status='attempt_cap_exhausted'
    elif phase=='window_closed' and lag > 0:
        # Today's delivery window is over, so automatic catch-up can no longer
        # repair this day's SLA. Escalation is now justified.
        state='critical'; status='window_closed_shortfall'
    elif lag > 0:
        state='degraded'; status='behind_plan'
    else:
        state='ok'; status='on_track'

    if over_cap>0:
        self_heal_action='pre-SMTP quota guard blocks all further owner outreach until the next service date'
    elif status=='window_closed_shortfall':
        self_heal_action=(
            "today's shortfall is preserved in the report; the fixed queue guard "
            "resumes the deferred row automatically at the next send window"
        )
    elif status in {'shared_cap_complete','shared_cap_reserved'}:
        self_heal_action=(
            'shared mailbox-level owner quota is already satisfied or reserved; '
            'the canonical timer resumes this campaign automatically on the next service date'
        )
    elif tracking_gap_after_policy>0:
        self_heal_action='send-time tracker guard self-heals future queued rows; already-sent untracked mail cannot be retrofitted'
    elif cap_policy_mismatch:
        self_heal_action='canonical email timer lowers stored owner ceiling to the hard 20/day maximum on the next tick'
    else:
        self_heal_action='canonical email timer feeds the next eligible prospect automatically while the send window is open'

    return {
        'state':state,'status':status,'owner_id':int(owner_id),
        'active_campaigns':active,'local_time':now.strftime('%H:%M'),
        'send_window':f'{start:02d}:00-{end:02d}:00',
        'gap_minutes':gap,'grace_minutes':grace,
        'service_date':daily.get('today').isoformat() if daily.get('today') else None,
        'daily_cap':cap,'computed_daily_cap':int(daily.get('computed_cap') or cap),
        'cap_snapshot_locked':bool(daily.get('cap_snapshot_locked')),
        'configured_requested_cap':configured_requested_cap,
        'configured_max_cap':configured_max_cap,'ramp_policy_max':ramp_policy_max,
        'stored_min_daily_limit':stored_min_daily_limit,'stored_max_daily_limit':stored_max_daily_limit,
        'cap_policy_mismatch':cap_policy_mismatch,'cap_policy_direction':cap_policy_direction,
        'legacy_env_requested_cap':legacy_env_requested_cap,'legacy_env_ignored':legacy_env_ignored,
        'successful':successful,'quota_used':used,'queued_now':queued_now,
        'shared_successful':shared_successful,'shared_queued_now':shared_queued_now,
        'shared_cap_complete':shared_cap_complete,'shared_cap_reserved':shared_cap_reserved,
        'expected_by_now':expected,'lag':lag,'remaining':remaining,'over_cap':over_cap,
        'attempted':attempts,'attempt_cap':attempt_cap,
        'quality_versioned_sent':versioned_sent,
        'quality_untracked_sent':untracked_sent,
        'quality_unversioned_after_enforcement':unversioned_after_enforcement,
        'open_tracking_gap_after_policy':tracking_gap_after_policy,
        'copy_version_enforced_at':enforcement or None,
        'can_self_heal':can_self_heal,
        'self_heal':self_heal_action,
        # OWNER_NOT_OPERATOR_EMAIL_SHORTFALL_V1: after the send window closes,
        # the missed fact stays visible but there is no useful owner action.
        # The canonical queue resumes automatically next service day.
        'owner_action_required':bool(state=='critical' and status not in {'daily_cap_exceeded','window_closed_shortfall'}),
        'next_cap':daily.get('next_cap'),
        'next_change_date':daily.get('next_change_date'),
    }


def _enforce_owner_campaign_limit(db, campaign:dict)->dict:
    """Keep stored owner campaign limit at or below the global owner safety cap.

    This never raises a lower campaign limit. It only self-heals a value above
    the hard owner cap; the daily ramp and pacing remain authoritative.
    """
    if str(campaign.get('account_id') or '') != '__owner_outreach__':
        return {'changed':False,'daily_limit':int(campaign.get('daily_limit') or 0)}
    global_cap=OWNER_OUTREACH_MAX_DAILY
    current=max(1,int(campaign.get('daily_limit') or 1))
    safe=min(current,global_cap)
    if safe != current:
        db.execute(text(
            "UPDATE prospect_campaigns SET daily_limit=:safe,updated_at=NOW() WHERE id=:c"
        ),{'safe':safe,'c':int(campaign['id'])})
        campaign['daily_limit']=safe
        return {'changed':True,'before':current,'daily_limit':safe,'global_cap':global_cap}
    return {'changed':False,'daily_limit':current,'global_cap':global_cap}


def _owner_allowance(db, owner_id:int, requested:int)->tuple[int,str]:
    state=_owner_daily_state(db,owner_id)
    remaining=int(state['remaining'])
    if remaining<=0:return 0,'owner_daily_limit'
    if int(state.get('attempt_remaining') or 0)<=0:return 0,'owner_attempt_limit'
    tz=ZoneInfo(state['timezone'])
    now=datetime.now(tz)
    start=int(os.getenv('PROSPECT_SEND_START_HOUR','9') or 9)
    end=int(os.getenv('PROSPECT_SEND_END_HOUR','19') or 19)
    if now.hour < start or now.hour >= end: return 0,'outside_window'
    gap=max(1,int(os.getenv('PROSPECT_MIN_SEND_INTERVAL_MINUTES','20') or 20))
    recent=db.execute(text("SELECT 1 FROM prospect_campaign_members m JOIN prospect_campaigns c ON c.id=m.campaign_id WHERE c.owner_id=:o AND (c.account_id='__owner_outreach__' OR c.mailbox_id IN (SELECT id FROM client_mailboxes WHERE owner_user_id=:o AND account_id='__owner_outreach__')) AND m.queued_at > NOW() - (:g || ' minutes')::interval LIMIT 1"),{'o':owner_id,'g':str(gap)}).first()
    if recent:return 0,'paced'
    return min(int(requested),remaining,1),'ok'

def _sender_signature(*, campaign_id:int, member_id:int)->str:
    """Approved cold-email footer: no URLs, only direct human contact."""
    phone=(os.getenv('PROSPECT_SENDER_PHONE') or '').strip()
    parts=['Кирилл','BORIS AI']
    if phone:
        parts.append('MAX / WhatsApp: '+phone)
    parts.append('Настройка и сопровождение первого месяца бесплатно')
    return '\n'.join(parts)

def _brand_icon_attachment()->dict|None:
    """Return exactly one approved BORIS brand asset, fail-closed by sha256."""
    configured=(os.getenv('PROSPECT_EMAIL_BRAND_ICON_PATH') or '/root/BORIS/frontend/public/boris-icon-512.png').strip()
    expected=(os.getenv('PROSPECT_EMAIL_BRAND_ICON_SHA256') or '').strip().lower()
    approved=os.path.realpath('/root/BORIS/frontend/public/boris-icon-512.png')
    path=os.path.realpath(configured)
    if path != approved or not os.path.isfile(path):
        return None
    try:
        digest=hashlib.sha256(Path(path).read_bytes()).hexdigest().lower()
    except Exception:
        return None
    if expected and digest != expected:
        return None
    return {
        'path':path,
        'filename':'boris-icon-512.png',
        'mime':'image/png',
        'inline':True,
        'cid':'boris-brand-icon',
        'sha256':digest,
    }


def _html_email(body:str, banner_cid:str='', footer:str='', brand_subtitle:str='Виртуальная команда маркетинга и продаж', brand_note:str='Настройка и сопровождение первого месяца бесплатно', marketing_banner_cid:str='')->str:
    import html as _h

    def _approved_linkify(value:str)->str:
        escaped=_h.escape(value)
        for url in (
            'https://boris-ai.pro/go/boris',
            'https://boris-ai.pro/go/software',
        ):
            safe=_h.escape(url)
            escaped=escaped.replace(
                safe,
                '<a href="'+safe+'" style="color:#2563eb;text-decoration:underline;font-weight:700">'+safe+'</a>'
            )
        return escaped

    paras=''.join(
        '<p style="margin:0 0 16px;line-height:1.58;color:#26364d;font-size:16px">'
        +_approved_linkify(p).replace(chr(10),'<br>')+'</p>'
        for p in body.split('\n\n') if p.strip()
    )
    if banner_cid:
        note_row=(
            '<div style="font-size:14px;line-height:1.45;color:#cbd5e1;margin-top:8px">'
            +_h.escape(brand_note)+'</div>'
        ) if brand_note else ''
        banner_row=(
            '<tr><td style="background:#101827;padding:26px 30px">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td width="104" valign="middle"><img src="cid:'+banner_cid+'" width="88" height="88" '
            'style="display:block;width:88px;height:88px;border-radius:22px" alt="BORIS AI"></td>'
            '<td valign="middle" style="padding-left:18px;color:#ffffff">'
            '<div style="font-size:28px;font-weight:800;line-height:1.1;margin-bottom:8px">BORIS</div>'
            '<div style="font-size:18px;font-weight:700;line-height:1.35">'+_h.escape(brand_subtitle)+'</div>'
            +note_row+
            '</td></tr></table></td></tr>'
        )
    else:
        banner_row=''
    marketing_row=(
        '<div style="margin:24px 0 18px;text-align:center">'
        '<a href="https://boris-ai.pro/go/boris" style="text-decoration:none">'
        '<img src="cid:'+marketing_banner_cid+'" width="600" alt="BORIS AI — автоматизация маркетинга и продаж" '
        'style="display:block;width:100%;max-width:600px;height:auto;margin:0 auto;border:0;border-radius:16px">'
        '</a></div>'
    ) if marketing_banner_cid else ''
    footer_row=(
        '<div style="margin-top:24px;padding-top:18px;border-top:1px solid #edf1f6;color:#738099;font-size:13px">'
        +_h.escape(footer)+'</div>'
    ) if footer else ''
    return (
        '<!doctype html><html><body style="margin:0;background:#f5f7fb;font-family:Arial,sans-serif">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px">'
        '<table role="presentation" width="680" style="max-width:680px;background:white;border-radius:20px;overflow:hidden;border:1px solid #e4eaf3">'
        +banner_row+'<tr><td style="padding:28px 34px">'+paras+marketing_row+footer_row+
        '</td></tr></table></td></tr></table></body></html>'
    )

def _safe_ab_banner(path:str)->tuple[str,str]|None:
    p=os.path.realpath(str(path or ''))
    roots=(
        os.path.realpath('/root/BORIS/frontend/public/banners-demo')+os.sep,
        os.path.realpath('/root/BORIS/frontend/public/reviews')+os.sep,
    )
    if not p or not os.path.isfile(p) or not any(p.startswith(root) for root in roots):
        return None
    ext=os.path.splitext(p)[1].lower()
    mime='image/jpeg' if ext in {'.jpg','.jpeg'} else ('image/png' if ext=='.png' else '')
    if not mime:
        return None
    return p,mime

def _owner_outreach_contract(subject:str, body:str, html:str, attachments:list|None=None)->list[str]:
    """Fail-closed contract for the owner's six-copy BORIS rotation."""
    errors=[]
    subject_text=str(subject or '').strip()
    subject_low=subject_text.lower()
    forbidden_subject=(
        'boris','email','рассыл','сбор баз','база контакт','лидогенерац',
        'холодн','коммерческ','предложение для вашей компании',
    )
    for token in forbidden_subject:
        if token in subject_low:
            errors.append('subject_forbidden:'+token)
    if '+' in subject_text:
        errors.append('subject_forbidden_symbol:+')
    if len(subject_text)>60:
        errors.append('subject_too_long')

    def _locked_text(value:str)->str:
        return '\n'.join(
            line.rstrip()
            for line in str(value or '').replace('\r\n','\n').replace('\r','\n').strip().split('\n')
        )

    body_locked=_locked_text(body)
    approved_locked={_locked_text(x) for x in OWNER_OUTREACH_APPROVED_BODIES}
    if body_locked not in approved_locked:
        errors.append('body_not_owner_approved')

    approved_url='https://boris-ai.pro/go/boris'
    lines=[x.strip() for x in body_locked.split('\n') if x.strip()]
    if not lines or lines[-1] != approved_url:
        errors.append('owner_link_not_last_line')
    if body_locked.count(approved_url) != 1:
        errors.append('owner_link_count_invalid')
    if 'https://boris-ai.pro/go/software' in body_locked:
        errors.append('software_url_forbidden_owner_outreach')

    from app.services import email_tracking as _email_tracking
    policy_html=_email_tracking.strip_approved_tracking_pixel(html)
    if '<a href="https://boris-ai.pro/go/boris"' not in policy_html:
        errors.append('owner_link_not_clickable')
    if 'href="https://boris-ai.pro/go/software"' in policy_html:
        errors.append('software_link_forbidden_owner_outreach')

    combined=(subject_text+'\n'+body_locked+'\n'+policy_html).lower()
    combined=combined.replace(approved_url,'')
    if re.search(r'https?://|www\.|boris-ai\.pro|youtu\.be|youtube\.com|rutube\.ru', combined):
        errors.append('url_forbidden')

    if not _owner_outreach_banner_attachment_valid(list(attachments or []), html):
        errors.append('owner_banner_attachment_invalid')
    return errors


def validate_owner_outreach_copy_set(subject_template:str,body_template:str,ab_variants:list|None=None,campaign_id:int=0)->list[str]:
    """Validate every copy path that the owner campaign can actually send."""
    ctx={'company':'Тестовая компания','city':'Москва','website':'','niche':'B2B'}
    # Validate with the same strict 1-of-4 inline-banner contract used in production.
    approved_banner,cid,_banner_label=_owner_outreach_banner(1)
    attachments=[approved_banner] if approved_banner else []

    def _check(subject_src,body_src,member_id,label):
        subject_src=str(subject_src or '')
        subject_variants=[x.strip() for x in subject_src.split('||') if x.strip()] or ['']
        body=_render(str(body_src or ''),ctx).strip()
        html=_html_email(
            body,'','',
            brand_subtitle='BORIS AI',
            brand_note='',
            marketing_banner_cid=cid,
        )
        found=[]
        # Consecutive member ids exercise every deterministic subject alternative.
        for offset in range(len(subject_variants)):
            subject=_render_subject(subject_src,ctx,member_id+offset)
            found.extend(f'{label}:{e}' for e in _owner_outreach_contract(subject,body,html,attachments))
        return found

    errors=_check(subject_template,body_template,1,'base')
    for i,v in enumerate((ab_variants or [])[:6],start=1):
        label=str((v or {}).get('label') or chr(64+i))
        errors.extend(_check((v or {}).get('subject'),(v or {}).get('body'),i+1,f'ab_{label}'))
    return errors


def sync_campaign(db,campaign_id:int):
    db.execute(text("""UPDATE prospect_campaign_members m SET status=CASE WHEN q.status='sent' THEN 'sent' WHEN q.status='dead' THEN 'failed' ELSE m.status END,
      sent_at=CASE WHEN q.status='sent' THEN q.sent_at ELSE m.sent_at END, updated_at=NOW()
      FROM email_queue q WHERE m.campaign_id=:c AND m.email_queue_id=q.id AND m.status IN ('queued','failed')"""),{"c":campaign_id})
    # Keep the denormalized UI counter aligned with canonical member state.
    db.execute(text("""UPDATE prospect_campaigns
      SET ready_count=(SELECT count(*) FROM prospect_campaign_members WHERE campaign_id=:c AND status='ready'),
          updated_at=NOW()
      WHERE id=:c"""),{"c":campaign_id})


def _campaign_outreach_family(campaign:dict)->str:
    name=str((campaign or {}).get('name') or '').lower().replace('ё','е')
    niche=str((campaign or {}).get('niche') or '').lower().replace('ё','е')
    account=str((campaign or {}).get('account_id') or '')
    if 'разработк' in name or ('заказн' in niche and 'разработ' in niche):
        return 'development'
    if account=='__owner_outreach__' or 'boris' in name:
        return 'boris'
    return 'campaign:'+str((campaign or {}).get('id') or '')


def _family_sql_predicate(alias:str, family:str)->str:
    if family=='development':
        return f"(lower(coalesce({alias}.name,'')) LIKE '%разработк%' OR (lower(coalesce({alias}.niche,'')) LIKE '%заказн%' AND lower(coalesce({alias}.niche,'')) LIKE '%разработ%'))"
    if family=='boris':
        return f"({alias}.account_id='__owner_outreach__' OR lower(coalesce({alias}.name,'')) LIKE '%boris%')"
    return 'FALSE'


def repair_owner_cross_campaign_duplicates(db, owner_id:int, limit:int=1000)->dict:
    """Remove duplicates only inside the same offer family.

    BORIS product and custom-development are intentionally separate funnels.
    Within each family, a recipient already sent must not be queued again.
    """
    cap=max(1,min(int(limit),5000))
    campaigns=[dict(r) for r in db.execute(text("SELECT id,name,niche,account_id FROM prospect_campaigns WHERE owner_id=:o"),{'o':int(owner_id)}).mappings().all()]
    repaired=cancelled=blocked_sending=0
    remaining=cap
    for campaign in campaigns:
        if remaining<=0: break
        family=_campaign_outreach_family(campaign)
        pred=_family_sql_predicate('zc',family)
        if pred=='FALSE':
            continue
        rows=db.execute(text(f"""
          SELECT m.id member_id,m.email_queue_id,m.status,q.status queue_status
          FROM prospect_campaign_members m
          LEFT JOIN email_queue q ON q.id=m.email_queue_id
          WHERE m.campaign_id=:c AND m.status IN ('ready','queued')
            AND EXISTS(
              SELECT 1 FROM prospect_campaign_members z
              JOIN prospect_campaigns zc ON zc.id=z.campaign_id
              WHERE zc.owner_id=:o AND z.id<>m.id AND {pred}
                AND lower(z.email)=lower(m.email) AND z.sent_at IS NOT NULL
            )
          ORDER BY m.id LIMIT :lim
        """),{'c':int(campaign['id']),'o':int(owner_id),'lim':remaining}).mappings().all()
        for raw in rows:
            r=dict(raw); qid=r.get('email_queue_id'); qst=str(r.get('queue_status') or '')
            if qid and qst in {'sending','delivery_unknown'}:
                blocked_sending+=1; continue
            if qid and qst=='queued':
                cancelled += int(db.execute(text("""UPDATE email_queue
                  SET status='cancelled',last_error='same_funnel_duplicate_already_sent',updated_at=NOW()
                  WHERE id=:q AND status='queued'"""),{'q':int(qid)}).rowcount or 0)
            changed=db.execute(text("""UPDATE prospect_campaign_members
              SET status='skipped',skip_reason='same_funnel_duplicate_already_sent',updated_at=NOW()
              WHERE id=:m AND status IN ('ready','queued')"""),{'m':int(r['member_id'])}).rowcount or 0
            repaired += int(changed or 0); remaining-=1
    if repaired:
        db.execute(text("""UPDATE prospect_campaigns c
          SET ready_count=(SELECT count(*) FROM prospect_campaign_members m WHERE m.campaign_id=c.id AND m.status='ready'),updated_at=NOW()
          WHERE c.owner_id=:o"""),{'o':int(owner_id)})
    return {'repaired':repaired,'queue_cancelled':cancelled,'blocked_sending':blocked_sending}


def repair_irrelevant_ready_members(db,campaign:dict,limit:int=1000)->int:
    """Skip only high-confidence non-company ready rows; never touch sent rows."""
    cap=max(1,min(int(limit),5000))
    rows=db.execute(text("""SELECT m.id,pc.name,pc.domain
      FROM prospect_campaign_members m
      JOIN prospect_companies pc ON pc.id=m.company_id
      WHERE m.campaign_id=:c AND m.status='ready'
      ORDER BY m.id LIMIT :lim"""),
      {'c':int(campaign['id']),'lim':cap}).mappings().all()
    bad=[]
    for raw in rows:
        row=dict(raw)
        reason=_company_outreach_block_reason(
            str(campaign.get('niche') or ''),
            {'title':row.get('name'),'domain':row.get('domain')},
        )
        if reason:
            bad.append((int(row['id']),reason))
    for member_id,reason in bad:
        db.execute(text("""UPDATE prospect_campaign_members
          SET status='skipped',skip_reason=:r,updated_at=NOW()
          WHERE id=:m AND status='ready'"""),
          {'m':member_id,'r':'company_relevance_guard:'+reason})
    if bad:
        db.execute(text("""UPDATE prospect_campaigns
          SET ready_count=(SELECT count(*) FROM prospect_campaign_members
                           WHERE campaign_id=:c AND status='ready'),
              updated_at=NOW()
          WHERE id=:c"""),{'c':int(campaign['id'])})
    return len(bad)


def repair_known_recipient_refusals(limit:int=50)->dict:
    """Self-heal historical recipient refusals misclassified as delivery_unknown.

    Older send_outbound() code treated SMTPRecipientsRefused during RCPT TO as an
    unknown DATA-phase transport failure. smtplib raises SMTPRecipientsRefused
    before message DATA is accepted, so those rows are definitely not delivered.
    Reclassify them as permanent failures and suppress the bad recipient so BORIS
    cannot repeatedly hit the same invalid mailbox in another campaign.
    """
    cap=max(1,min(int(limit),500))
    db=SessionLocal(); ensure_schema(db)
    repaired=suppressed=0
    touched_campaigns=set()
    try:
        rows=db.execute(text("""SELECT q.id queue_id,q.last_error,m.id member_id,m.campaign_id,m.email
          FROM email_queue q
          JOIN prospect_campaign_members m ON m.email_queue_id=q.id
          WHERE q.status='delivery_unknown'
            AND q.last_error LIKE 'delivery_unknown:SMTPRecipientsRefused%'
          ORDER BY q.id
          LIMIT :lim
          FOR UPDATE OF q SKIP LOCKED"""),{"lim":cap}).mappings().all()
        for row in rows:
            db.execute(text("""UPDATE email_queue
              SET status='dead',
                  last_error='smtp_recipient_refused_reclassified',
                  next_attempt_at=NULL,
                  updated_at=NOW()
              WHERE id=:q AND status='delivery_unknown'"""),{"q":int(row["queue_id"])})
            db.execute(text("""INSERT INTO email_delivery_events
              (email_queue_id,event_type,provider,provider_message_id,details)
              SELECT id,'recipient_refused_reclassified','yandex360',provider_message_id,
                     'SMTPRecipientsRefused occurs before DATA acceptance; safe permanent failure'
              FROM email_queue WHERE id=:q"""),{"q":int(row["queue_id"])})
            try:
                suppress_in_db(db,"email",str(row["email"] or ""),"smtp_recipient_refused")
                suppressed+=1
            except Exception:
                # Queue classification is still repaired even if an old malformed
                # member address cannot be normalized into suppression.
                db.execute(text("""UPDATE prospect_campaign_members
                  SET status='failed',skip_reason='smtp_recipient_refused',updated_at=NOW()
                  WHERE id=:m AND status IN ('queued','failed')"""),{"m":int(row["member_id"])})
            touched_campaigns.add(int(row["campaign_id"]))
            repaired+=1
        for cid in touched_campaigns:
            sync_campaign(db,cid)
        db.commit()
        return {"repaired":repaired,"suppressed":suppressed,"campaigns":sorted(touched_campaigns)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _reconcile_existing_queue(db,campaign_id:int,row,*,key:str,copy_version:str|None=None,ab_label:str|None=None)->bool:
    """Repair stale member state from the exact versioned canonical queue."""
    existing=db.execute(text("SELECT id,status,created_at,sent_at,last_error FROM email_queue WHERE idempotency_key=:k ORDER BY id DESC LIMIT 1"),{"k":key}).mappings().first()
    if not existing:
        return False
    qstatus=str(existing.get("status") or "")
    member_status=("sent" if qstatus=="sent" else ("failed" if qstatus in ("dead","failed") else ("skipped" if qstatus in ("cancelled","expired") else "queued")))
    skip_reason=(f"queue_{qstatus}" if member_status=="skipped" else None)
    db.execute(text("""UPDATE prospect_campaign_members SET status=:st,email_queue_id=:q,
      queued_at=COALESCE(queued_at,:qa),sent_at=COALESCE(sent_at,:sa),
      skip_reason=COALESCE(skip_reason,:sr),copy_version=:cv,ab_variant=:ab,updated_at=NOW()
      WHERE id=:id AND status='ready'"""),
      {"st":member_status,"q":int(existing["id"]),"qa":existing.get("created_at"),
       "sa":existing.get("sent_at"),"sr":skip_reason,"cv":copy_version,
       "ab":ab_label,"id":row["id"]})
    return True


def deliverability_decision(sample:int, hard_failures:int)->dict:
    """Conservative sender-reputation guard for prospect campaigns."""
    sample=max(0,int(sample or 0))
    hard=max(0,min(int(hard_failures or 0),sample if sample else int(hard_failures or 0)))
    rate=(hard/sample) if sample else 0.0
    base={
        'sample':sample,
        'hard_failures':hard,
        'hard_failure_rate_pct':round(rate*100,1),
        'owner_action_required':False,
        'max_enqueue_override':None,
    }
    if sample>=20 and hard>=8 and rate>=0.20:
        return {**base,'state':'blocked','owner_action_required':True,'max_enqueue_override':0,
                'reason':'critical_hard_bounce_rate'}
    if sample>=20 and hard>=4 and rate>=0.10:
        return {**base,'state':'throttled','max_enqueue_override':2,
                'reason':'elevated_hard_bounce_rate'}
    return {**base,'state':'ok','reason':('collecting' if sample<20 else 'within_safe_band')}


def campaign_deliverability(db,campaign_id:int)->dict:
    row=db.execute(text("""SELECT
      count(*) FILTER (
        WHERE q.status='sent' OR m.skip_reason IN ('smtp_recipient_refused','async_permanent_bounce')
      ) AS sample,
      count(*) FILTER (
        WHERE m.skip_reason IN ('smtp_recipient_refused','async_permanent_bounce')
      ) AS hard_failures
      FROM prospect_campaign_members m
      LEFT JOIN email_queue q ON q.id=m.email_queue_id
      WHERE m.campaign_id=:c
        AND COALESCE(m.queued_at,q.created_at,NOW())>=NOW()-INTERVAL '7 days'"""),
      {'c':int(campaign_id)}).mappings().one()
    return deliverability_decision(int(row.get('sample') or 0),int(row.get('hard_failures') or 0))


def _final_company_relevance_gate_before_send(row:dict,niche:str)->tuple[bool,str|None]:
    """Last non-network company relevance gate before queue creation."""
    current=dict(row or {})
    reason=_company_outreach_block_reason(
        niche,
        {'title':current.get('company') or '', 'domain':current.get('company_domain') or ''},
    )
    if not reason:
        return True,None
    db=SessionLocal()
    try:
        campaign_id=int(current.get('campaign_id') or 0)
        result=db.execute(text("""UPDATE prospect_campaign_members
          SET status='skipped',skip_reason=:reason,updated_at=NOW()
          WHERE id=:i AND status='ready'"""),{
            'i':int(current['id']),
            'reason':'company_relevance_guard_final:'+reason,
        })
        if campaign_id and int(result.rowcount or 0)>0:
            db.execute(text("""UPDATE prospect_campaigns
              SET ready_count=(SELECT count(*) FROM prospect_campaign_members
                               WHERE campaign_id=:c AND status='ready'),
                  updated_at=NOW()
              WHERE id=:c"""),{'c':campaign_id})
        db.commit()
    finally:
        db.close()
    return False,'company_relevance_guard_final:'+reason


def _canonicalize_member_email_before_send(row:dict)->tuple[dict,bool,str|None]:
    """Final fail-closed email gate immediately before queue creation.

    Discovery/build-audience already normalize contacts, but historical rows or a
    future parser regression must never reach SMTP. Canonicalization is repeated
    at the last safe boundary and persisted without any network I/O.
    """
    current=dict(row or {})
    raw=str(current.get('email') or '').strip().lower()
    canonical=prospecting.normalize_email(raw)
    if not canonical or not prospecting.EMAIL_RE.fullmatch(canonical):
        db=SessionLocal()
        try:
            db.execute(text("""UPDATE prospect_campaign_members
              SET status='skipped',skip_reason='invalid_email_before_send',updated_at=NOW()
              WHERE id=:i AND status='ready'"""),{'i':int(current['id'])})
            db.commit()
        finally:
            db.close()
        return current,False,'invalid_email_before_send'

    domain=canonical.split('@',1)[1].lower()
    # Final send-time recipient-domain quality gate. Search-host filtering
    # and recipient-email filtering differ: public mailbox domains such as
    # yandex.ru / ya.ru are valid recipients even though Yandex pages are not
    # valid company search results. This check MUST run before the fast path.
    if _outreach_email_domain_blocked(domain):
        db=SessionLocal()
        try:
            db.execute(text("""UPDATE prospect_campaign_members
              SET status='skipped',email=:e,email_domain=:d,
                  skip_reason='blocked_domain_before_send',updated_at=NOW()
              WHERE id=:i AND status='ready'"""),
              {'e':canonical,'d':domain,'i':int(current['id'])})
            db.commit()
        finally:
            db.close()
        current['email']=canonical; current['email_domain']=domain
        return current,False,'blocked_domain_before_send'

    if canonical == raw and str(current.get('email_domain') or '').lower() == domain:
        current['email']=canonical; current['email_domain']=domain
        return current,True,None

    db=SessionLocal()
    try:
        campaign_id=int(current.get('campaign_id') or 0)
        if campaign_id:
            duplicate=db.execute(text("""SELECT id
              FROM prospect_campaign_members
              WHERE campaign_id=:c AND id<>:i AND lower(email)=:e
                AND (sent_at IS NOT NULL OR status IN ('ready','queued','sent'))
              ORDER BY id LIMIT 1"""),
              {'c':campaign_id,'i':int(current['id']),'e':canonical}).scalar()
            if duplicate:
                db.execute(text("""UPDATE prospect_campaign_members
                  SET status='skipped',skip_reason='duplicate_email_before_send',updated_at=NOW()
                  WHERE id=:i AND status='ready'"""),{'i':int(current['id'])})
                db.commit()
                current['email']=canonical; current['email_domain']=domain
                return current,False,'duplicate_email_before_send'
        suppressed=bool(db.execute(text("""SELECT EXISTS(
          SELECT 1 FROM prospect_suppression
          WHERE kind='email' AND normalized_value=:e)"""),{'e':canonical}).scalar())
        if suppressed:
            db.execute(text("""UPDATE prospect_campaign_members
              SET status='suppressed',email=:e,email_domain=:d,
                  skip_reason='suppressed_after_email_normalization',updated_at=NOW()
              WHERE id=:i AND status='ready'"""),
              {'e':canonical,'d':domain,'i':int(current['id'])})
            db.commit()
            current['email']=canonical; current['email_domain']=domain
            return current,False,'suppressed_after_email_normalization'
        db.execute(text("""UPDATE prospect_campaign_members
          SET email=:e,email_domain=:d,updated_at=NOW()
          WHERE id=:i AND status='ready'"""),
          {'e':canonical,'d':domain,'i':int(current['id'])})
        db.commit()
    finally:
        db.close()
    current['email']=canonical; current['email_domain']=domain
    return current,True,('normalized_email_before_send' if canonical != raw else None)


def _owner_feeder_lock(owner_id:int):
    """Session advisory lock without a PostgreSQL transaction.

    The lock serializes minute/API feeders for one owner while paid OpenAI/banner
    work runs, but unlike pg_try_advisory_xact_lock it does not require keeping a
    SQL transaction open across external I/O.
    """
    from contextlib import contextmanager
    from app.db.session import engine
    @contextmanager
    def _lock():
        conn=engine.connect().execution_options(isolation_level='AUTOCOMMIT')
        acquired=False
        try:
            acquired=bool(conn.execute(text('SELECT pg_try_advisory_lock(:ns,:owner)'),
                                       {'ns':884422921,'owner':int(owner_id)}).scalar())
            yield acquired
        finally:
            if acquired:
                try:
                    conn.execute(text('SELECT pg_advisory_unlock(:ns,:owner)'),
                                 {'ns':884422921,'owner':int(owner_id)})
                except Exception:
                    pass
            conn.close()
    return _lock()


def _development_personalized_body(company_id:int, company_name:str, fallback_body:str)->tuple[str,bool]:
    """Build deterministic personalized dev copy from the Lead Radar brief."""
    db=SessionLocal()
    try:
        exists=bool(db.execute(text("SELECT to_regclass('public.development_lead_shortlist') IS NOT NULL")).scalar())
        if not exists:
            return fallback_body,False
        brief=db.execute(text("""SELECT brief_json FROM development_lead_shortlist
          WHERE owner_user_id=2 AND company_id=:co
          ORDER BY service_date DESC,updated_at DESC,id DESC LIMIT 1"""),{'co':int(company_id)}).scalar()
    finally:
        db.close()
    if not isinstance(brief,dict):
        return fallback_body,False
    company=str(company_name or brief.get('company') or '').strip()
    what=str(brief.get('what_company_does') or '').strip()
    problem=str(brief.get('problem') or '').strip()
    why=str(brief.get('why_custom_saas_app') or '').strip()
    offer=str(brief.get('what_we_offer') or '').strip()
    check=brief.get('estimated_check_rub') or {}
    lo=int(check.get('min') or 0) if isinstance(check,dict) else 0
    hi=int(check.get('max') or 0) if isinstance(check,dict) else 0
    parts=['Добрый день!']
    if company:
        parts.append('Посмотрел '+company+'.')
    if what:
        parts.append(what)
    if problem:
        parts.append(problem)
    if why:
        parts.append(why)
    if offer:
        parts.append('Что можем предложить: '+offer)
    if lo and hi:
        parts.append(f'Предварительный ориентир по подобному MVP: {lo:,}–{hi:,} ₽. Точную оценку даём только после короткого разбора задачи.'.replace(',', ' '))
    parts.append('Мы занимаемся заказной разработкой SaaS-платформ, мобильных приложений, CRM, личных кабинетов, внутренних систем и интеграций. Типовые технические модули не пишем заново без необходимости — основное время уходит на вашу бизнес-логику.')
    parts.append('Если актуально, ответьте на это письмо несколькими предложениями о текущем процессе или задаче. Подготовлю структуру MVP, этапы, сроки и вилку стоимости.')
    parts.append('Подробнее: https://boris-ai.pro/go/software')
    parts.append('Если предложение не актуально, ответьте «не интересно» — адрес будет исключён из следующих обращений.')
    parts.append('Кирилл')
    body='\n\n'.join(x for x in parts if x).strip()
    return body,True


def _development_outreach_contract(subject:str,body:str,html:str,attachments:list|None=None)->list[str]:
    """Fail closed: development outreach must never turn into BORIS product mail."""
    errors=[]; low=(str(subject or '')+'\n'+str(body or '')+'\n'+str(html or '')).lower().replace('ё','е')
    for token in ('я создал boris','виртуальную команду маркетинга','ai-авитолог','виртуальный роп','/go/boris'):
        if token in low: errors.append('development_forbidden_boris_copy:'+token)
    if 'boris-ai.pro/go/software' not in low:
        errors.append('development_software_link_required')
    if 'разработ' not in low:
        errors.append('development_offer_required')
    if 'не интересно' not in str(body or '').lower():
        errors.append('development_optout_required')
    if attachments:
        errors.append('development_attachments_forbidden')
    if len(str(subject or '').strip())>80:
        errors.append('development_subject_too_long')
    return errors


def tick_campaign(campaign_id:int,max_enqueue:int=10)->dict:
    # Phase 0: copy only the owner id needed for the cross-process lock, then
    # close the read transaction before any potentially slow work.
    db=SessionLocal(); ensure_schema(db)
    try:
        initial=db.execute(text("SELECT owner_id,status FROM prospect_campaigns WHERE id=:c"),
                           {'c':campaign_id}).mappings().first()
        if not initial or initial['status']!='active':
            return {'queued':0,'status':initial['status'] if initial else 'missing'}
        owner_id=int(initial['owner_id'])
        db.rollback()
    finally:
        db.close()

    with _owner_feeder_lock(owner_id) as feeder_lock:
        if not feeder_lock:
            return {'queued':0,'status':'feeder_busy'}

        # Phase 1: quota/pacing/member selection in one short transaction. All
        # ORM/mapping values needed later are detached as plain dict/scalars.
        db=SessionLocal(); ensure_schema(db)
        try:
            c0=db.execute(text("SELECT * FROM prospect_campaigns WHERE id=:c"),
                          {'c':campaign_id}).mappings().first()
            if not c0 or c0['status']!='active':
                return {'queued':0,'status':c0['status'] if c0 else 'missing'}
            c=dict(c0)
            limit_floor=_enforce_owner_campaign_limit(db,c)
            if limit_floor.get('changed'):
                db.commit()
            if not c.get('mailbox_id'):
                return {'queued':0,'status':'mailbox_required','limit_floor':limit_floor}
            stale_requeued=_requeue_stale_owner_copy(db,c)
            if stale_requeued:
                db.commit()
            duplicate_repair=repair_owner_cross_campaign_duplicates(db,owner_id,limit=1000)
            if int(duplicate_repair.get('repaired') or 0):
                db.commit()
            irrelevant_ready_repaired=repair_irrelevant_ready_members(db,c,limit=1000)
            if irrelevant_ready_repaired:
                db.commit()
            # SMTP and IMAP health are independent. An IMAP success must never
            # erase a still-unresolved SMTP failure. Both channels fail closed:
            # the minute worker repairs SMTP with rate-limited LOGIN/NOOP and
            # repairs IMAP with the normal inbox poller.
            mailbox_health=db.execute(text("""SELECT
              last_checked_at,last_error,
              smtp_last_checked_at,smtp_last_error,
              imap_last_checked_at,imap_last_error
              FROM client_mailboxes WHERE id=:m AND status='active'"""),
              {'m':int(c['mailbox_id'])}).mappings().first()
            if not mailbox_health:
                return {'queued':0,'status':'mailbox_required'}
            if mailbox_health.get('smtp_last_error'):
                return {'queued':0,'status':'mailbox_smtp_unhealthy',
                        'mailbox_id':int(c['mailbox_id']),
                        'last_checked_at':mailbox_health.get('smtp_last_checked_at'),
                        'last_error':str(mailbox_health.get('smtp_last_error') or '')[:160],
                        'self_heal':'automatic_rate_limited_smtp_login_noop_probe'}
            if mailbox_health.get('imap_last_error'):
                return {'queued':0,'status':'mailbox_imap_unhealthy',
                        'mailbox_id':int(c['mailbox_id']),
                        'last_checked_at':mailbox_health.get('imap_last_checked_at'),
                        'last_error':str(mailbox_health.get('imap_last_error') or '')[:160],
                        'self_heal':'automatic_imap_inbox_poll'}
            pending_copy=db.execute(text("SELECT count(*) FROM mailbox_sent_copy_queue WHERE mailbox_id=:m AND status='pending'"),
                                    {'m':int(c['mailbox_id'])}).scalar() or 0
            if pending_copy:
                return {'queued':0,'status':'waiting_sent_copy','pending_sent_copies':int(pending_copy)}
            auth_probe=db.execute(text("""SELECT id,attempts,next_attempt_at,last_error
              FROM email_queue
              WHERE mailbox_id=:m AND status='queued' AND attempts>0
                AND last_error LIKE 'SMTPAuthenticationError%'
              ORDER BY updated_at DESC LIMIT 1"""), {'m':int(c['mailbox_id'])}).mappings().first()
            if auth_probe:
                return {'queued':0,'status':'mailbox_auth_backoff',
                        'probe_id':int(auth_probe['id']),'attempts':int(auth_probe['attempts'] or 0),
                        'next_attempt_at':auth_probe.get('next_attempt_at')}
            sync_campaign(db,campaign_id)
            revision_state=owner_copy_revision_state(db,c,apply=True)
            db.commit()
            if revision_state.get('changed'):
                from app.services.action_log import log_action, ACTOR_BORIS_AUTO
                pending=revision_state.get('status')=='pending'
                log_action(
                    '__owner_outreach__',
                    ('Поставил текст на безопасную доработку' if pending else 'Снял текст с доработки после полезного ответа'),
                    object_name=f"{c.get('name') or 'Email-кампания'} · {revision_state.get('version') or ''}",
                    object_kind='email_copy',
                    before_val=str(revision_state.get('version') or ''),
                    after_val=('revision_pending' if pending else str(revision_state.get('status') or '')),
                    reason=(
                        f"{int(revision_state.get('sent') or 0)} отправок текущей версии без квалифицированных ответов; "
                        "кампания продолжает работать до безопасной замены"
                        if pending else
                        "После позднего квалифицированного ответа автоматическая замена больше не требуется"
                    ),
                    actor=ACTOR_BORIS_AUTO,
                    source='prospect_copy_guard',
                    request_id=f"owner_copy_revision:{campaign_id}:{revision_state.get('version') or ''}:{revision_state.get('status') or ''}",
                )
            tz_name=os.getenv('PROSPECT_TIMEZONE','Europe/Moscow')
            today=datetime.now(ZoneInfo(tz_name)).date()
            used=db.execute(text("SELECT count(*) FROM prospect_campaign_members WHERE campaign_id=:c AND status IN ('queued','sent') AND timezone(:tz,queued_at)::date=:d"),
                            {'c':campaign_id,'tz':tz_name,'d':today}).scalar() or 0
            allowance=max(0,min(int(max_enqueue),int(c['daily_limit'])-int(used)))
            if allowance<=0:
                return {'queued':0,'status':'daily_limit'}
            allowance,owner_reason=_owner_allowance(db,owner_id,allowance)
            if allowance<=0:
                return {'queued':0,'status':owner_reason}
            deliverability=campaign_deliverability(db,campaign_id)
            if deliverability.get('state')=='blocked':
                return {'queued':0,'status':'deliverability_blocked','deliverability':deliverability,
                        'self_heal':'hard_failures_already_suppressed; owner review required before further sends'}
            override=deliverability.get('max_enqueue_override')
            if override is not None:
                allowance=min(int(allowance),max(0,int(override)))
            if allowance<=0:
                return {'queued':0,'status':'deliverability_blocked','deliverability':deliverability}
            selected=[dict(x) for x in db.execute(text("""SELECT m.*,pc.name company,pc.city,pc.website,pc.domain company_domain
              FROM prospect_campaign_members m JOIN prospect_companies pc ON pc.id=m.company_id
              WHERE m.campaign_id=:c AND m.status='ready'
                AND COALESCE(m.quality_score,0)>=:minq
                AND NOT EXISTS(SELECT 1 FROM prospect_suppression s WHERE s.kind='email' AND s.normalized_value=m.email)
                AND (COALESCE(pc.domain,'')='' OR NOT EXISTS(
                  SELECT 1
                  FROM prospect_campaign_members z JOIN prospect_companies zc ON zc.id=z.company_id
                  WHERE z.campaign_id=:c AND z.id<>m.id
                    AND lower(COALESCE(zc.domain,''))=lower(pc.domain)
                    AND (z.sent_at IS NOT NULL OR z.status IN ('queued','sent'))
                ))
                AND (SELECT count(*) FROM prospect_campaign_members x WHERE x.campaign_id=:c AND x.email_domain=m.email_domain
                     AND x.status IN ('queued','sent')
                     AND timezone(:tz,x.queued_at)::date=:d) < :pd
              ORDER BY m.quality_score DESC,m.id LIMIT :lim"""),
              {'c':campaign_id,'pd':int(c['per_domain_daily_limit']),'minq':int(c['min_quality_score']),'lim':allowance,'tz':tz_name,'d':today}).mappings().all()]
            db.rollback()
        finally:
            db.close()

        queued=0; reconciled=0; gated=0
        for r in selected:
            company_allowed,_company_gate_reason=_final_company_relevance_gate_before_send(
                r,str(c.get('niche') or '')
            )
            if not company_allowed:
                gated+=1
                continue
            r,send_allowed,_gate_reason=_canonicalize_member_email_before_send(r)
            if not send_allowed:
                gated+=1
                continue
            # Copy identity is part of queue identity. A changed approved
            # offer therefore cannot reuse an older queued payload.
            row_ctx={'company':r.get('company') or '', 'city':r.get('city') or '',
                     'website':r.get('website') or '', 'niche':c.get('niche') or ''}
            is_owner=str(c.get('account_id') or '') == '__owner_outreach__'
            is_development=str(c.get('name') or '') == 'Кирилл · разработка SaaS/App'
            if is_owner:
                # Strict send-order rotation A→F. Owner allowance is one queued
                # message per pacing window, so successful sent count is the
                # canonical sequence cursor and retries keep their stored label.
                vdb=SessionLocal()
                try:
                    ab=_owner_next_rotation_variant(vdb,c)
                finally:
                    vdb.close()
            else:
                ab=_ab_variant(c,int(r['id']))
            copy_subject_src=str((ab or {}).get('subject') or c.get('subject_template') or '')
            copy_body_src=str((ab or {}).get('body') or c.get('body_template') or '')
            personalized_development=False
            if is_development and not ab:
                rendered_fallback=_render(copy_body_src,row_ctx).strip()
                copy_body_src,personalized_development=_development_personalized_body(
                    int(r.get('company_id') or 0),str(r.get('company') or ''),rendered_fallback
                )
            copy_version=_copy_version(copy_subject_src,copy_body_src,str((ab or {}).get('label') or ('personalized' if personalized_development else 'base')))
            key=f"prospect_campaign:{campaign_id}:{r['contact_id']}:{copy_version}"
            # Phase 2a: reconcile only the exact same copy version.
            rdb=SessionLocal()
            try:
                if _reconcile_existing_queue(
                    rdb,campaign_id,r,key=key,copy_version=copy_version,
                    ab_label=((ab or {}).get('label') if ab else None),
                ):
                    rdb.commit(); reconciled+=1; continue
                rdb.rollback()
            finally:
                rdb.close()

            # Phase 2b: approved campaign copy is the default production path.
            # Paid AI copy is opt-in only; owner outreach must never consume a
            # client account's budget by accident.
            # Owner outreach always uses the approved stored copy. This prevents
            # accidental paid AI calls and keeps the measured copy version exact.
            paid_copy_enabled=(
                str(os.getenv('PROSPECT_PAID_COPY_ENABLED','0')).strip().lower() in {'1','true','yes','on'}
                and not ab and not is_owner
            )
            if ab:
                subject=_render_subject(str(ab.get('subject') or ''),row_ctx,1)
                body=_render(str(ab.get('body') or ''),row_ctx).strip()
            elif paid_copy_enabled:
                try:
                    subject,body=_generate_openai_email_copy(
                        company=r.get('company') or '', niche=c['niche'], city=r.get('city') or '', website=r.get('website') or '',
                        account_id=str(c.get('account_id') or ''), idempotency_key=key,
                    )
                except Exception:
                    subject=_render_subject(str(c.get('subject_template') or ''),row_ctx,int(r['id']))
                    body=_render(str(c.get('body_template') or ''),row_ctx).strip()
            else:
                subject=_render_subject(str(c.get('subject_template') or ''),row_ctx,int(r['id']))
                body=(copy_body_src if is_development and personalized_development else _render(str(c.get('body_template') or ''),row_ctx).strip())
            if len(subject)<2:
                subject='Вопрос по привлечению клиентов' if is_owner else 'Предложение для вашей компании'
            if len(body)<120:
                body=('Добрый день!\n\nПредлагаем быстро рассчитать стоимость поставки под вашу задачу. '
                      'Ответьте на письмо адресом и примерным объёмом — менеджер подготовит вариант без долгой переписки.')
            if not is_owner and 'не интересно' not in body.lower():
                body += '\n\nЕсли предложение не актуально, ответьте «не интересно» — адрес будет исключён из следующих обращений.'
            attachments=[]; cid=''
            banner_mode=(os.getenv('PROSPECT_EMAIL_BANNER_MODE') or 'off').strip().lower()
            # Owner outreach in brand_icon mode ignores any campaign A/B image:
            # only the whitelisted BORIS brand asset may be attached.
            if ab and ab.get('banner_path') and not (is_owner and banner_mode == 'brand_icon'):
                approved=_safe_ab_banner(str(ab.get('banner_path') or ''))
                if approved:
                    banner_path,banner_mime=approved
                    cid=f"ab-banner-{campaign_id}-{r['id']}"
                    attachments.append({'path':banner_path,'filename':os.path.basename(banner_path),'mime':banner_mime,'inline':True,'cid':cid})
            if not cid:
                if banner_mode == 'brand_icon':
                    approved=_brand_icon_attachment()
                    if approved:
                        attachments.append(approved)
                        cid=str(approved.get('cid') or '')
                elif banner_mode == 'reuse':
                    try:
                        from app.services.prospect_banner import render_banner
                        cid=f"boris-banner-{campaign_id}-{r['id']}"
                        banner=render_banner(company=r.get('company') or '',niche=c['niche'],city=r.get('city') or '',campaign_id=campaign_id,member_id=r['id'])
                        attachments.append({'path':banner,'filename':'banner.png','mime':'image/png','inline':True,'cid':cid})
                    except Exception:
                        cid=''
            if c.get('attachment_path'):
                attachments.append({'path':c['attachment_path'],'filename':c['attachment_path'].rsplit('/',1)[-1],'mime':'application/pdf'})
            if is_owner:
                # BORIS owner outreach carries exactly one approved banner.
                # Banner order is tied to the A→F copy slot: 1→2→3→4→1→2.
                attachments=[]
                _banner_slot=_owner_banner_slot_for_label((ab or {}).get('label'))
                approved_banner,cid,_banner_label=_owner_outreach_banner(_banner_slot)
                if approved_banner:
                    attachments=[approved_banner]
                html=_html_email(
                    body,'','',brand_subtitle='BORIS AI',brand_note='',
                    marketing_banner_cid=cid,
                )
            elif is_development:
                # Development outreach must never inherit BORIS artwork/branding.
                attachments=[]
                cid=''
                html=_html_email(body,'','',brand_subtitle='Разработка программных продуктов',brand_note='')
            else:
                html=_html_email(body,cid,'')

            if str(c.get('account_id') or '') == '__owner_outreach__':
                contract_errors=_owner_outreach_contract(subject,body,html,attachments)
                if contract_errors:
                    # Fail closed but DO NOT persist a campaign pause. During a
                    # rolling deploy an old worker and a new DB/content contract
                    # can overlap briefly. A permanent pause turns a transient,
                    # safely-blocked mismatch into an owner-operated outage.
                    # Keeping the campaign active lets the next scheduler tick
                    # retry automatically after DETECT/SELF-HEAL/deploy convergence.
                    return {
                        'queued':queued,'reconciled':reconciled,
                        'status':'content_contract_blocked_retrying',
                        'errors':contract_errors,
                        'changed_delivery':False,
                        'self_heal':'campaign stays active; next scheduler tick retries after integrity/deploy convergence',
                    }
            if is_development:
                development_errors=_development_outreach_contract(subject,body,html,attachments)
                if development_errors:
                    pdb=SessionLocal()
                    try:
                        pdb.execute(text("UPDATE prospect_campaigns SET status='paused',paused_at=NOW(),updated_at=NOW() WHERE id=:c"),{'c':campaign_id})
                        pdb.commit()
                    finally:
                        pdb.close()
                    return {'queued':queued,'reconciled':reconciled,'status':'development_content_contract_failed','errors':development_errors}

            q=enqueue_email(
                r['email'],subject,body,html=html,source='prospect_campaign',idempotency_key=key,
                send_now=False,attachments=attachments,mailbox_id=c.get('mailbox_id'),reply_to=None,
                from_name=('Кирилл' if is_development else None),
                ref_type='prospect_campaign_member',ref_id=str(r['id']),
            )

            # Phase 2c: persist member linkage in a fresh short transaction.
            if q.get('id'):
                wdb=SessionLocal()
                try:
                    changed=wdb.execute(text("""UPDATE prospect_campaign_members SET status='queued',email_queue_id=:q,
                      queued_at=COALESCE(queued_at,NOW()),ab_variant=:ab,copy_version=:cv,updated_at=NOW()
                      WHERE id=:id AND status='ready'"""),
                      {'q':q['id'],'ab':(ab.get('label') if ab else None),'cv':copy_version,'id':r['id']}).rowcount or 0
                    wdb.commit(); queued+=int(bool(changed))
                finally:
                    wdb.close()
        return {
            'queued':queued,'reconciled':reconciled,'gated':gated,
            'stale_copy_requeued':int(stale_requeued or 0),
            'status':'ok','deliverability':deliverability,
        }

def tick_all(max_campaigns:int=20,max_enqueue_each:int=10)->dict:
    db=SessionLocal(); ensure_schema(db)
    try:
        ids=[int(x[0]) for x in db.execute(text("""SELECT c.id FROM prospect_campaigns c
          LEFT JOIN prospect_campaign_members m ON m.campaign_id=c.id AND m.queued_at IS NOT NULL
          WHERE c.status='active' GROUP BY c.id ORDER BY max(m.queued_at) ASC NULLS FIRST,c.id LIMIT :n"""),{"n":max_campaigns}).all()]
    finally: db.close()
    return {cid:tick_campaign(cid,max_enqueue_each) for cid in ids}

def campaign_stats(owner_id:int,campaign_id:int)->dict:
    db=SessionLocal(); ensure_schema(db)
    try:
        c=db.execute(text("SELECT id,name,niche,regions,status,daily_limit,per_domain_daily_limit,min_quality_score,account_id,mailbox_id,created_at,activated_at FROM prospect_campaigns WHERE id=:c AND owner_id=:o"),{"c":campaign_id,"o":owner_id}).mappings().first()
        if not c: raise ValueError("campaign not found")
        counts={r[0]:int(r[1]) for r in db.execute(text("SELECT status,count(*) FROM prospect_campaign_members WHERE campaign_id=:c GROUP BY status"),{"c":campaign_id})}
        replies={r[0] or "none":int(r[1]) for r in db.execute(text("SELECT reply_status,count(*) FROM prospect_campaign_members WHERE campaign_id=:c GROUP BY reply_status"),{"c":campaign_id})}
        ab_stats=[dict(r) for r in db.execute(text("""SELECT COALESCE(ab_variant,'—') variant,
          count(*) total,
          count(*) FILTER (WHERE queued_at IS NOT NULL) queued,
          count(*) FILTER (WHERE sent_at IS NOT NULL) sent,
          count(*) FILTER (WHERE reply_status IN ('replied','opt_out')) replied,
          count(*) FILTER (WHERE reply_status='replied') positive_replied,
          count(*) FILTER (WHERE reply_status='opt_out') opt_out
          FROM prospect_campaign_members WHERE campaign_id=:c
          GROUP BY COALESCE(ab_variant,'—') ORDER BY variant"""),{"c":campaign_id}).mappings().all()]
        daily=_owner_daily_state(db,int(owner_id))
        tz=daily['timezone']; today=daily['today']
        sent_today=int(db.execute(text("SELECT count(*) FROM prospect_campaign_members WHERE campaign_id=:c AND timezone(:tz,sent_at)::date=:d"),{"c":campaign_id,"tz":tz,"d":today}).scalar() or 0)
        queued_today=int(db.execute(text("SELECT count(*) FROM prospect_campaign_members WHERE campaign_id=:c AND status IN ('queued','sent') AND timezone(:tz,queued_at)::date=:d"),{"c":campaign_id,"tz":tz,"d":today}).scalar() or 0)
        attempted_today=int(db.execute(text("SELECT count(*) FROM prospect_campaign_members WHERE campaign_id=:c AND timezone(:tz,queued_at)::date=:d"),{"c":campaign_id,"tz":tz,"d":today}).scalar() or 0)
        _,owner_reason=_owner_allowance(db,int(owner_id),1000)
        deliverability=campaign_deliverability(db,campaign_id)
        return {"campaign":dict(c),"counts":counts,"replies":replies,"ab_stats":ab_stats,"deliverability":deliverability,"total":sum(counts.values()),
                "today":{"sent":sent_today,"queued_or_sent":queued_today,"attempted":attempted_today,
                         "effective_daily_cap":int(daily['cap']),"owner_used":int(daily['used']),"remaining":int(daily['remaining']),
                         "attempt_cap":int(daily.get('attempt_cap') or 0),"attempt_remaining":int(daily.get('attempt_remaining') or 0),
                         "ramp_day":int(daily['age'])+1,"gate":owner_reason,"timezone":tz,
                         "next_cap":daily.get('next_cap'),
                         "next_change_date":daily.get('next_change_date').isoformat() if daily.get('next_change_date') else None}}
    finally: db.close()
