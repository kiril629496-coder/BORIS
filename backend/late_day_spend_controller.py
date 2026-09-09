#!/usr/bin/env python3
import argparse, json, math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import httpx
from app.db.session import SessionLocal
from app.services.marketing_clock import marketing_today_iso
from app.models.storage import Storage
from kpi_goal_runner import get_goal_auto_accounts
from app.services.marketing_signal_guard import money_raise_signals_eligible
from app.services.control_plane_adapters_ext import marketing_service_entitlement
MSK=ZoneInfo("Europe/Moscow")
STATE_KEY="late_day_spend_boost_state"
LEARNING_KEY="late_day_spend_learning"
# INTRADAY_SPEND_PACING_START_V1:
# Start budget pacing after the morning learning window, not only in the evening.
# A raise is still impossible without fresh stats/spend, unmet lead KPI, safe CPL,
# proven conversion evidence, active promotion, measurement clearance and every
# final apply_one money guard. Budget is a ceiling; under-spend is only a reason
# to accelerate proven converters while KPI is behind.
BOOST_START_HOUR=10
MAX_ITEMS_PER_ACCOUNT_CYCLE=8
API_BASES=("http://127.0.0.1:8000","http://127.0.0.1:8001")

def _load(db,a,k):
    r=db.query(Storage).filter(Storage.account_id==a,Storage.key==k).order_by(Storage.id.desc()).first()
    if not r or not r.value:return {}
    try:return json.loads(r.value)
    except Exception:return {}

def _save(db,a,k,v):
    r=db.query(Storage).filter(Storage.account_id==a,Storage.key==k).order_by(Storage.id.desc()).first(); raw=json.dumps(v,ensure_ascii=False)
    if r:r.value=raw
    else:db.add(Storage(account_id=a,key=k,value=raw))
    db.commit()

def _post(path,payload,timeout=35):
    last=None
    for base in API_BASES:
        try:
            r=httpx.post(base+path,json=payload,timeout=timeout)
            if r.status_code<500:return r
            last=RuntimeError(f"http_{r.status_code}")
        except (httpx.ConnectError,httpx.ConnectTimeout) as exc:last=exc; continue
        except httpx.ReadTimeout:raise
    if last:raise last
    raise RuntimeError("internal_api_unavailable")

def _intensity(r):
    return 50 if r<=0.25 else 40 if r<=0.50 else 30 if r<=0.70 else 0

def _pacing_ratio(now_msk):
    # LATE_DAY_SPEND_TIME_PACING_V1: compare actual spend with the share of
    # the Moscow day already elapsed. Near midnight the expected share tends
    # to 100%, so a 70% spend ratio is still recognized as meaningful lag.
    seconds = now_msk.hour * 3600 + now_msk.minute * 60 + now_msk.second
    return max(0.0, min(1.0, seconds / 86400.0))

def _catchup_intensity(spend_ratio, now_msk):
    expected = _pacing_ratio(now_msk)
    if expected <= 0 or spend_ratio >= expected:
        return 0, expected, 0.0
    gap = max(0.0, expected - spend_ratio)
    relative_gap = gap / expected
    boost = 50 if relative_gap >= 0.50 else 40 if relative_gap >= 0.30 else 30
    return boost, expected, relative_gap

