# -*- coding: utf-8 -*-
"""Authoritative daily call-analysis digest for BORIS.

One source of truth: persisted call_analysis rows. The same rendered digest is
upserted (send once, then edit in place) to owner and client report routes.
Before the end-of-day cutoff an empty digest is NOT sent, preventing false
"today there were no calls" messages while ingestion/analysis is still running.
No AI/provider calls are made here; cost is 0 RUB.
"""
import argparse, hashlib, json, os, sys
from collections import Counter
from datetime import date as _date, datetime, time, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/BORIS/backend")
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from app.api.inbox_slots import SessionLocal

REPORT_TZ = ZoneInfo(os.environ.get("BORIS_REPORT_TZ", "Europe/Moscow"))
FINAL_EMPTY_HOUR = int(os.environ.get("BORIS_CALL_DIGEST_FINAL_EMPTY_HOUR", "21"))


def accounts_of(db, owner):
    rows = db.execute(text("""
        SELECT a.account_id, coalesce(a.name,'') FROM accounts a
         WHERE a.owner_user_id=:u
           AND NOT EXISTS (SELECT 1 FROM reliability_kill_switches r WHERE r.account_id=a.account_id AND r.blocked=true AND r.module IN ('actions','background','reports'))
        UNION
        SELECT x.account_id, coalesce(a.name,'')
          FROM user_account_access x LEFT JOIN accounts a ON a.account_id=x.account_id
         WHERE x.user_id=:u
           AND NOT EXISTS (SELECT 1 FROM reliability_kill_switches r WHERE r.account_id=x.account_id AND r.blocked=true AND r.module IN ('actions','background','reports'))
    """), {"u": owner}).fetchall()
    return {r[0]: (r[1] or r[0]) for r in rows}


