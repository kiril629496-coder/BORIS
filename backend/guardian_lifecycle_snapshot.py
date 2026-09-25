import json, math, subprocess, sys
from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.storage import Storage
from app.models.campaign_item import CampaignItem
from app.services.campaign_identity import is_canonical_writable
from app.services.control_plane_adapters_ext import marketing_service_entitlement

IDS=[
    "planetazayavki_65985","antonybatono_78732","prodazha_bytovok_25677",
    "mbroker_27859","evgeniy_peregorodki_12447","pbi_artashes_plitka_mo_83993",
    "vasyailchenko_82650","garik_plitka_mo_58647","td_sakura_boris_82421",
]
WARN_SECONDS=2*60*60
CRIT_SECONDS=6*60*60
TITLE_COHORT_PCT=20


def parse_dt(value):
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception: return None


def load_json(s, aid, key, default):
    row=s.query(Storage).filter(Storage.account_id==aid,Storage.key==key).order_by(Storage.id.desc()).first()
    if not row: return default
    try: return json.loads(row.value or "{}")
    except Exception: return default


def save_json(s, aid, key, payload):
    row=s.query(Storage).filter(Storage.account_id==aid,Storage.key==key).order_by(Storage.id.desc()).first()
    raw=json.dumps(payload,ensure_ascii=False,default=str)
    if row: row.value=raw
    else: s.add(Storage(account_id=aid,key=key,value=raw))