def _eligible_pool(advice, winner_ids=None, continuing_boost_ids=None, measurement_clear_ids=None):
    winner_ids={int(x) for x in (winner_ids or set()) if str(x).isdigit()}
    continuing_boost_ids={int(x) for x in (continuing_boost_ids or set()) if str(x).isdigit()}
    measurement_clear_ids={int(x) for x in (measurement_clear_ids or set()) if str(x).isdigit()}
    recs=(advice or {}).get("recommendations") or {}; by={}
    for group in ("raise","watching","lower","archive_candidates","lower_or_archive"):
        for x in recs.get(group) or []:
            if not isinstance(x,dict):continue
            iid=x.get("item_id") or x.get("id")
            try:iid=int(iid); bid=float(x.get("bid_rub") or 0)
            except Exception:continue
            if bid<=0 or not bool(x.get("promotion_active")):continue
            # LATE_DAY_CAUSAL_POOL_V2: evening catch-up spends only on a
            # currently promoted listing with observed demand evidence.
            contacts_7d=int(x.get("contacts_7d") or 0)
            if contacts_7d<=0:continue
            # LATE_DAY_ONE_STEP_THEN_MEASURE_V2: waiting measurement is binding.
            # Evening under-spend is not permission to stack hourly money changes
            # on one listing before the first causal result exists.
            if (str(x.get("boris_measure_status") or "") == "waiting_measurement"
                    and iid not in measurement_clear_ids): continue
            # LATE_DAY_LIVE_CONVERTER_FALLBACK_V1: scaling may use either durable
            # winner memory OR strong current conversion evidence. This avoids
            # spending on weak ads while allowing a live converter that has not
            # yet accumulated a finalized historical experiment verdict.
            today_contacts=int(x.get("contacts") or 0)
            live_converter=bool(today_contacts>0 or (contacts_7d>=2 and int(x.get("views_7d") or 0)>=5))
            if iid not in winner_ids and not live_converter:continue
            score=(1,contacts_7d,int(x.get("views_7d") or 0))
            if iid not in by or score>by[iid][0]:by[iid]=(score,{**x,"id":iid,"bid_rub":bid})
    vals=[v[1] for v in by.values()]
    vals.sort(key=lambda x:(not bool(x.get("winner_memory")),-int(x.get("contacts_7d") or 0),-int(x.get("views_7d") or 0),int(x["id"])))
    return vals