def _day_window_utc(day):
    d = _date.fromisoformat(str(day))
    start_local = datetime.combine(d, time.min, tzinfo=REPORT_TZ)
    end_local = datetime.combine(d, time.max, tzinfo=REPORT_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def collect(db, accs, day):
    if not accs:
        return []
    start_utc, end_utc = _day_window_utc(day)
    rows = db.execute(text(
        "SELECT account_id, call_id, minutes, analysis, created_at FROM call_analysis"
        " WHERE account_id = ANY(:a) AND created_at >= :s AND created_at <= :e"
        " ORDER BY created_at"),
        {"a": list(accs.keys()), "s": start_utc, "e": end_utc}).fetchall()
    out = []
    for acc, call_id, minutes, an, created_at in rows:
        if isinstance(an, str):
            try: an = json.loads(an)
            except Exception: an = {}
        out.append({"acc": acc, "call_id": call_id, "minutes": float(minutes or 0),
                    "an": an or {}, "created_at": created_at})
    return out


def render(accs, day, calls, *, live=True, api_stats=None, chat_stats=None):
    suffix = " · обновляется" if live else ""
    L = ["📞 Разбор звонков — %s%s" % (day, suffix), ""]
    by_acc = {}
    for c in calls:
        by_acc.setdefault(c["acc"], []).append(c)

    if not calls:
        # Source truth must not disappear merely because AI analysis is absent.
        # This digest performs no paid AI calls; it only explains the current state.
        if api_stats:
            total=sum(int((api_stats.get(a) or {}).get("total") or 0) for a in accs)
            answered=sum(int((api_stats.get(a) or {}).get("answered") or 0) for a in accs)
            missed=sum(int((api_stats.get(a) or {}).get("missed") or 0) for a in accs)
            L.append("Всего звонков по Avito: %d" % total)
            L.append("Отвечено: %d" % answered)
            L.append("Пропущено: %d" % missed)
        if chat_stats:
            chats=sum(int((chat_stats.get(a) or {}).get("chats") or 0) for a in accs)
            messages=sum(int((chat_stats.get(a) or {}).get("messages") or 0) for a in accs)
            L.append("Переписок Avito: %d" % chats)
            L.append("Сообщений в переписках: %d" % messages)
        L.append("Разобрано разговоров: 0")
        L.append("")
        L.append("По аккаунтам:")
        for acc, name in sorted(accs.items(), key=lambda x: x[1].lower()):
            st=(api_stats or {}).get(acc) or {}
            cs=(chat_stats or {}).get(acc) or {}
            if st:
                L.append("• %s — звонки %d/%d отв., пропущено %d, разбор 0 · переписки %d" %
                         (name,int(st.get("total") or 0),int(st.get("answered") or 0),int(st.get("missed") or 0),int(cs.get("chats") or 0)))
            else:
                L.append("• %s — источник звонков сейчас недоступен для сводки" % name)
        # Entitlement diagnosis is read-only and prevents a false operational alarm.
        try:
            from app.api.calltracking import get_rop_minutes
            expired=[]; exhausted=[]
            for acc,name in accs.items():
                b=get_rop_minutes(acc)
                if b.get("expired"): expired.append(name)
                elif float(b.get("left") or 0)<=0: exhausted.append(name)
            if expired:
                L.append("")
                L.append("AI-разбор не выполняется: период РОП завершён — " + ", ".join(expired[:5]) + ".")
            elif exhausted:
                L.append("")
                L.append("AI-разбор не выполняется: пакет минут РОП исчерпан — " + ", ".join(exhausted[:5]) + ".")
            elif answered:
                L.append("")
                L.append("Есть отвеченные звонки без AI-разбора; BORIS должен обработать очередь анализа.")
        except Exception:
            pass
        return "\n".join(L)

    scores = [float(c["an"].get("score") or 0) for c in calls if c["an"].get("score") is not None]
    if api_stats:
        total=sum(int((api_stats.get(a) or {}).get("total") or 0) for a in accs)
        answered=sum(int((api_stats.get(a) or {}).get("answered") or 0) for a in accs)
        missed=sum(int((api_stats.get(a) or {}).get("missed") or 0) for a in accs)
        L.append("Всего звонков по Avito: %d" % total)
        L.append("Отвечено: %d" % answered)
        L.append("Пропущено: %d" % missed)
    L.append("Разобрано разговоров: %d" % len(calls))
    L.append("Суммарно минут: %.0f" % sum(c["minutes"] for c in calls))
    if scores:
        L.append("Средняя оценка: %.1f" % (sum(scores) / len(scores)))
    if chat_stats:
        chats=sum(int((chat_stats.get(a) or {}).get("chats") or 0) for a in accs)
        messages=sum(int((chat_stats.get(a) or {}).get("messages") or 0) for a in accs)
        L.append("Переписок Avito: %d" % chats)
        L.append("Сообщений в переписках: %d" % messages)
    L.append("")
    L.append("По аккаунтам:")
    for acc, name in sorted(accs.items(), key=lambda x: x[1].lower()):
        st=(api_stats or {}).get(acc) or {}
        if st:
            cs=(chat_stats or {}).get(acc) or {}
            L.append("• %s — звонки %d/%d отв., пропущено %d, разбор %d · переписки %d" % (name,int(st.get("total") or 0),int(st.get("answered") or 0),int(st.get("missed") or 0),len(by_acc.get(acc, [])),int(cs.get("chats") or 0)))
        else:
            L.append("• %s — разобрано %d" % (name, len(by_acc.get(acc, []))))
    L.append("")

    misses = Counter()
    for c in calls:
        for item in (c["an"].get("checklist") or []):
            if isinstance(item, dict) and not item.get("done"):
                misses[str(item.get("item") or "").strip()] += 1
    if misses:
        L.append("❗ Что чаще всего пропускали")
        for name, n in misses.most_common(5):
            L.append("• %s — %d из %d" % (name, n, len(calls)))
        L.append("")

    hooks = [h for c in calls for h in (c["an"].get("client_hooks") or []) if h]
    if hooks:
        L.append("🎯 Что важно клиентам")
        for h in hooks[:5]: L.append("• %s" % str(h)[:140])
        L.append("")

    growth = [g for c in calls for g in (c["an"].get("growth_points") or []) if g]
    if growth:
        L.append("📈 Точки роста")
        for g in growth[:5]: L.append("• %s" % str(g)[:140])
        L.append("")

    stops = []
    for c in calls:
        for s in (c["an"].get("stop_words") or []):
            if isinstance(s, dict):
                w = s.get("word") or s.get("phrase") or s.get("text") or s.get("stop_word") or ""
            else: w = str(s)
            w = str(w).strip()
            if w and w not in stops: stops.append(w)
    if stops:
        L.append("🚫 Стоп-слова: " + ", ".join(w[:40] for w in stops[:8]))
        L.append("")

    weak = sorted([c for c in calls if c["an"].get("score") is not None],
                  key=lambda c: float(c["an"]["score"]))[:3]
    if weak and float(weak[0]["an"]["score"]) < 50:
        L.append("🔻 Слабые звонки")
        for c in weak:
            if float(c["an"]["score"]) >= 50: continue
            who = str(c["an"].get("client_name") or "").strip() or "клиент не назван"
            L.append("• звонок %s — оценка %s (%s)" % (c["call_id"], c["an"]["score"], who))
        L.append("")

    rec = [str(c["an"].get("recommendation") or "").strip() for c in calls]
    rec = [r for r in rec if r]
    if rec: L.append("💡 Рекомендация: " + rec[0][:300])
    return "\n".join(L).strip()


def route_scopes(db, owner, all_accs):
    """Return destination -> account scope without cross-client leakage.

    Explicit client_reports routes win over a generic owner route when both point
    to the same Telegram destination. This matters for management groups that
    were historically also stored as owner:executive routes.
    """
    out = {}
    if all_accs:
        rows = db.execute(text(
            "SELECT account_id,chat_id,thread_id FROM tg_routes"
            " WHERE account_id = ANY(:a) AND thread_key='client_reports'"),
            {"a": list(all_accs.keys())}).fetchall()
        for acc, chat_id, thread_id in rows:
            key=(str(chat_id), int(thread_id or 0))
            out.setdefault(key,{})[acc]=all_accs.get(acc,acc)

    owner_route = db.execute(text(
        "SELECT chat_id,thread_id FROM tg_routes WHERE account_id=:k"
        " AND thread_key IN ('rop','executive')"
        " ORDER BY CASE thread_key WHEN 'rop' THEN 0 ELSE 1 END LIMIT 1"),
        {"k": "owner:%d" % owner}).fetchone()
    if owner_route:
        key=(str(owner_route[0]), int(owner_route[1] or 0))
        if key not in out:
            out[key]=dict(all_accs)
    return out


def _state_key(owner, day, chat_id, thread_id):
    raw=f"{owner}|{day}|{chat_id}|{thread_id}"
    return "rop_digest_msg:"+hashlib.sha1(raw.encode()).hexdigest()[:24]


def _load_state(db, owner, key):
    row=db.execute(text("SELECT value FROM storage WHERE account_id=:a AND key=:k LIMIT 1"),
                   {"a":f"owner:{owner}","k":key}).fetchone()
    if not row or not row[0]: return {}
    try: return json.loads(row[0]) if isinstance(row[0],str) else dict(row[0])
    except Exception: return {}


def _save_state(db, owner, key, value):
    """Persist digest state with one safe DB reconnect retry.

    This function has no external side effects. Retrying here cannot resend or
    re-edit Telegram; it only repeats the idempotent storage upsert after a
    transient PostgreSQL disconnect.
    """
    payload=json.dumps(value,ensure_ascii=False)
    for attempt in range(2):
        try:
            row=db.execute(text("SELECT id FROM storage WHERE account_id=:a AND key=:k LIMIT 1"),
                           {"a":f"owner:{owner}","k":key}).fetchone()
            if row:
                db.execute(text("UPDATE storage SET value=:v WHERE id=:id"),{"v":payload,"id":row[0]})
            else:
                db.execute(text("INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"),
                           {"a":f"owner:{owner}","k":key,"v":payload})
            db.commit()
            return
        except OperationalError:
            db.rollback()
            if attempt:
                raise


def _digest_state_payload(message_id, digest, text_body, report_date=None):
    return {
        "message_id": int(message_id or 0),
        "sha256": str(digest),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "contract": "avito_calls_truth_v2",
        "report_date": str(report_date or ""),
        "text": str(text_body)[:3900],
    }


def upsert_digest(db, owner, day, chat_id, thread_id, text_body):
    from app.telegram_bot import send_telegram_message, edit_telegram_message
    key=_state_key(owner,day,chat_id,thread_id)
    state=_load_state(db,owner,key)
    digest=hashlib.sha256(text_body.encode()).hexdigest()
    if state.get("sha256")==digest:
        # Refresh durable proof even when Telegram text itself needs no edit.
        expected=_digest_state_payload(state.get("message_id"),digest,text_body,day)
        if (
            state.get("contract")!=expected["contract"]
            or state.get("report_date")!=expected["report_date"]
            or state.get("text")!=expected["text"]
        ):
            _save_state(db,owner,key,expected)
        return {"status":"unchanged","message_id":state.get("message_id")}
    mid=state.get("message_id")
    if mid:
        r=edit_telegram_message(chat_id,int(mid),text_body)
        if r.get("ok"):
            _save_state(db,owner,key,_digest_state_payload(int(mid),digest,text_body,day))
            return {"status":"edited","message_id":int(mid)}
    r=send_telegram_message(chat_id,text_body,thread_id or None)
    if not r.get("ok"):
        return {"status":"error","error":str(r)[:300]}
    new_mid=int((r.get("result") or {}).get("message_id") or 0)
    _save_state(db,owner,key,_digest_state_payload(new_mid,digest,text_body,day))
    return {"status":"sent","message_id":new_mid}


def _api_stats_cache_key(day):
    return "rop_digest_api_stats:" + str(day)


def _load_api_stats_cache(db, account_id, day):
    if db is None:
        return None
    try:
        row=db.execute(text(
            "SELECT value FROM storage WHERE account_id=:a AND key=:k"
            " ORDER BY id DESC LIMIT 1"
        ),{"a":str(account_id),"k":_api_stats_cache_key(day)}).fetchone()
        if not row or not row[0]:
            return None
        raw=json.loads(row[0]) if isinstance(row[0],str) else dict(row[0])
        if not isinstance(raw,dict):
            return None
        return {
            "total":int(raw.get("total") or 0),
            "answered":int(raw.get("answered") or 0),
            "missed":int(raw.get("missed") or 0),
        }
    except Exception:
        return None


def _save_api_stats_cache(db, account_id, day, stats):
    if db is None:
        return
    payload=json.dumps({
        "total":int(stats.get("total") or 0),
        "answered":int(stats.get("answered") or 0),
        "missed":int(stats.get("missed") or 0),
        "verified_at":datetime.now(timezone.utc).isoformat(),
    },ensure_ascii=False,sort_keys=True)
    key=_api_stats_cache_key(day)
    row=db.execute(text(
        "SELECT id FROM storage WHERE account_id=:a AND key=:k"
        " ORDER BY id DESC LIMIT 1"
    ),{"a":str(account_id),"k":key}).fetchone()
    if row:
        db.execute(text("UPDATE storage SET value=:v WHERE id=:id"),{"v":payload,"id":row[0]})
    else:
        db.execute(text(
            "INSERT INTO storage(account_id,key,value) VALUES(:a,:k,:v)"
        ),{"a":str(account_id),"k":key,"v":payload})
    db.commit()


def _prefer_verified_stats(candidate, cached):
    if cached is None:
        return candidate
    if candidate is None:
        return cached
    c_total=int(candidate.get("total") or 0)
    p_total=int(cached.get("total") or 0)
    c_answered=int(candidate.get("answered") or 0)
    p_answered=int(cached.get("answered") or 0)
    # Historical/current call counts are monotonic inside one account/day.
    # A lower later snapshot is treated as partial upstream data, not truth.
    if c_total < p_total or (c_total == p_total and c_answered < p_answered):
        return cached
    return candidate


def _api_stats_for_day(accs, day, db=None):
    import time as _time
    from app.api.calltracking import _ct_token, _get_calls
    d=_date.fromisoformat(str(day))
    start=datetime.combine(d,time.min,tzinfo=REPORT_TZ).isoformat()
    end=datetime.combine(d,time.max,tzinfo=REPORT_TZ).isoformat()
    out={}
    for acc in accs:
        cached=_load_api_stats_cache(db,acc,day)
        try:
            token,err=_ct_token(acc)
        except Exception:
            token,err=None,"token_unavailable"
        if err or not token:
            if cached is not None:
                out[acc]=cached
            continue
        rows=None
        # Avito can transiently return 200 with an empty/partial list. One weak
        # answer must not turn a previously verified historical day into a false zero.
        for attempt in range(2):
            try:
                code,data=_get_calls(token,start,end,acc)
            except Exception:
                code,data=0,{}
            if code==200:
                candidate=(data or {}).get("calls",[])
                if candidate or attempt==1:
                    rows=candidate
                    break
            if attempt==0:
                _time.sleep(0.4)
        candidate_stats=None
        if rows is not None:
            total=len(rows); answered=0
            for row in rows:
                c=row.get("call") or row
                if float(c.get("talkDuration") or 0)>0:
                    answered+=1
            candidate_stats={
                "total":total,
                "answered":answered,
                "missed":total-answered,
            }
        chosen=_prefer_verified_stats(candidate_stats,cached)
        if chosen is None:
            continue
        out[acc]=chosen
        if cached != chosen:
            _save_api_stats_cache(db,acc,day,chosen)
    return out

def _chat_stats_for_day(db, accs, day):
    d=_date.fromisoformat(str(day)); start=datetime.combine(d,time.min,tzinfo=REPORT_TZ).timestamp(); end=datetime.combine(d,time.max,tzinfo=REPORT_TZ).timestamp()
    out={}
    for acc in accs:
        r=db.execute(text("SELECT count(*),count(distinct avito_chat_id) FROM messenger_messages WHERE account_id=:a AND avito_created_at>=:s AND avito_created_at<=:e"),{"a":acc,"s":int(start),"e":int(end)}).fetchone()
        out[acc]={"messages":int(r[0] or 0),"chats":int(r[1] or 0)}
    return out

def _should_defer_digest(calls, raw_calls, final_empty):
    return (not calls) and int(raw_calls or 0) <= 0 and (not final_empty)


def run_owner(owner, day=None, send=False, force_empty=False):
    day = day or datetime.now(REPORT_TZ).date().isoformat()
    now_local=datetime.now(REPORT_TZ)
    db=SessionLocal()
    try:
        all_accs=accounts_of(db,owner)
        scopes=route_scopes(db,owner,all_accs)
        results=[]
        for (chat_id,thread_id), accs in scopes.items():
            calls=collect(db,accs,day)
            api_stats=_api_stats_for_day(accs,day,db=db)
            chat_stats=_chat_stats_for_day(db,accs,day)
            raw_calls=sum(int((api_stats.get(acc) or {}).get("total") or 0) for acc in accs)
            final_empty = force_empty or now_local.hour >= FINAL_EMPTY_HOUR or str(day) < now_local.date().isoformat()
            # A day is not empty merely because paid AI analysis produced no
            # call_analysis rows. Raw Avito calls are client-visible activity
            # and must trigger/update the digest even when ROP is expired.
            if _should_defer_digest(calls, raw_calls, final_empty):
                results.append({
                    "chat_id":chat_id,
                    "thread_id":thread_id,
                    "accounts":len(accs),
                    "calls":0,
                    "raw_calls":0,
                    "status":"deferred_empty",
                })
                continue
            txt=render(accs,day,calls,live=not final_empty,api_stats=api_stats,chat_stats=chat_stats)
            if not send:
                results.append({
                    "chat_id":chat_id,
                    "thread_id":thread_id,
                    "accounts":len(accs),
                    "calls":len(calls),
                    "raw_calls":raw_calls,
                    "status":"dry_run",
                    "text":txt,
                })
            else:
                r=upsert_digest(db,owner,day,chat_id,thread_id,txt)
                results.append({
                    "chat_id":chat_id,
                    "thread_id":thread_id,
                    "accounts":len(accs),
                    "calls":len(calls),
                    "raw_calls":raw_calls,
                    **r,
                })
        return {"owner":owner,"day":day,"tz":str(REPORT_TZ),"routes":results}
    finally:
        db.close()


def owners_with_routes():
    db=SessionLocal()
    try:
        rows=db.execute(text("SELECT DISTINCT account_id FROM tg_routes WHERE account_id LIKE 'owner:%'")).fetchall()
        out=[]
        for (raw,) in rows:
            try: out.append(int(str(raw).split(':',1)[1]))
            except Exception: pass
        return sorted(set(out))
    finally: db.close()


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--owner",type=int)
    p.add_argument("--all",action="store_true")
    p.add_argument("--date")
    p.add_argument("--send",action="store_true")
    p.add_argument("--force-empty",action="store_true")
    a=p.parse_args()
    owners=owners_with_routes() if a.all else ([a.owner] if a.owner else [])
    if not owners:
        p.error("use --owner ID or --all")
    for owner in owners:
        print(json.dumps(run_owner(owner,a.date,a.send,a.force_empty),ensure_ascii=False,default=str))


if __name__ == "__main__":
    main()