def title_health(s, aid, now):
    """Truthful title-cohort health: mapping capacity != mutation capacity.

    GUARDIAN_TITLE_IDENTITY_VS_MUTATION_CAPACITY_V1:
    publication_rejected_bound is exact identity evidence but not write authority.

    GUARDIAN_TITLE_MANAGED_SUPPLY_CAP_V1:
    the desired 20% cohort is capped only by a fresh official Autoload managed
    supply proof matching both current feed and current active Avito ids.
    """
    import hashlib
    day=now.date().isoformat()
    try:
        entitlement=marketing_service_entitlement(s,aid) or {}
    except Exception:
        entitlement={"state":"unknown","source":"entitlement_read_error","until":None}
    service_active=str(entitlement.get("state") or "") == "active"

    stats=load_json(s,aid,f"daily_stats:{day}",{})
    active=[x for x in (stats.get("items") or [])
            if isinstance(x,dict) and str(x.get("status") or "").lower()=="active"]
    active_ids={str(x.get("id") or "").strip() for x in active if str(x.get("id") or "").strip()}
    raw_target=max(1,math.ceil(len(active)*TITLE_COHORT_PCT/100.0)) if active else 0
    target=raw_target

    feed=load_json(s,aid,"feed_items",[])
    feed=feed if isinstance(feed,list) else []
    feed_ids={str(x.get("id") or x.get("Id") or "").strip()
              for x in feed if isinstance(x,dict)
              and str(x.get("id") or x.get("Id") or "").strip()}
    feed_fingerprint=hashlib.sha256(chr(10).join(sorted(feed_ids)).encode("utf-8")).hexdigest()
    active_ids_fingerprint=hashlib.sha256(chr(10).join(sorted(active_ids)).encode("utf-8")).hexdigest()

    rows=s.query(CampaignItem).filter(
        CampaignItem.account_id==aid,
        CampaignItem.avito_item_id.in_(list(active_ids) or ["__none__"])
    ).all()
    by={}
    for row in rows:
        by.setdefault(str(row.avito_item_id or ""),[]).append(row)

    def exact_identity_known(row):
        if row is None:
            return False
        fid=str(getattr(row,"feed_identity","") or "").strip()
        avito_id=str(getattr(row,"avito_item_id","") or "").strip()
        if not fid or not avito_id or fid not in feed_ids:
            return False
        if str(getattr(row,"identity_status","") or "") not in {
            "published_identity_bound","publication_rejected_bound"
        }:
            return False
        try:
            payload=json.loads(getattr(row,"payload_json","") or "{}")
        except Exception:
            return False
        ident=(payload or {}).get("identity") or {}
        if str(ident.get("feed_identity") or "").strip()!=fid:
            return False
        if str(ident.get("avito_item_id") or "").strip()!=avito_id:
            return False
        resolution=str(ident.get("resolution") or "").strip()
        if resolution=="publication_result":
            return True
        if resolution=="autoload_report":
            try:
                return int(ident.get("upload_id") or 0)>0
            except (TypeError,ValueError):
                return False
        return False

    exact_identity={
        iid for iid,group in by.items()
        if len(group)==1 and exact_identity_known(group[0])
    }
    revision_hold={
        iid for iid,group in by.items()
        if len(group)==1 and iid in exact_identity
        and str(getattr(group[0],"identity_status","") or "")=="publication_rejected_bound"
    }
    writable={
        iid for iid,group in by.items()
        if len(group)==1 and is_canonical_writable(group[0])
        and str(group[0].feed_identity or "").strip() in feed_ids
    }

    managed_supply_cap=None
    managed_supply_proof={"valid":False}
    try:
        mapping_state=load_json(s,aid,"kpi_mapping_recovery_state",{})
        ms=(mapping_state or {}).get("managed_supply") or {}
        checked=parse_dt((mapping_state or {}).get("checked_at"))
        proof_age=max(0.0,(now-checked).total_seconds()) if checked else None
        recoverable=max(0,int(ms.get("recoverable_active_exact_pairs") or 0))
        valid=bool(
            str(ms.get("policy_version") or "")=="AUTOLOAD_EXACT_MANAGED_SUPPLY_SNAPSHOT_V1"
            and str(ms.get("feed_fingerprint") or "")==feed_fingerprint
            and str(ms.get("active_ids_fingerprint") or "")==active_ids_fingerprint
            and int(ms.get("source_active_items") or -1)==len(active_ids)
            and proof_age is not None and proof_age<=8*3600
        )
        if valid:
            managed_supply_cap=max(recoverable,len(exact_identity))
            target=min(raw_target,managed_supply_cap)
        managed_supply_proof={
            "valid":valid,
            "recoverable_active_exact_pairs":recoverable,
            "effective_cap":managed_supply_cap,
            "proof_age_sec":round(proof_age,1) if proof_age is not None else None,
            "feed_fingerprint_match":str(ms.get("feed_fingerprint") or "")==feed_fingerprint,
            "active_ids_fingerprint_match":str(ms.get("active_ids_fingerprint") or "")==active_ids_fingerprint,
        }
    except Exception as exc:
        managed_supply_proof={"valid":False,"reason":"managed_supply_proof_unavailable",
                              "detail":type(exc).__name__}

    apply_log=load_json(s,aid,"kpi_apply_log",[])
    apply_log=apply_log if isinstance(apply_log,list) else []
    published_today=set(); observing=set(); active_title=set()
    active_states={"prepared","safe_feed_ready","mapping_wait","feed_applied",
                   "publishing","publish_requested","publish_failed","published"}
    terminal={"improved","worse","insufficient_data","no_clear_effect","kept","try_next_action"}
    for op in apply_log:
        if not isinstance(op,dict) or "title" not in (op.get("changes") or {}):
            continue
        iid=str(op.get("avito_item_id") or op.get("item_id") or "").strip()
        if not iid or iid not in active_ids:
            continue
        st=str(op.get("status") or ""); eff=str((op.get("effect") or {}).get("status") or "")
        ts=str(op.get("ts") or op.get("published_at") or "")
        if st=="published" and ts.startswith(day):
            published_today.add(iid)
        if st=="published" and eff=="waiting_observation_window":
            observing.add(iid)
        if st in active_states and not (st=="published" and eff in terminal):
            active_title.add(iid)

    prepared=set()
    for prow in s.query(Storage).filter(
        Storage.account_id==aid,Storage.key.like("kpi_prepare:%")
    ).all():
        try:
            data=json.loads(prow.value or "{}")
        except Exception:
            continue
        if str(data.get("action") or "")!="prepare_weak_titles":
            continue
        if str(data.get("status") or "") not in {
            "ready_for_ai","ai_running","proposal_ready","apply_in_progress"
        }:
            continue
        for item in data.get("items") or []:
            iid=str((item or {}).get("id") or "").strip() if isinstance(item,dict) else ""
            if iid and iid in active_ids:
                prepared.add(iid)

    covered=published_today|observing|active_title|prepared
    mapping_capacity_gap=max(0,target-len(exact_identity)) if service_active else 0
    mutation_capacity_gap=max(0,target-len(writable)) if service_active else 0
    capacity_gap=mapping_capacity_gap
    writable_target=min(target,len(writable)) if service_active else 0
    prepare_gap=max(0,writable_target-len(covered & writable)) if service_active else 0
    execution_gap=max(0,target-len((published_today|observing)&active_ids)) if service_active else 0

    if not service_active:
        severity="inactive_service"
    elif not active:
        severity="idle_no_active_inventory"
    elif mapping_capacity_gap:
        severity="degraded_mapping_capacity"
    elif mutation_capacity_gap and revision_hold:
        severity="revision_hold"
    elif mutation_capacity_gap:
        severity="degraded_mutation_capacity"
    elif prepare_gap:
        severity="needs_prepare"
    elif execution_gap:
        severity="working"
    else:
        severity="covered"

    payload={
        "version":2,"account_id":aid,"checked_at":now.isoformat(),"target_pct":TITLE_COHORT_PCT,
        "service_entitlement":entitlement,"service_active":service_active,
        "active_items":len(active),"raw_target_items":raw_target,"target_items":target,
        "managed_supply_cap":managed_supply_cap,"managed_supply_proof":managed_supply_proof,
        "exact_identity_active":len(exact_identity),"exact_writable_active":len(writable),
        "revision_hold_active":len(revision_hold),
        "published_today":len(published_today),"active_title_observations":len(observing),
        "active_title_apply":len(active_title),"prepared_title_items":len(prepared),
        "covered_work_items":len(covered),
        "capacity_gap":capacity_gap,"mapping_capacity_gap":mapping_capacity_gap,
        "mutation_capacity_gap":mutation_capacity_gap,"prepare_gap":prepare_gap,
        "execution_gap":execution_gap,"capacity_sufficient":mapping_capacity_gap==0,
        "mutation_capacity_sufficient":mutation_capacity_gap==0,
        "mutation_blocked_by_revision_state":bool(
            mutation_capacity_gap>0 and mapping_capacity_gap==0 and revision_hold
        ),
        "severity":severity,"owner_action_required":False,
    }
    save_json(s,aid,"guardian_title_cohort_health",payload)
    return payload