def _rotate_one_measurement_slot_for_pacing(
    db, account_id, advice, state, now_msk, *,
    spend_ratio, expected_ratio, contacts, target, cpl, red_cpl,
):
    """Free at most one causal slot/day for a materially under-paced strong converter.

    DB-only: never changes Avito. The old experiment becomes explicit
    inconclusive evidence; the replacement raise still goes through apply_one,
    the canonical account cap and every final money/provider guard.
    """
    from datetime import datetime as _SlotDt, timezone as _SlotTz
    from app.api.cpx_advisor import (
        _active_raise_measurement_summary as _slot_summary,
        _calculate_measure_effect as _slot_effect,
    )

    # LATE_DAY_PACING_SLOT_ROTATION_FRESH_GUARD_V1:
    # Do not rely only on the caller. Slot lifecycle changes can unlock a later
    # money write, so this helper independently requires the same fresh,
    # coherent <=15m spend+inventory proof as an actual raise.
    _rotation_money_ok, _rotation_money_evidence = money_raise_signals_eligible(
        db, account_id, max_age_seconds=900.0
    )
    if not _rotation_money_ok:
        return {
            "status":"money_signal_not_fresh",
            "rotated_item_ids":[],
            "money_evidence":_rotation_money_evidence,
        }

    day = now_msk.date().isoformat()
    rotations = state.get("slot_rotations") if isinstance(state.get("slot_rotations"), dict) else {}
    if str(state.get("last_slot_rotation_day") or "") == day:
        return {"status":"already_rotated_today","rotated_item_ids":[]}

    try:
        _cpl = float(cpl) if cpl is not None else None
        _red = float(red_cpl or 0)
        _spent_ratio = float(spend_ratio or 0)
        _expected = float(expected_ratio or 0)
    except Exception:
        return {"status":"invalid_economics","rotated_item_ids":[]}

    # LATE_DAY_PACING_SLOT_ROTATION_V1: only a severe pacing gap with very
    # strong CPL headroom may sacrifice an unfinished experiment.
    if contacts <= 0 or contacts >= target or _cpl is None or _red <= 0:
        return {"status":"economics_not_eligible","rotated_item_ids":[]}
    if _cpl > _red * 0.50:
        return {"status":"cpl_headroom_insufficient","rotated_item_ids":[]}
    if _expected <= 0 or _spent_ratio >= _expected * 0.70:
        return {"status":"pacing_gap_not_severe","rotated_item_ids":[]}

    summary = _slot_summary(db, account_id)
    cap = int(summary.get("cap") or 2)
    if int(summary.get("active_count") or 0) < cap:
        return {"status":"slot_already_available","rotated_item_ids":[]}

    # Index current advisor entries. Exact current CPX truth is required:
    # promotion_active + current bid equal to the experiment target.
    entries = {}
    recs = (advice or {}).get("recommendations") or {}
    for group in ("raise","watching","lower","archive_candidates","lower_or_archive"):
        for x in recs.get(group) or []:
            if not isinstance(x,dict): continue
            try: iid=int(x.get("id") or x.get("item_id"))
            except Exception: continue
            entries.setdefault(iid,x)

    now_utc = _SlotDt.now(_SlotTz.utc)
    candidates=[]
    for m in summary.get("items") or []:
        try:
            iid=int(m.get("item_id")); started=_SlotDt.fromisoformat(str(m.get("started_at")).replace("Z","+00:00"))
            if started.tzinfo is None: started=started.replace(tzinfo=_SlotTz.utc)
            age_h=(now_utc-started.astimezone(_SlotTz.utc)).total_seconds()/3600.0
        except Exception:
            continue
        if age_h < 12.0: continue
        x=entries.get(iid) or {}
        try:
            current_bid=float(x.get("bid_rub") or 0)
            expected_bid=float(m.get("new_bid_rub") or 0)
        except Exception:
            continue
        if not bool(x.get("promotion_active")) or current_bid <= 0 or abs(current_bid-expected_bid) >= 0.01:
            continue
        contacts7=int(x.get("contacts_7d") or 0); views7=int(x.get("views_7d") or 0)
        # Strong converter only. For Evgeniy's 11-ruble listing this is 2/12.
        if contacts7 < 2 or views7 < 5:
            continue
        effect=_slot_effect(db,account_id,iid,started)
        if str(effect.get("status") or "") != "waiting_measurement":
            continue
        after=effect.get("after") if isinstance(effect.get("after"),dict) else {}
        # If a completed positive result already exists, normal finalization must
        # handle it; rotation is only for still-unresolved windows.
        candidates.append((-(contacts7/max(1,views7)), -contacts7, -views7, age_h, iid, m, x, effect))

    if not candidates:
        return {"status":"no_strong_waiting_converter","rotated_item_ids":[]}

    candidates.sort()
    _,_,_,age_h,iid,m,x,effect=candidates[0]
    key=f"cpx_measure:{iid}:raise"
    row=(db.query(Storage).filter(Storage.account_id==account_id,Storage.key==key).order_by(Storage.id.desc()).first())
    if not row:
        return {"status":"measurement_row_missing","rotated_item_ids":[]}
    try: cur=json.loads(row.value or "{}")
    except Exception: cur={}
    if cur.get("status")!="waiting_measurement":
        return {"status":"measurement_already_changed","rotated_item_ids":[]}

    finished=now_utc.isoformat()
    cur.update({
        "status":"superseded",
        "finished_at":finished,
        "effect":"inconclusive_pacing_slot_rotation",
        "decision":"rotate_slot_to_strong_converter_underpace",
        "scale_eligible":False,
        "reason":(
            "Аккаунт сильно отстаёт от дневного темпа расхода при CPL значительно ниже красной цены. "
            "Старый незавершённый замер закрыт без verdict, чтобы один раз за день разрешить следующий "
            "ограниченный шаг на этом же доказанном конвертере."
        ),
        "pacing_slot_rotation":{
            "policy_version":"LATE_DAY_PACING_SLOT_ROTATION_V1",
            "day_msk":day,
            "age_hours":round(age_h,2),
            "spend_ratio":round(_spent_ratio,4),
            "expected_spend_ratio":round(_expected,4),
            "cpl_rub":round(_cpl,2),
            "red_cpl_rub":round(_red,2),
            "contacts_7d":int(x.get("contacts_7d") or 0),
            "views_7d":int(x.get("views_7d") or 0),
            "current_bid_rub":float(x.get("bid_rub") or 0),
        },
    })
    row.value=json.dumps(cur,ensure_ascii=False)
    journal=_load(db,account_id,"cpx_learning_journal") or []
    if not isinstance(journal,list): journal=[]
    journal.append({
        "finished_at":finished,"account_id":account_id,"item_id":iid,"action":"raise",
        "old_bid_rub":cur.get("old_bid_rub"),"new_bid_rub":cur.get("new_bid_rub"),
        "effect":"inconclusive_pacing_slot_rotation",
        "decision":"rotate_slot_to_strong_converter_underpace",
        "learning_contract":"daily_bid_learning_v1",
        "red_price_guard":"pacing_rotation_no_winner_verdict",
    })
    _save(db,account_id,"cpx_learning_journal",journal[-500:])
    rotations[str(iid)]={"day_msk":day,"rotated_at":finished}
    state["slot_rotations"]=rotations
    state["last_slot_rotation_day"]=day
    state["last_slot_rotation_item_id"]=iid
    state["last_slot_rotation_at"]=finished
    _save(db,account_id,STATE_KEY,state)
    db.commit()
    return {
        "status":"rotated","rotated_item_ids":[iid],"item_id":iid,
        "age_hours":round(age_h,2),"contacts_7d":int(x.get("contacts_7d") or 0),
        "views_7d":int(x.get("views_7d") or 0),
        "current_bid_rub":float(x.get("bid_rub") or 0),
        "effect_before_rotation":effect,
        "policy_version":"LATE_DAY_PACING_SLOT_ROTATION_V1",
    }


