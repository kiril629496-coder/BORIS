"""Account-scoped, stable, read-only projection of real Campaign action_log rows."""
import base64
import csv
import hashlib
import io
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import text

OLD_DETAILS = "Для этого исторического события детальное сравнение не сохранялось."
FILTERS = {"all", "boris", "staff", "changes", "publications", "ab", "errors"}
AUTO = ("boris", "boris_auto", "cron", "system")
SECRET = re.compile(r"(?i)(access[_ -]?token|refresh[_ -]?token|authorization|bearer|cookie|password|api[_ -]?key|sk-[A-Za-z0-9_-]{16,}|secret|stack trace|traceback|sha256|\b[a-f0-9]{40,64}\b)")
TECH = re.compile(r"(?i)\b(template_id|hypothesis_id|feed_identity|provider|mutation|worker|internal|depends_on)\b")

def public_text(value, limit=700):
    s=str(value or "").strip()
    if not s:
        return ""
    if SECRET.search(s) or s.startswith(("{","[")):
        return "Технические подробности скрыты"
    s=re.sub(r"<[^>]*>", " ", s)
    s=TECH.sub("служебные сведения", s)
    return " ".join(s.split())[:limit]

def _dates(period, start, end, tz):
    try: zone=ZoneInfo(tz)
    except Exception: raise HTTPException(422, "Некорректный часовой пояс")
    today=datetime.now(zone).date()
    if period in {"today","7","30"}:
        start=(today-timedelta(days={"today":0,"7":6,"30":29}[period])).isoformat()
        end=today.isoformat()
    elif period not in {"all","custom"}:
        raise HTTPException(422, "Некорректный период")
    if period=="custom" and (not start or not end):
        raise HTTPException(422, "Укажите начало и конец периода")
    try:
        lo=datetime.fromisoformat(start).replace(tzinfo=zone) if start else None
        hi=(datetime.fromisoformat(end)+timedelta(days=1)).replace(tzinfo=zone) if end else None
    except ValueError: raise HTTPException(422, "Некорректная дата")
    if lo and hi and lo>=hi: raise HTTPException(422, "Начало периода позже конца")
    return lo,hi,zone