def budget_brake_self_heal(now, max_accounts=1):
    # GUARDIAN_BUDGET_BRAKE_SELF_HEAL_V1: when the hourly hard budget brake
    # already proved the safety cutoff (>=90% of the daily red line) but provider transport prevented removal,
    # retry only that lower-only safety action on the 5-minute guardian cadence.
    # Never scan/mutate healthy accounts and never raise bids here.
    detect=SessionLocal()
    candidates=[]
    try:
        rows=detect.query(Storage).filter(Storage.key=="cpx_budget_brake_runtime").all()
        for row in rows:
            try: data=json.loads(row.value or "{}")
            except Exception: continue
            status=str(data.get("status") or "")
            try: used=float(data.get("budget_used_pct") or 0)
            except Exception: used=0.0
            if used < 90.0 or status not in {"error","fail","needs_retry","deferred"}:
                continue
            candidates.append((str(row.account_id),used,status,str(data.get("checked_at") or "")))
    finally:
        detect.close()
    candidates.sort(key=lambda x:(-x[1],x[0]))
    results=[]
    for aid,used,status,checked_at in candidates[:max_accounts]:
        try:
            proc=subprocess.run(
                [sys.executable,"cpx_budget_brake.py","--apply","--account",aid],
                cwd="/root/BORIS/backend",capture_output=True,text=True,timeout=100,
            )
            tail=(proc.stdout or "").strip().splitlines()[-3:]
            results.append({"account_id":aid,"budget_used_pct":used,"before_status":status,
                            "rc":proc.returncode,"output_tail":tail})
        except subprocess.TimeoutExpired:
            results.append({"account_id":aid,"budget_used_pct":used,"before_status":status,
                            "rc":124,"output_tail":["budget_brake_retry_timeout"]})
        except Exception as exc:
            results.append({"account_id":aid,"budget_used_pct":used,"before_status":status,
                            "rc":125,"output_tail":[f"{type(exc).__name__}:{exc}"[:220]]})
    return {"checked_at":now.isoformat(),"candidates":len(candidates),"attempted":len(results),
            "max_accounts":max_accounts,"results":results}