def _late_day_pacing_advice(advice, winner_ids=None):
    # LATE_DAY_MEASUREMENT_BINDING_V3: preserve advice exactly; the evening lane
    # must never rewrite waiting_measurement in memory to bypass causal learning.
    return advice or {}

def reset_previous_days(now_msk):
    db=SessionLocal(); summary=[]
    try:
        rows=db.query(Storage).filter(Storage.key==STATE_KEY).all(); accounts=sorted({str(r.account_id) for r in rows})
        for a in accounts:
            st=_load(db,a,STATE_KEY) or {}; day=str(st.get("day_msk") or "")
            if not day or day>=now_msk.date().isoformat():continue
            items=st.get("items") if isinstance(st.get("items"),dict) else {}
            st["status"]="reset_pending"; st["reset_started_at_msk"]=st.get("reset_started_at_msk") or now_msk.isoformat(); _save(db,a,STATE_KEY,st)
            changed=blocked=done=0
            for iid,meta in list(items.items()):
                if not isinstance(meta,dict) or str(meta.get("status") or "") in {"reset","skipped_below_baseline"}:continue
                baseline=meta.get("baseline_bid_rub")
                if baseline is None:continue
                payload={"account_id":a,"item_id":int(iid),"action":"lower","actor_type":"boris_auto","trigger":"moscow_midnight_reset","source":"late_day_spend_reset","target_bid_rub":float(baseline),"request_id":f"late-reset:{a}:{day}:{iid}"}
                try:
                    r=_post("/api/cpx_advisor/apply_one",payload); body=r.json() if r.content else {}; status=str(body.get("status") or "")
                    if status=="ok" and (body.get("new_bid_rub") is not None or body.get("idempotent")):
                        meta.update({"status":"reset","reset_at":datetime.now(timezone.utc).isoformat(),"reset_bid_rub":body.get("new_bid_rub") or baseline}); changed+=1
                    elif status=="skipped" and str(body.get("reason_code") or "") in {"late_day_reset_already_at_or_below_baseline","skipped_bid_unchanged"}:meta.update({"status":"skipped_below_baseline","reset_at":datetime.now(timezone.utc).isoformat()}); done+=1
                    else:meta["last_reset_block"]=(body.get("blocked_by") or body.get("reason_code") or body.get("reason") or body.get("message") or f"http_{r.status_code}"); blocked+=1
                except Exception as exc:meta["last_reset_block"]=f"{type(exc).__name__}:{str(exc)[:140]}"; blocked+=1
                st["items"][str(iid)]=meta; st["updated_at"]=datetime.now(timezone.utc).isoformat(); _save(db,a,STATE_KEY,st)
            pending=sum(1 for m in st.get("items",{}).values() if isinstance(m,dict) and str(m.get("status") or "") not in {"reset","skipped_below_baseline"})
            if pending==0:st["status"]="reset_complete"; st["reset_completed_at_msk"]=now_msk.isoformat(); _save(db,a,STATE_KEY,st)
            summary.append({"account_id":a,"changed":changed,"already_safe":done,"blocked":blocked,"pending":pending,"status":st.get("status")})
    finally:db.close()
    return summary