def page(db, account_id, *, q="", category="all", period="all", start="", end="", tz="Europe/Moscow", cursor="", limit=40):
    if category not in FILTERS: raise HTTPException(422,"Некорректный фильтр")
    limit=max(1,min(100,int(limit)))
    lo,hi,zone=_dates(period,start,end,tz)
    q=str(q or "").strip()[:200]
    signature=hashlib.sha256(json.dumps([account_id,q,category,period,start,end,tz],ensure_ascii=False).encode()).hexdigest()
    last=watermark=None
    if cursor:
        try:
            data=json.loads(base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4)))
            if data["s"]!=signature: raise ValueError()
            last=int(data["l"]);watermark=int(data["w"])
            lo=datetime.fromisoformat(data["lo"]) if data.get("lo") else None
            hi=datetime.fromisoformat(data["hi"]) if data.get("hi") else None
            if last<1 or watermark<last: raise ValueError()
        except Exception: raise HTTPException(422,"Обновите историю: параметры страницы изменились")
    where=["l.account_id=:a", "(l.source LIKE 'campaign.%' OR l.source LIKE 'avito.%' OR l.source LIKE 'mass_editor%' OR l.action ILIKE '%объявлен%' OR l.action ILIKE '%Avito%')"]
    args={"a":account_id,"n":limit+1}
    if last is not None: where+=["l.id<:last","l.id<=:watermark"];args.update(last=last,watermark=watermark)
    if lo: where.append("l.ts>=:lo");args["lo"]=lo
    if hi: where.append("l.ts<:hi");args["hi"]=hi
    if category=="boris":where.append("l.actor IN ('boris','boris_auto','cron','system')")
    if category=="staff":where.append("COALESCE(l.actor,'') NOT IN ('boris','boris_auto','cron','system')")
    if category=="changes":where.append("(l.source LIKE '%patch%' OR l.source LIKE '%edit%' OR l.source LIKE '%transform%' OR l.source LIKE '%enhance%' OR l.action ILIKE '%измен%')")
    if category=="publications":where.append("(l.source LIKE '%publish%' OR l.source LIKE '%deliver%' OR l.action ILIKE '%публик%')")
    if category=="ab":where.append("(l.source LIKE 'campaign.ab%' OR l.action ILIKE '%A/B%')")
    if category=="errors":where.append("(l.action ILIKE '%ошиб%' OR l.action ILIKE '%не удалось%' OR l.source LIKE '%error%' OR l.source LIKE '%failed%')")
    project="""CASE WHEN l.object_kind='кампания' THEN l.object_name ELSE
      (SELECT c.name FROM campaigns c WHERE c.id::text=split_part(l.source,':',2)
       AND (c.default_account_id=l.account_id OR EXISTS
         (SELECT 1 FROM campaign_items ci WHERE ci.campaign_id=c.id AND ci.account_id=l.account_id))
       LIMIT 1) END"""
    # Only an explicit persisted campaign reference or campaign event supplies a
    # project. Matching a current title would invent links for renamed/duplicate ads.
    if q:
        args["q"]="%"+q.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        where.append("(l.object_name ILIKE :q OR l.action ILIKE :q OR l.actor ILIKE :q OR u.email ILIKE :q OR "+project+" ILIKE :q)")
    sql="SELECT l.id,l.ts,l.actor,l.action,l.object_kind,l.object_name,l.before_val,l.after_val,l.reason,l.source,u.email,"+project+" AS project FROM action_log l LEFT JOIN users u ON u.id=l.user_id WHERE "+" AND ".join(where)+" ORDER BY l.id DESC LIMIT :n"
    rows=db.execute(text(sql),args).fetchall()
    has_more=len(rows)>limit;rows=rows[:limit]
    if watermark is None and rows: watermark=int(rows[0][0])
    items=[]
    for r in rows:
        when=r[1]
        if when and when.tzinfo is None: when=when.replace(tzinfo=timezone.utc)
        actor="BORIS" if r[2] in AUTO else public_text(r[10] or r[2] or "Сотрудник")
        before=public_text(r[6]);after=public_text(r[7])
        items.append({"id":int(r[0]),"when":when.isoformat() if when else None,"actor":actor,
            "action":public_text(r[3]),"object_kind":public_text(r[4]),"object_name":public_text(r[5]),
            "project":public_text(r[11]),"account":account_id,"before":before,"after":after,
            "reason":public_text(r[8]),"duration_ms":None,"status":"Ошибка" if "ошиб" in str(r[3]).lower() or str(r[9]).split(":",1)[0].endswith((".error",".failed")) else "Событие записано",
            "details_notice":"" if before or after else OLD_DETAILS})
    next_cursor=base64.urlsafe_b64encode(json.dumps({"s":signature,"l":items[-1]["id"],"w":watermark,"lo":lo.isoformat() if lo else None,"hi":hi.isoformat() if hi else None}).encode()).decode().rstrip("=") if has_more and items else None
    return {"status":"ok","items":items,"next_cursor":next_cursor,"source":"real_action_log","history_contract":2}

def csv_chunk(items, header=False, tz="Europe/Moscow"):
    out=io.StringIO(newline="");writer=csv.writer(out,delimiter=";")
    if header:writer.writerow(["Дата","Время","Аккаунт","Проект","Объявление","Действие","Кто","Статус","Длительность"])
    def cell(v):
        s=public_text(v)
        return "'"+s if s.lstrip().startswith(("=","+","-","@")) else s
    for e in items:
        when=datetime.fromisoformat(e["when"]).astimezone(ZoneInfo(tz)) if e["when"] else None
        writer.writerow([when.strftime("%d.%m.%Y") if when else "",when.strftime("%H:%M:%S") if when else "",
            *[cell(e.get(k)) for k in ("account","project","object_name","action","actor","status")],
            "" if e.get("duration_ms") is None else str(e["duration_ms"]/1000)])
    return out.getvalue()