def finalize_due_measurements(s, aid, now, max_items=50):
    # GUARDIAN_MEASUREMENT_SELF_HEAL_V1: DB-only bounded self-heal.
    # This never calls Avito mutation endpoints. It reuses the canonical
    # CPX measurement finalizer so due experiments are journaled and their
    # money locks are released automatically instead of waiting for the next
    # hourly marketer cycle.
    from app.api.cpx_advisor import _finalize_measure_state

    rows=s.query(Storage).filter(
        Storage.account_id==aid,
        Storage.key.like("cpx_measure:%:raise"),
    ).all()
    due=[]
    for row in rows:
        try: data=json.loads(row.value or "{}")
        except Exception: continue
        if str(data.get("status") or "") != "waiting_measurement": continue
        started=parse_dt(data.get("started_at"))
        if not started: continue
        # GUARDIAN_CANONICAL_CALENDAR_AGE_V1: match the CPX finalizer exactly.
        # The canonical lifecycle uses completed calendar dates, not a strict
        # 168-hour wall-clock delay, so guardian self-heal must become due on
        # the same date and never lag the hourly finalizer by almost a day.
        age_days=max(0,(now.date()-started.date()).days)
        if age_days < 7: continue
        due.append((started,row,data))
    due.sort(key=lambda x:x[0])
    results=[]
    for _,row,data in due[:max_items]:
        try:
            item_id=int(data.get("item_id") or 0)
        except Exception:
            item_id=0
        if not item_id: continue
        try:
            result=_finalize_measure_state(
                db=s,
                account_id=aid,
                item_id=item_id,
                action="raise",
            ) or {}
            results.append({
                "item_id":item_id,
                "status":result.get("status"),
                "effect":result.get("effect"),
            })
        except Exception as exc:
            results.append({"item_id":item_id,"status":"error","reason":f"{type(exc).__name__}:{exc}"[:240]})
    measured=sum(1 for r in results if r.get("status")=="measured")
    waiting=sum(1 for r in results if r.get("status")=="waiting_measurement")
    errors=sum(1 for r in results if r.get("status")=="error")
    return {
        "due_found":len(due),"attempted":len(results),"measured":measured,
        "waiting":waiting,"errors":errors,"max_items":max_items,
        "results":results[:20],
    }


def measurement_health(s, aid, now):
    try:
        entitlement=marketing_service_entitlement(s,aid) or {}
    except Exception:
        entitlement={"state":"unknown","source":"entitlement_read_error","until":None}
    service_active=str(entitlement.get("state") or "") == "active"
    rows=s.query(Storage).filter(Storage.account_id==aid,Storage.key.like("cpx_measure:%:raise")).all()
    waiting=[]
    for row in rows:
        try: data=json.loads(row.value or "{}")
        except Exception: continue
        if str(data.get("status") or "") != "waiting_measurement": continue
        started=parse_dt(data.get("started_at"))
        waiting.append((row.key,started))
    # GUARDIAN_CANONICAL_CALENDAR_AGE_V1: keep health age aligned with the
    # canonical CPX finalizer's completed-calendar-day semantics.
    ages=[max(0,(now.date()-dt.date()).days) for _,dt in waiting if dt]
    oldest_days=max(ages) if ages else 0
    aged_7d=sum(1 for x in ages if x>=7)
    limit=20
    saturated=bool(service_active and len(waiting)>=limit)
    # MEASUREMENT_DUE_BEATS_SATURATION_V2: a saturated queue is a safe money
    # brake, but once any measurement reaches the terminalization age the
    # actionable lifecycle state is finalization_due. This makes the guardian
    # surface the work that must be drained instead of hiding it behind the
    # generic saturated label.
    # GUARDIAN_HISTORICAL_MEASURE_CLEANUP_V1: finishing an already-started
    # measurement is DB-only historical bookkeeping, not delivery of a paid
    # marketing service and not a provider/money mutation. Therefore expired
    # entitlement must block NEW actions but must not freeze old learning locks.
    finalization_due=bool(aged_7d)
    if not service_active: severity="inactive_service"
    elif finalization_due: severity="finalization_due"
    elif saturated: severity="saturated"
    elif waiting: severity="measuring"
    else: severity="clear"
    payload={
        "version":2,"account_id":aid,"checked_at":now.isoformat(),
        "service_entitlement":entitlement,"service_active":service_active,
        "waiting_raise_measurements":len(waiting),"measurement_backlog_limit":limit,
        "measurement_backlog_saturated":saturated,"oldest_waiting_days":oldest_days,
        "waiting_at_or_over_7d":aged_7d,"finalization_due":finalization_due,
        "severity":severity,"owner_action_required":False,
    }
    save_json(s,aid,"guardian_measurement_backlog_health",payload)
    return payload