# LATE_DAY_SPEND_EFFECT_LEARNING_V1: learn after the Moscow day closes whether
# temporary evening catch-up produced a new contact. DB-only; never authorizes money.
def learn_previous_days(now_msk):
    db=SessionLocal(); out=[]
    try:
        rows=db.query(Storage).filter(Storage.key==STATE_KEY).all()
        for row in rows:
            a=str(row.account_id); st=_load(db,a,STATE_KEY) or {}; day=str(st.get("day_msk") or "")
            if not day or day>=now_msk.date().isoformat(): continue
            items=st.get("items") if isinstance(st.get("items"),dict) else {}
            boosted=[str(i) for i,m in items.items() if isinstance(m,dict) and m.get("baseline_bid_rub") is not None]
            if not boosted: continue
            hist=_load(db,a,LEARNING_KEY) or []
            if not isinstance(hist,list): hist=[]
            if any(isinstance(x,dict) and x.get("day_msk")==day for x in hist): continue
            ds=_load(db,a,"daily_stats:"+day) or {}; sp=ds.get("spending") if isinstance(ds.get("spending"),dict) else {}
            if str(sp.get("status") or "")!="ok": continue
            try: spent=float(sp.get("all_spend_rub") or 0)
            except Exception: continue
            active=[x for x in (ds.get("items") or []) if isinstance(x,dict) and x.get("status")=="active"]
            contacts=sum(int(x.get("contacts") or 0) for x in active)
            try: budget=float(st.get("spend_plan_rub") or 0); start_spend=float(st.get("spent_at_check_rub") or 0); start_contacts=int(st.get("contacts_at_check") or 0)
            except Exception: budget=0.0; start_spend=0.0; start_contacts=0
            spend_gain=max(0.0,spent-start_spend); contact_gain=max(0,contacts-start_contacts); end_ratio=(spent/budget) if budget>0 else None; cpl=(spent/contacts) if contacts>0 else None
            verdict="helped" if contact_gain>0 else "no_lead_after_boost"
            rec={"day_msk":day,"evaluated_at_msk":now_msk.isoformat(),"boosted_items":boosted,"start_spend_rub":round(start_spend,2),"end_spend_rub":round(spent,2),"post_boost_spend_rub":round(spend_gain,2),"start_contacts":start_contacts,"end_contacts":contacts,"post_boost_contacts":contact_gain,"end_spend_ratio":round(end_ratio,4) if end_ratio is not None else None,"end_cpl_rub":round(cpl,2) if cpl is not None else None,"verdict":verdict,"policy":"late_day_spend_effect_learning_v1"}
            hist.append(rec); _save(db,a,LEARNING_KEY,hist[-60:]); st["learning_verdict"]=verdict; st["learning_evaluated_at_msk"]=now_msk.isoformat(); _save(db,a,STATE_KEY,st); out.append({"account_id":a,**rec})
    finally: db.close()
    return out

def boost_evening(now_msk, account_filter=None):
    if now_msk.hour<BOOST_START_HOUR:return []
    accounts=[x for x in get_goal_auto_accounts() if float(x.get("target_leads_per_day") or 0)>0]; out=[]
    for cfg in accounts:
        a=str(cfg.get("account_id"))
        if account_filter and a != str(account_filter):
            continue
        db=SessionLocal()
        try:
            # LATE_DAY_CANONICAL_SERVICE_ENTITLEMENT_V1:
            # Evening catch-up is a spend-increasing lane. Block it before any
            # money/provider request unless the canonical paid marketing period
            # is active. Reset/compensation is a separate lower-only path above.
            entitlement=marketing_service_entitlement(db,a) or {}
            if str(entitlement.get("state") or "").lower()!="active":
                out.append({"account_id":a,"status":"blocked_entitlement",
                            "reason":"no_active_boris_marketer_entitlement",
                            "service_entitlement":entitlement,
                            "changed_avito":False,"applied":0})
                continue
            kpi=_load(db,a,"kpi_settings") or {}
            try:budget=float(kpi.get("daily_budget_limit_rub") or 0); red=float(kpi.get("max_cost_per_lead_rub") or 0); target=float(kpi.get("target_leads_per_day") or 0)
            except Exception:budget=red=target=0
            if budget<=0 or red<=0 or target<=0:continue
            # LATE_DAY_SPEND_FRESH_MONEY_PROOF_V1: evening acceleration is a
            # money-increasing action. It must use the same strict <=15 minute,
            # same-day, non-degraded spend+inventory proof as every other raise.
            # Reporting-compatible stale snapshots may remain visible, but they
            # can never authorize a catch-up boost.
            _money_ok,_money_evidence=money_raise_signals_eligible(db,a,max_age_seconds=900.0)
            if not _money_ok:continue
            ds=_load(db,a,"daily_stats:"+marketing_today_iso()) or {}; sp=ds.get("spending") if isinstance(ds.get("spending"),dict) else {}
            if str(sp.get("status") or "")!="ok":continue
            try:spent=float(sp.get("all_spend_rub"))
            except Exception:continue
            ratio=spent/budget; target_boost, expected_ratio, pacing_gap = _catchup_intensity(ratio, now_msk)
            if target_boost<=0:continue
            active=[x for x in (ds.get("items") or []) if isinstance(x,dict) and x.get("status")=="active"]
            # LATE_DAY_CURRENT_DAY_CONTACTS_ONLY_V1:
            # Same-day spend + lagged prior-day contacts must not create a fake
            # cheap CPL or a fake completed KPI after Moscow midnight.
            _item_signal_current = str(ds.get("stats_date") or "") == marketing_today_iso()
            contacts=sum(int(x.get("contacts") or 0) for x in active) if _item_signal_current else 0
            if not _item_signal_current:continue
            if contacts>=target:continue
            cpl=(spent/contacts) if contacts>0 else None
            if cpl is not None and cpl>red:continue
            if contacts<=0 and spent>=1.10*red:continue
            # Build durable winner memory from finalized bid experiments.
            # Latest verdict wins; an older success cannot override a newer loss.
            learning=_load(db,a,"cpx_learning_journal") or []
            latest_effect={}
            for e in reversed(learning if isinstance(learning,list) else []):
                if not isinstance(e,dict) or str(e.get("action") or "")!="raise":continue
                try:_iid=int(e.get("item_id"))
                except Exception:continue
                if _iid not in latest_effect:latest_effect[_iid]=str(e.get("effect") or "")
            winner_ids={iid for iid,eff in latest_effect.items() if eff=="improved"}
            # LATE_DAY_NEGATIVE_FEEDBACK_FENCE_V1: two consecutive completed
            # catch-up days with spend but no new contact pause future evening
            # acceleration for this account. A single bad day is not enough.
            # Normal KPI/bid learning remains active; this only fences the
            # optional late-day accelerator until a later helpful verdict exists.
            _late_learning=_load(db,a,LEARNING_KEY) or []
            _late_learning=[x for x in _late_learning if isinstance(x,dict)] if isinstance(_late_learning,list) else []
            _recent_verdicts=[str(x.get("verdict") or "") for x in _late_learning[-2:]]
            if len(_recent_verdicts)>=2 and all(v=="no_lead_after_boost" for v in _recent_verdicts):
                continue
            state=_load(db,a,STATE_KEY) or {}
            if str(state.get("day_msk") or "")!=now_msk.date().isoformat():state={"day_msk":now_msk.date().isoformat(),"status":"active","items":{}}
            # LATE_DAY_ONE_STEP_THEN_MEASURE_V2: no blind continuation cohort.
            # If both account measurement slots are occupied, one strong
            # converter may rotate its own >12h unresolved slot once/day under a
            # severe pacing gap + very strong CPL headroom. The replacement still
            # consumes the same slot and passes every final money guard.
            continuing_boost_ids=set()
            _pacing_advice=_late_day_pacing_advice(_load(db,a,"cpx_advice") or {}, winner_ids=winner_ids)
            rotation=_rotate_one_measurement_slot_for_pacing(
                db,a,_pacing_advice,state,now_msk,
                spend_ratio=ratio,expected_ratio=expected_ratio,
                contacts=contacts,target=target,cpl=cpl,red_cpl=red,
            )
            _clear_ids=set(rotation.get("rotated_item_ids") or [])
            pool=_eligible_pool(_pacing_advice, winner_ids=winner_ids,
                                continuing_boost_ids=continuing_boost_ids,
                                measurement_clear_ids=_clear_ids)
            if not pool:continue
            state.update({"status":"active","spend_plan_rub":budget,"spent_at_check_rub":round(spent,2),"contacts_at_check":contacts,"spend_ratio":round(ratio,4),"expected_spend_ratio":round(expected_ratio,4),"pacing_gap_ratio":round(pacing_gap,4),"target_uplift_pct":target_boost,"last_boost_check_msk":now_msk.isoformat()}); _save(db,a,STATE_KEY,state)
            cohort=max(1,min(MAX_ITEMS_PER_ACCOUNT_CYCLE,math.ceil(len(pool)*target_boost/100.0))); candidates=[]
            for x in pool:
                iid=str(x["id"]); meta=(state.get("items") or {}).get(iid) or {}; baseline=meta.get("baseline_bid_rub"); last=meta.get("last_bid_rub"); current=float(last if last is not None else x.get("bid_rub") or 0); achieved=((current/float(baseline)-1.0)*100.0) if baseline else 0.0
                if baseline and achieved>=target_boost-1.0:continue
                # STRICT_STEP10_LATE_DAY_V1: pacing intensity may rank/select a
                # proven converter, but one actual autonomous bid write is never
                # allowed to exceed the canonical 10% causal step. Measurement
                # must complete before any later step can be considered.
                step_pct=min(int(target_boost),10)
                if step_pct<=0:continue
                candidates.append((achieved,{**x,"step_pct":step_pct}))
            candidates.sort(key=lambda z:(z[0],not bool(z[1].get("winner_memory")),-int(z[1].get("contacts_7d") or 0)))
            applied=blocked=0; details=[]
            for _,x in candidates[:cohort]:
                iid=int(x["id"]); payload={"account_id":a,"item_id":iid,"action":"raise","actor_type":"boris_auto","trigger":"late_day_spend_catchup","source":"late_day_spend_catchup","max_bid_delta_pct":int(x.get("step_pct") or target_boost),"temporary_boost_target_pct":target_boost,"request_id":f"late-boost:{a}:{now_msk.date().isoformat()}:{now_msk.hour}:{iid}"}
                try:
                    r=_post("/api/cpx_advisor/apply_one",payload); body=r.json() if r.content else {}; status=str(body.get("status") or "")
                    _real_change = bool(status=="ok" and body.get("old_bid_rub") is not None and body.get("new_bid_rub") is not None and float(body.get("new_bid_rub"))>float(body.get("old_bid_rub")))
                    if _real_change:
                        applied+=1; meta=state.setdefault("items",{}).setdefault(str(iid),{});
                        if meta.get("baseline_bid_rub") is None:meta["baseline_bid_rub"]=body.get("old_bid_rub")
                        meta.update({"status":"boosting","last_bid_rub":body.get("new_bid_rub"),"target_uplift_pct":target_boost,"last_boost_at":datetime.now(timezone.utc).isoformat(),"steps":int(meta.get("steps") or 0)+1})
                    elif status=="ok" and body.get("idempotent"):
                        # LATE_DAY_SPEND_IDEMPOTENT_NO_FAKE_STEP_V1
                        details.append({"item_id":iid,"status":"idempotent","reason":"same_hour_request_already_applied"})
                    else:blocked+=1
                    details.append({"item_id":iid,"status":status,"reason":body.get("blocked_by") or body.get("reason_code") or body.get("reason"),"old":body.get("old_bid_rub"),"new":body.get("new_bid_rub")})
                except Exception as exc:blocked+=1; details.append({"item_id":iid,"status":"error","reason":f"{type(exc).__name__}:{str(exc)[:100]}"})
                state["updated_at"]=datetime.now(timezone.utc).isoformat(); _save(db,a,STATE_KEY,state)
            out.append({"account_id":a,"budget":budget,"spent":round(spent,2),"spend_ratio":round(ratio,3),"expected_spend_ratio":round(expected_ratio,3),"pacing_gap_ratio":round(pacing_gap,3),"contacts":contacts,"target_leads":target,"target_uplift_pct":target_boost,"pool":len(pool),"cohort":cohort,"measurement_slot_rotation":rotation,"applied":applied,"blocked":blocked,"details":details[:12]})
        finally:db.close()
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--reset-only',action='store_true'); ap.add_argument('--boost-only',action='store_true'); args=ap.parse_args(); now=datetime.now(MSK); reset=[]; learning=[]; boost=[]
    if not args.boost_only:reset=reset_previous_days(now); learning=learn_previous_days(now)
    if not args.reset_only:boost=boost_evening(now)
    print(json.dumps({"marker":"LATE_DAY_SPEND_CATCHUP_V1","now_msk":now.isoformat(),"reset":reset,"learning":learning,"boost":boost},ensure_ascii=False,default=str)); return 0
if __name__=='__main__':raise SystemExit(main())