now=datetime.now(timezone.utc)
# Retry at most one previously proven over-budget safety brake before the
# read-only lifecycle snapshot. The retry itself owns its process/file lock.
try:
    _budget_heal=budget_brake_self_heal(now,max_accounts=1)
    if _budget_heal.get("attempted"):
        print("BUDGET_BRAKE_SELF_HEAL",json.dumps(_budget_heal,ensure_ascii=False,default=str))
except Exception as _budget_heal_exc:
    print("BUDGET_BRAKE_SELF_HEAL",json.dumps({"status":"error","reason":f"{type(_budget_heal_exc).__name__}:{_budget_heal_exc}"[:300]},ensure_ascii=False))
s=SessionLocal()
try:
    total_active=stale_warn=stale_crit=title_capacity_gap_accounts=title_prepare_gap_accounts=measurement_backlog_saturated_accounts=0
    for aid in IDS:
        data=load_json(s,aid,"kpi_execution_ledger",{})
        active=int(data.get("active_count") or 0); total_active+=active
        updated_at=data.get("updated_at"); dt=parse_dt(updated_at)
        age_s=max(0,int((now-dt).total_seconds())) if dt else None
        obligations=data.get("obligations") or []
        observation_wait=bool(active and obligations and all(str(o.get("status") or "")=="published" and str(o.get("effect_status") or "")=="waiting_observation_window" for o in obligations))
        provider_stalled=False
        if active:
            try:
                hist=load_json(s,aid,"kpi_apply_log",[]); active_ids={str(o.get("operation_id") or "") for o in obligations}
                provider_stalled=any(str(op.get("operation_id") or "") in active_ids and (op.get("publish_provider_stalled_at") or op.get("rollback_provider_stalled_at")) for op in hist if isinstance(op,dict))
            except Exception: provider_stalled=False
        severity="ok"
        if provider_stalled: severity="incident"; stale_crit+=1
        elif observation_wait: severity="observation"
        elif active and age_s is None: severity="critical"; stale_crit+=1
        elif active and age_s>=CRIT_SECONDS: severity="critical"; stale_crit+=1
        elif active and age_s>=WARN_SECONDS: severity="warning"; stale_warn+=1
        print("LIFECYCLE",aid,"active_count",active,"updated_at",updated_at,"age_seconds",age_s,"severity",severity,"obligations",json.dumps(obligations,ensure_ascii=False,default=str))
        try:
            th=title_health(s,aid,now)
            title_capacity_gap_accounts += int(th["capacity_gap"]>0)
            title_prepare_gap_accounts += int(th["prepare_gap"]>0)
            print("TITLE_COHORT_HEALTH",aid,json.dumps(th,ensure_ascii=False,default=str))
        except Exception as exc:
            print("TITLE_COHORT_HEALTH",aid,json.dumps({"status":"error","reason":f"{type(exc).__name__}:{exc}"[:300]},ensure_ascii=False))
        try:
            mh=measurement_health(s,aid,now)
            heal=None
            if bool(mh.get("finalization_due")):
                heal=finalize_due_measurements(s,aid,now,max_items=50)
                # Re-read after bounded DB-only finalization so the persisted
                # health state reflects the queue that remains, not the stale
                # pre-heal count.
                mh=measurement_health(s,aid,now)
                print("MEASUREMENT_SELF_HEAL",aid,json.dumps(heal,ensure_ascii=False,default=str))
            measurement_backlog_saturated_accounts += int(mh["measurement_backlog_saturated"])
            print("MEASUREMENT_BACKLOG_HEALTH",aid,json.dumps(mh,ensure_ascii=False,default=str))
        except Exception as exc:
            print("MEASUREMENT_BACKLOG_HEALTH",aid,json.dumps({"status":"error","reason":f"{type(exc).__name__}:{exc}"[:300]},ensure_ascii=False))
    s.commit()
    print("LIFECYCLE_SUMMARY","active",total_active,"warning",stale_warn,"critical",stale_crit,"title_capacity_gap_accounts",title_capacity_gap_accounts,"title_prepare_gap_accounts",title_prepare_gap_accounts,"measurement_backlog_saturated_accounts",measurement_backlog_saturated_accounts,"checked_at",now.isoformat())
finally:
    s.close()
