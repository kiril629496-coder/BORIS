from __future__ import annotations
import datetime as dt, json, math
from collections import defaultdict
from sqlalchemy.orm import Session
from app.director.models import DirectConnection, DirectEntity, DirectStat, DirectKpiConfig, DirectQueryStat, DirectSliceStat
from app.services.exception_observability import observe_suppressed

# BORIS AI Director knowledge policy. These are conservative operating rules,
# not promises about the auction. They intentionally prefer observation over
# frequent intervention when Yandex auto-strategies may still be learning.
KNOWLEDGE = {
    "conversion_strategy": {
        "min_weekly_conversions_for_learning": 10,
        "evaluation_window_days": [7, 14],
        "principle": "Не ломать конверсионную стратегию по одному плохому дню; сначала проверить объём конверсий, бюджет, цели и недавние изменения.",
    },
    "attribution": {
        "preferred_model": "AUTO",
        "principle": "Продажи и выручку оценивать с учётом задержки конверсии; NO_DATA не равно нулю.",
    },
    "autotargeting": {
        "principle": "Оценивать категории отдельно по фактическим запросам и качеству лидов; на поиске ЕПК не пытаться отключить автотаргетинг целиком.",
    },
    "negative_keywords": {
        "principle": "Минусовать только очевидно нерелевантный интент или подтверждённо убыточный запрос при достаточном объёме данных; не резать охват по единичным кликам.",
        "network_caveat": "Не считать минус-фразы универсальным фильтром для товарных объявлений в сетях: логика показа зависит от формата и места размещения.",
    },
    "budget": {
        "principle": "Увеличивать бюджет только при выполнении unit-экономики и наличии запаса по мощности продаж/производства; не превышать hard limit владельца.",
        "conversion_learning_rule": "Для конверсионной стратегии недельный бюджет должен позволять получить ориентировочно не менее 10 конверсий по целевой цене.",
        "weekly_budget_semantics": True,
    },
    "site_monitoring": {
        "principle": "До масштабирования убедиться, что сайт и формы доступны; падение посадочной трактовать как технический инцидент, а не как ухудшение рекламы."
    },
    "geo": {
        "principle": "Расширенный геотаргетинг меняет смысл охвата: запрос с названием региона может показываться пользователю вне выбранного региона. Проверять его отдельно по качеству лидов."
    },
    "experiments": {
        "principle": "Не менять одновременно стратегию, креатив, аудиторию и посадочную, если затем нужно понять причинность результата.",
    },
}


def _j(raw, default=None):
    try: return json.loads(raw or "")
    except Exception: return {} if default is None else default


def _sum(rows, attr):
    return sum(float(getattr(r, attr) or 0) for r in rows)


def _metrics(rows):
    cost=_sum(rows,"cost"); conv=_sum(rows,"conversions"); rev=_sum(rows,"revenue"); clicks=_sum(rows,"clicks"); impr=_sum(rows,"impressions")
    return {
        "impressions": int(impr), "clicks": int(clicks), "cost": round(cost,2),
        "conversions": round(conv,2), "revenue": round(rev,2),
        "ctr": round(clicks/impr*100,2) if impr else None,
        "cpc": round(cost/clicks,2) if clicks else None,
        "cpl": round(cost/conv,2) if conv else None,
        "roas": round(rev/cost,3) if cost else None,
    }


def diagnose(db: Session, account_id: str) -> dict:
    today=dt.date.today(); d7=str(today-dt.timedelta(days=6)); d14=str(today-dt.timedelta(days=13)); d30=str(today-dt.timedelta(days=29))
    rows=db.query(DirectStat).filter(DirectStat.account_id==account_id, DirectStat.day>=d30).all()
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    if not rows:
        return {"status":"waiting_data","score":None,"summary":"Недостаточно статистики для маркетинговой диагностики","signals":[],"campaigns":[],"search_queries":[],"knowledge":KNOWLEDGE}
    r7=[r for r in rows if r.day>=d7]; r14=[r for r in rows if r.day>=d14]
    m7=_metrics(r7); m14=_metrics(r14); m30=_metrics(rows)
    target_cpl=float((cfg.max_cpl or cfg.target_cpl) if cfg and (cfg.max_cpl or cfg.target_cpl) else 0)
    target_daily=float(cfg.target_leads_daily or 0) if cfg else 0
    signals=[]

    if m7["conversions"] < KNOWLEDGE["conversion_strategy"]["min_weekly_conversions_for_learning"]:
        signals.append({"code":"LOW_CONVERSION_VOLUME","severity":"attention","title":"Мало сигналов для обучения стратегии","detail":f"За 7 дней зафиксировано {m7['conversions']:g} конверсий. При малом объёме нельзя агрессивно менять автостратегию по краткосрочным колебаниям.","recommended_action":"Проверить качество цели, бюджет и ограничения охвата; изменения стратегии делать только после подтверждения причины."})
    if cfg and cfg.monthly_budget and target_cpl:
        weekly_budget=float(cfg.monthly_budget)/4.345
        recommended_floor=target_cpl*10
        if weekly_budget < recommended_floor:
            signals.append({"code":"LEARNING_BUDGET_TOO_LOW","severity":"attention","title":"Бюджета может не хватать для обучения конверсионной стратегии","detail":f"Ориентир недельного бюджета из месячного лимита — около {weekly_budget:.0f} ₽, а 10 целевых конверсий по {target_cpl:.0f} ₽ требуют около {recommended_floor:.0f} ₽.","recommended_action":"Не ужесточать CPA и таргетинги одновременно. Проверить фактическую стратегию и приоритет бизнес-цели до изменения бюджета."})

    if target_cpl and m7["cpl"] is not None and m7["cpl"] > target_cpl:
        ratio=m7["cpl"]/target_cpl
        signals.append({"code":"CPL_ABOVE_TARGET","severity":"critical" if ratio>=1.5 else "attention","title":"CPL выше цели","detail":f"CPL за 7 дней {m7['cpl']:.0f} ₽ при цели {target_cpl:.0f} ₽.","recommended_action":"Локализовать перерасход по кампаниям и поисковым запросам; не урезать весь аккаунт целиком."})
    if target_daily:
        expected=target_daily*7
        if m7["conversions"] < expected*0.7:
            signals.append({"code":"UNDERDELIVERY_7D","severity":"attention","title":"Не выполняется план объёма","detail":f"За 7 дней {m7['conversions']:g} конверсий при ориентире {expected:g}.","recommended_action":"Проверить потерю охвата, бюджет, модерацию, ставки/стратегию и конверсию посадочной."})
    if m7["clicks"] >= 30 and m7["conversions"] == 0:
        signals.append({"code":"TRAFFIC_WITHOUT_CONVERSIONS","severity":"critical","title":"Трафик есть, конверсий нет","detail":f"За 7 дней {m7['clicks']} кликов и 0 конверсий.","recommended_action":"Сначала проверить Метрику, цели, формы, телефонию и посадочную; только потом считать трафик плохим."})
    if m7["cost"] > 0 and m7["revenue"] == 0:
        signals.append({"code":"REVENUE_NOT_ATTRIBUTED","severity":"info","title":"Нет подтверждённой выручки","detail":"Расход есть, но выручка в Direct Reports не атрибутирована.","recommended_action":"Не делать вывод о прибыльности без CRM/Метрики; проверить передачу revenue и качество лидов."})

    campaigns=[]
    by=defaultdict(list)
    for r in rows: by[r.campaign_id].append(r)
    campaign_entities={e.external_id:e for e in db.query(DirectEntity).filter_by(account_id=account_id,entity_type="campaign").all()}
    names={k:v.name for k,v in campaign_entities.items()}
    for cid, cr in by.items():
        cm=_metrics([x for x in cr if x.day>=d7]); score=100
        raw=_j(campaign_entities[cid].state_json,{}) if cid in campaign_entities else {}
        start_date=raw.get("StartDate")
        learning_window=False
        try:
            learning_window=(today-dt.date.fromisoformat(str(start_date))).days < 14
        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=109)
        strategy=raw.get("UnifiedCampaign",{}).get("BiddingStrategy") or raw.get("TextCampaign",{}).get("BiddingStrategy") or raw.get("BiddingStrategy")
        if target_cpl and cm["cpl"] is not None:
            score-=min(55,max(0,(cm["cpl"]/target_cpl-1)*45))
        if cm["clicks"]>=20 and cm["conversions"]==0: score-=35
        if cm["clicks"]<5: score-=10
        campaigns.append({"campaign_id":cid,"name":names.get(cid) or cid,"score":max(0,round(score)),"start_date":start_date,"learning_window":learning_window,"strategy":strategy,**cm})
    campaigns.sort(key=lambda x:(x["score"],-x["cost"]))

    qrows=db.query(DirectQueryStat).filter(DirectQueryStat.account_id==account_id, DirectQueryStat.day>=d7).all()
    qby=defaultdict(list)
    for q in qrows: qby[(q.query,q.campaign_id,q.criterion_type or "")].append(q)
    queries=[]
    for (query,cid,ctype), qr in qby.items():
        cost=sum(float(x.cost or 0) for x in qr); conv=sum(float(x.conversions or 0) for x in qr); clicks=sum(int(x.clicks or 0) for x in qr)
        cpl=cost/conv if conv else None
        classification="OBSERVE"
        low=query.lower()
        obvious_negative=any(w in low for w in ("ваканси","работа ","своими руками","бесплатно","скачать","инструкция"))
        if obvious_negative and clicks>=2: classification="NEGATIVE_CANDIDATE"
        elif conv>0 and (not target_cpl or (cpl is not None and cpl<=target_cpl)): classification="PROVEN_VALUE"
        elif clicks>=10 and conv==0: classification="INEFFICIENT_CANDIDATE"
        queries.append({"query":query,"campaign_id":cid,"criterion_type":ctype,"clicks":clicks,"cost":round(cost,2),"conversions":round(conv,2),"cpl":round(cpl,2) if cpl is not None else None,"classification":classification})
    queries.sort(key=lambda x:(0 if x["classification"] in ("NEGATIVE_CANDIDATE","INEFFICIENT_CANDIDATE") else 1,-x["cost"]))

    score=100
    for s in signals:
        score-=25 if s["severity"]=="critical" else 12 if s["severity"]=="attention" else 3
    score=max(0,score)
    summary="Реклама работает стабильно" if score>=85 else "Есть точки роста" if score>=60 else "Нужна приоритетная оптимизация"
    return {"status":"ok","score":score,"summary":summary,"windows":{"days7":m7,"days14":m14,"days30":m30},"signals":signals,"campaigns":campaigns[:20],"search_queries":queries[:50],"knowledge":KNOWLEDGE}


# Business-funnel layer: intentionally read-only. It reuses BORIS CRM as the
# authoritative sales system and never creates a parallel CRM/qualification engine.
def crm_funnel_snapshot(db: Session, account_id: str) -> dict:
    from sqlalchemy import text
    acct=db.execute(text("SELECT owner_user_id FROM accounts WHERE account_id=:a LIMIT 1"), {"a":account_id}).mappings().first()
    if not acct or acct.get("owner_user_id") is None:
        return {"status":"NO_OWNER_SCOPE","attribution_quality":None,"leads":None,"qualified":None,"sales":None,"revenue":None,"cac":None,"cpql":None,"note":"У аккаунта нет owner scope для безопасной связи с CRM."}
    owner_id=int(acct["owner_user_id"])
    rows=db.execute(text("""
        SELECT id,status,source,source_ref,amount_kopeks,created_at
        FROM boris_crm_deals
        WHERE owner_user_id=:u AND created_at >= now() - interval '30 days'
          AND (lower(coalesce(source,'')) LIKE '%direct%' OR lower(coalesce(source,'')) LIKE '%yandex%')
    """), {"u":owner_id}).mappings().all()
    if not rows:
        return {"status":"NO_ATTRIBUTED_CRM_DATA","attribution_quality":0.0,"leads":0,"qualified":None,"sales":None,"revenue":None,"cac":None,"cpql":None,"note":"В CRM пока нет сделок, явно атрибутированных к Яндекс Директу. BORIS не будет приписывать Директу чужие лиды."}
    ids=[int(r["id"]) for r in rows]
    maturity={}
    if ids:
        acts=db.execute(text("""
            SELECT deal_id, lower(coalesce(body,'')) body, lower(coalesce(title,'')) title
            FROM boris_crm_activities WHERE deal_id = ANY(:ids)
        """), {"ids":ids}).mappings().all()
        for a in acts:
            txt=(a["title"]+" "+a["body"])
            m=maturity.setdefault(int(a["deal_id"]), {"qualified":False,"sale":False})
            if any(x in txt for x in ("qualified","квалифиц","target_action","целев")): m["qualified"]=True
            if any(x in txt for x in ("оплачен","продаж","успешно реализ","won")): m["sale"]=True
    qualified=0; sales=0; revenue=0.0
    for r in rows:
        st=str(r.get("status") or "").lower(); m=maturity.get(int(r["id"]),{})
        q=bool(m.get("qualified")) or st in {"qualified","target_action","won","success","closed_won"}
        sold=bool(m.get("sale")) or st in {"won","success","closed_won","paid"}
        qualified += 1 if q else 0; sales += 1 if sold else 0
        if sold: revenue += float(r.get("amount_kopeks") or 0)/100.0
    spend_rows=db.query(DirectStat).filter(DirectStat.account_id==account_id, DirectStat.day>=str(dt.date.today()-dt.timedelta(days=29))).all()
    spend=_sum(spend_rows,"cost")
    return {"status":"OK","attribution_quality":1.0,"leads":len(rows),"qualified":qualified,"sales":sales,"revenue":round(revenue,2),"spend":round(spend,2),"cpql":round(spend/qualified,2) if qualified else None,"cac":round(spend/sales,2) if sales else None,"romi":round((revenue-spend)/spend*100,2) if spend and revenue else None,"note":"CRM учитывает только сделки с явным source Яндекс Директа; неоднозначные лиды исключены."}


def business_diagnose(db: Session, account_id: str) -> dict:
    media=diagnose(db, account_id); crm=crm_funnel_snapshot(db, account_id)
    signals=list(media.get("signals") or [])
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    if crm.get("status")=="OK":
        leads=int(crm.get("leads") or 0); qualified=int(crm.get("qualified") or 0); sales=int(crm.get("sales") or 0)
        if leads>=10 and qualified/leads < 0.35:
            signals.append({"code":"LEAD_QUALITY_LOW","severity":"critical","title":"Директ приводит много слабых лидов","detail":f"Квалифицировано {qualified} из {leads} лидов за 30 дней.","recommended_action":"Оптимизировать запросы, оффер и таргетинг по качеству лидов, а не по CPL."})
        elif qualified>=8 and sales/qualified < 0.15:
            signals.append({"code":"SALES_CONVERSION_LOW","severity":"attention","title":"Реклама приводит целевых лидов, но продажи проседают","detail":f"Продаж {sales} из {qualified} квалифицированных лидов.","recommended_action":"Не душить Директ. Передать приоритет в МОП/РОП: скорость ответа, квалификация, оффер и дожим."})
        if cfg and cfg.target_cac and crm.get("cac") is not None and float(crm["cac"])>float(cfg.target_cac):
            signals.append({"code":"CAC_ABOVE_TARGET","severity":"critical","title":"Цена продажи выше бизнес-цели","detail":f"CAC {crm['cac']:.0f} ₽ при цели {float(cfg.target_cac):.0f} ₽.","recommended_action":"Перераспределять бюджет по продажам/марже, а не по дешёвым лидам."})
    else:
        signals.append({"code":"CRM_ATTRIBUTION_MISSING","severity":"info","title":"Оптимизация пока не видит продажи из Директа","detail":crm.get("note"),"recommended_action":"Передавать source/yclid/UTM в BORIS CRM; до этого не выдавать CPL за прибыльность."})
    media["signals"]=signals
    media["crm_funnel"]=crm
    media["optimization_level"]="PROFIT" if crm.get("status")=="OK" and crm.get("sales") else "QUALIFIED_LEADS" if crm.get("status")=="OK" and crm.get("qualified") else "LEADS"
    # Economics by advertising surface: search/RSA, device and actual user geo.
    since=str(dt.date.today()-dt.timedelta(days=29))
    slice_rows=db.query(DirectSliceStat).filter(DirectSliceStat.account_id==account_id, DirectSliceStat.day>=since).all()
    slice_out={}
    for dtype in ("network","device","geo","age","gender","ad_format","mobile_platform"):
        grouped=defaultdict(list)
        for row in slice_rows:
            if row.dimension_type==dtype: grouped[row.dimension_value].append(row)
        items=[]
        for value,rs in grouped.items():
            mm=_metrics(rs); items.append({"value":value,**mm})
        items.sort(key=lambda x:(x.get("cpl") is None, x.get("cpl") if x.get("cpl") is not None else 10**18, -x.get("cost",0)))
        slice_out[dtype]=items[:30]
    media["slices"]=slice_out
    return media


def negative_keyword_plan(db: Session, account_id: str) -> dict:
    """Build conservative negative-keyword candidates without mutating Direct."""
    since=str(dt.date.today()-dt.timedelta(days=13))
    rows=db.query(DirectQueryStat).filter(DirectQueryStat.account_id==account_id,DirectQueryStat.day>=since).all()
    grouped=defaultdict(list)
    for r in rows: grouped[(r.query,r.campaign_id,r.ad_group_id)].append(r)
    existing=[]
    for et in ("campaign","adgroup","negative_shared_set"):
        for e in db.query(DirectEntity).filter_by(account_id=account_id,entity_type=et).all():
            raw=_j(e.state_json,{})
            vals=raw.get("NegativeKeywords") or []
            if isinstance(vals,dict): vals=vals.get("Items") or []
            existing.extend(str(x).strip().lower() for x in vals if str(x).strip())
    candidates=[]
    intent_markers=("ваканси","работа ","резюме","своими руками","бесплатно","скачать","инструкция","обучение","курс ")
    for (query,cid,gid),rs in grouped.items():
        clicks=sum(int(x.clicks or 0) for x in rs); cost=sum(float(x.cost or 0) for x in rs); conv=sum(float(x.conversions or 0) for x in rs)
        q=query.strip(); low=q.lower(); confidence=0.0; reason=[]
        if any(m in low for m in intent_markers): confidence+=0.72; reason.append("нерелевантный информационный/трудовой интент")
        if clicks>=5 and conv==0: confidence+=0.16; reason.append("5+ кликов без конверсии")
        if clicks>=10 and conv==0: confidence+=0.10; reason.append("10+ кликов без конверсии")
        if conv>0: confidence-=0.55; reason.append("есть конверсии — сильный запрет на минусацию")
        words=[w for w in low.replace('!','').split() if len(w)>2][:7]
        phrase=" ".join(words)
        already=any(x and (x==phrase or x in low) for x in existing)
        status="REJECT" if conv>0 or confidence<0.80 or not phrase else "CANDIDATE"
        candidates.append({"query":q,"campaign_id":cid,"ad_group_id":gid,"clicks":clicks,"cost":round(cost,2),"conversions":round(conv,2),"candidate":phrase,"confidence":round(max(0,min(confidence,0.99)),2),"already_covered":already,"status":"ALREADY_COVERED" if already else status,"reason":"; ".join(reason) or "недостаточно доказательств"})
    candidates.sort(key=lambda x:(x["status"]!="CANDIDATE",-x["confidence"],-x["cost"]))
    return {"status":"ok","policy":{"max_words":7,"shared_sets_per_user_max":30,"max_total_chars":4096,"requires_no_conversions":True,"min_confidence":0.80,"auto_write":False},"candidates":candidates[:100]}


def strategy_intelligence(db: Session, account_id: str) -> dict:
    """Explain strategy risks; strategy writes remain high-risk confirmation-only."""
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    strategies=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="strategy").all()
    rows=db.query(DirectStat).filter(DirectStat.account_id==account_id,DirectStat.day>=str(dt.date.today()-dt.timedelta(days=13))).all()
    total_conv=_sum(rows,"conversions"); total_cost=_sum(rows,"cost")
    out=[]
    for e in strategies:
        raw=_j(e.state_json,{})
        signals=[]
        if total_conv<10: signals.append("LOW_LEARNING_VOLUME")
        target=float((cfg.max_cpl or cfg.target_cpl) if cfg and (cfg.max_cpl or cfg.target_cpl) else 0)
        actual=total_cost/total_conv if total_conv else None
        if target and actual and actual>target*1.35: signals.append("CPA_ABOVE_TARGET")
        if raw.get("AttributionModel") not in (None,"AUTO"): signals.append("ATTRIBUTION_REVIEW")
        out.append({"id":e.external_id,"name":e.name,"status":e.status,"signals":signals,"raw":raw,"decision":"OBSERVE" if total_conv<10 else "REVIEW" if signals else "KEEP","risk":"HIGH","write_policy":"CONFIRM_ONLY"})
    return {"status":"ok","portfolio":out,"account_context":{"conversions_14d":round(total_conv,2),"cost_14d":round(total_cost,2)},"policy":{"strategy_changes_high_risk":True,"minimum_learning_conversions":10,"one_major_change_at_a_time":True}}


def scale_recovery_plan(db: Session, account_id: str) -> dict:
    """Rank safe scale and recovery opportunities without executing financial changes."""
    diag=business_diagnose(db,account_id)
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    max_cpl=float((cfg.max_cpl or cfg.target_cpl) if cfg and (cfg.max_cpl or cfg.target_cpl) else 0)
    items=[]
    for c in diag.get("campaigns") or []:
        cpl=c.get("cpl"); conv=float(c.get("conversions") or 0); clicks=int(c.get("clicks") or 0)
        if conv>=5 and cpl is not None and (not max_cpl or cpl<=max_cpl*0.85):
            items.append({"campaign_id":c["campaign_id"],"campaign":c["name"],"mode":"SCALE_CANDIDATE","risk":"MEDIUM","confidence":0.82,"reason":"Есть устойчивые конверсии и запас по CPL","max_change_percent":min(float(cfg.max_single_change_percent or 20) if cfg else 20,15.0)})
        elif clicks>=30 and conv==0:
            items.append({"campaign_id":c["campaign_id"],"campaign":c["name"],"mode":"RECOVERY","risk":"LOW","confidence":0.95,"reason":"Есть трафик без конверсий: сначала tracking/landing/query diagnosis","max_change_percent":0})
        elif max_cpl and cpl is not None and cpl>max_cpl*1.5 and conv>=3:
            items.append({"campaign_id":c["campaign_id"],"campaign":c["name"],"mode":"EFFICIENCY_RECOVERY","risk":"MEDIUM","confidence":0.85,"reason":"CPL существенно выше лимита при достаточном сигнале","max_change_percent":min(float(cfg.max_single_change_percent or 20) if cfg else 20,10.0)})
    return {"status":"ok","state":"EMERGENCY" if any(x["mode"]=="RECOVERY" for x in items) else "ATTENTION" if items else "NORMAL","items":items,"policy":{"budget_hard_limit":cfg.daily_hard_limit if cfg else None,"monthly_budget":cfg.monthly_budget if cfg else None,"never_scale_without_unit_economics":True,"never_change_during_insufficient_learning":True}}


def creative_intelligence(db: Session, account_id: str) -> dict:
    ads=db.query(DirectEntity).filter_by(account_id=account_id,entity_type="ad").all()
    issues=[]; accepted=0; rejected=0; unknown=0
    for e in ads:
        raw=_j(e.state_json,{})
        status=str(raw.get("Status") or e.status or "UNKNOWN")
        if status=="ACCEPTED": accepted+=1
        elif status=="REJECTED": rejected+=1
        else: unknown+=1
        reasons=[]
        top=str(raw.get("StatusClarification") or "").strip()
        if top: reasons.append(top)
        for block_name in ("TextAd","ResponsiveAd","DynamicTextAd","MobileAppAd"):
            block=raw.get(block_name) or {}
            if not isinstance(block,dict): continue
            for key,val in block.items():
                if isinstance(val,dict):
                    st=str(val.get("Status") or "")
                    cl=str(val.get("StatusClarification") or "").strip()
                    if st=="REJECTED" or cl: reasons.append(f"{key}: {cl or st}")
                elif isinstance(val,list):
                    for i,item in enumerate(val):
                        if isinstance(item,dict):
                            st=str(item.get("Status") or ""); cl=str(item.get("StatusClarification") or "").strip()
                            if st=="REJECTED" or cl: reasons.append(f"{key}[{i}]: {cl or st}")
        if status=="REJECTED" or reasons:
            text_block=raw.get("TextAd") or {}; resp=raw.get("ResponsiveAd") or {}
            issues.append({"ad_id":e.external_id,"campaign_id":raw.get("CampaignId"),"ad_group_id":raw.get("AdGroupId"),"type":raw.get("Type"),"subtype":raw.get("Subtype"),"status":status,"reasons":reasons[:10],"title":text_block.get("Title"),"text":text_block.get("Text"),"responsive_titles":len(resp.get("Titles") or []) if isinstance(resp,dict) else 0,"responsive_texts":len(resp.get("Texts") or []) if isinstance(resp,dict) else 0,"recommended_action":"FIX_AND_RESUBMIT" if status=="REJECTED" else "REVIEW_EXTENSION_MODERATION"})
    return {"status":"ok","summary":{"ads":len(ads),"accepted":accepted,"rejected":rejected,"unknown":unknown,"issues":len(issues)},"issues":issues[:100],"policy":{"never_rewrite_claims_without_knowledge_validation":True,"moderation_reason_is_authoritative":True,"creative_change_requires_measurement":True}}


def bid_modifier_optimizer(db: Session, account_id: str) -> dict:
    """Recommend conservative modifier changes from observed segment economics; never writes directly."""
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    target=float((cfg.max_cpl or cfg.target_cpl) if cfg and (cfg.max_cpl or cfg.target_cpl) else 0)
    since=str(dt.date.today()-dt.timedelta(days=13))
    rows=db.query(DirectSliceStat).filter(DirectSliceStat.account_id==account_id,DirectSliceStat.day>=since,DirectSliceStat.dimension_type.in_(["device","age","gender","geo"])).all()
    by=defaultdict(list)
    for r in rows: by[(r.dimension_type,r.dimension_value)].append(r)
    recommendations=[]
    for (dtype,value),rs in by.items():
        clicks=sum(int(x.clicks or 0) for x in rs); conv=sum(float(x.conversions or 0) for x in rs); cost=sum(float(x.cost or 0) for x in rs)
        if clicks<30: continue
        cpl=cost/conv if conv else None
        action="OBSERVE"; delta=0; confidence=0.55; reason="Недостаточно доказательств для изменения коэффициента"
        if conv>=5 and target and cpl is not None:
            ratio=cpl/target
            if ratio<=0.70:
                delta=10; action="INCREASE_CANDIDATE"; confidence=0.82; reason="Сегмент стабильно дешевле целевого CPL"
            elif ratio>=1.50:
                delta=-15; action="DECREASE_CANDIDATE"; confidence=0.86; reason="Сегмент существенно дороже целевого CPL"
            elif ratio>=1.20:
                delta=-10; action="DECREASE_CANDIDATE"; confidence=0.78; reason="Сегмент дороже целевого CPL"
        elif conv==0 and clicks>=50:
            delta=-15; action="DECREASE_CANDIDATE"; confidence=0.84; reason="50+ кликов без конверсии"
        recommendations.append({"dimension":dtype,"value":value,"clicks":clicks,"conversions":round(conv,2),"cost":round(cost,2),"cpl":round(cpl,2) if cpl is not None else None,"action":action,"recommended_delta_percent":delta,"confidence":confidence,"risk":"MEDIUM" if delta else "LOW","reason":reason})
    recommendations.sort(key=lambda x:(x["action"]=="OBSERVE",-x["confidence"],-x["cost"]))
    return {"status":"ok","recommendations":recommendations[:100],"policy":{"writes":False,"min_clicks":30,"min_conversions_for_cpl_change":5,"max_step_percent":15,"requires_strategy_compatibility_check":True,"requires_existing_modifier_or_safe_add_path":True}}


def budget_allocator(db: Session, account_id: str) -> dict:
    """Plan budget reallocation across campaigns without increasing approved total budget."""
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    since=str(dt.date.today()-dt.timedelta(days=13)); rows=db.query(DirectStat).filter(DirectStat.account_id==account_id,DirectStat.day>=since).all()
    by=defaultdict(list)
    for r in rows: by[r.campaign_id].append(r)
    names={e.external_id:e.name for e in db.query(DirectEntity).filter_by(account_id=account_id,entity_type="campaign").all()}
    target=float((cfg.max_cpl or cfg.target_cpl) if cfg and (cfg.max_cpl or cfg.target_cpl) else 0)
    scored=[]
    for cid,rs in by.items():
        clicks=sum(int(x.clicks or 0) for x in rs); conv=sum(float(x.conversions or 0) for x in rs); cost=sum(float(x.cost or 0) for x in rs); cpl=cost/conv if conv else None
        if clicks<20: quality="INSUFFICIENT"
        elif conv>=5 and cpl is not None and (not target or cpl<=target*0.85): quality="WINNER"
        elif clicks>=40 and conv==0: quality="LOSER"
        elif target and cpl is not None and cpl>=target*1.4 and conv>=3: quality="LOSER"
        else: quality="NEUTRAL"
        scored.append({"campaign_id":cid,"campaign":names.get(cid,cid),"clicks":clicks,"conversions":round(conv,2),"cost":round(cost,2),"cpl":round(cpl,2) if cpl is not None else None,"quality":quality})
    winners=[x for x in scored if x["quality"]=="WINNER"]; losers=[x for x in scored if x["quality"]=="LOSER"]
    moves=[]
    if winners and losers:
        donor_share=min(10.0,float(cfg.max_single_change_percent or 20) if cfg else 10.0)
        for i,l in enumerate(losers[:len(winners)]):
            w=winners[i%len(winners)]
            moves.append({"from_campaign_id":l["campaign_id"],"from_campaign":l["campaign"],"to_campaign_id":w["campaign_id"],"to_campaign":w["campaign"],"share_percent":donor_share,"risk":"MEDIUM","reason":"Перераспределить внутри утверждённого бюджета от слабой кампании к подтверждённо эффективной"})
    return {"status":"ok","campaigns":scored,"moves":moves,"policy":{"increase_total_budget":False,"respect_monthly_budget":True,"respect_daily_hard_limit":True,"max_reallocation_percent":min(10.0,float(cfg.max_single_change_percent or 20) if cfg else 10.0),"strategy_weekly_budget_preferred":True}}


def performance_forecast(db: Session, account_id: str) -> dict:
    """Forecast month outcome from MTD actuals; avoids intraday forecast if Yandex timezone is unknown."""
    import calendar
    conn=db.query(DirectConnection).filter_by(account_id=account_id).first()
    cfg=db.query(DirectKpiConfig).filter_by(account_id=account_id).first()
    today=dt.date.today(); start=today.replace(day=1); rows=db.query(DirectStat).filter(DirectStat.account_id==account_id,DirectStat.day>=str(start),DirectStat.day<=str(today)).all()
    elapsed=max(1,today.day); days_in_month=calendar.monthrange(today.year,today.month)[1]
    cost=_sum(rows,"cost"); conv=_sum(rows,"conversions"); revenue=_sum(rows,"revenue")
    factor=days_in_month/elapsed
    projected_cost=cost*factor; projected_conv=conv*factor; projected_revenue=revenue*factor
    target_daily=float(cfg.target_leads_daily or 0) if cfg else 0
    target_month=target_daily*days_in_month if target_daily else None
    monthly_budget=float(cfg.monthly_budget or 0) if cfg else 0
    return {"status":"ok" if rows else "NO_DATA","month":{"elapsed_days":elapsed,"days_in_month":days_in_month,"actual_cost":round(cost,2) if rows else None,"actual_conversions":round(conv,2) if rows else None,"projected_cost":round(projected_cost,2) if rows else None,"projected_conversions":round(projected_conv,2) if rows else None,"projected_revenue":round(projected_revenue,2) if rows else None,"target_conversions":round(target_month,2) if target_month is not None else None,"budget_limit":monthly_budget or None,"budget_overrun_forecast":bool(monthly_budget and projected_cost>monthly_budget) if rows else None,"plan_attainment_forecast_percent":round(projected_conv/target_month*100,1) if rows and target_month else None},"intraday":{"status":"AVAILABLE" if conn and conn.timezone else "NO_TIMEZONE","timezone":conn.timezone if conn else None,"note":"Часовой пояс Яндекс Директа получен; почасовой forecast можно включать после накопления hourly-report." if conn and conn.timezone else "Почасовой прогноз не строится без подтверждённого часового пояса Яндекс Директа."}}


def creative_fatigue(db: Session, account_id: str) -> dict:
    """Detect likely creative fatigue by comparing ad-level last 7d vs previous 7d."""
    today=dt.date.today(); d7=str(today-dt.timedelta(days=6)); d14=str(today-dt.timedelta(days=13)); prev_end=str(today-dt.timedelta(days=7))
    rows=db.query(DirectSliceStat).filter(DirectSliceStat.account_id==account_id,DirectSliceStat.dimension_type=="ad",DirectSliceStat.day>=d14).all()
    by=defaultdict(list)
    for r in rows: by[r.dimension_value].append(r)
    ads={e.external_id:e for e in db.query(DirectEntity).filter_by(account_id=account_id,entity_type="ad").all()}
    out=[]
    for adid,rs in by.items():
        curr=[x for x in rs if x.day>=d7]; prev=[x for x in rs if d14<=x.day<=prev_end]
        if not curr or not prev: continue
        cm=_metrics(curr); pm=_metrics(prev)
        if pm["clicks"]<20 or cm["clicks"]<20: continue
        signals=[]
        if pm["ctr"] and cm["ctr"] is not None and cm["ctr"]<pm["ctr"]*0.7: signals.append("CTR_DROP_30PCT")
        if pm["cpl"] and cm["cpl"] is not None and cm["cpl"]>pm["cpl"]*1.35: signals.append("CPL_GROWTH_35PCT")
        if pm["conversions"]>=3 and cm["conversions"]==0: signals.append("CONVERSIONS_DISAPPEARED")
        if signals:
            raw=_j(ads.get(adid).state_json,{}) if adid in ads else {}
            out.append({"ad_id":adid,"campaign_id":raw.get("CampaignId"),"ad_group_id":raw.get("AdGroupId"),"signals":signals,"previous_7d":pm,"current_7d":cm,"action":"CREATIVE_TEST_CANDIDATE","risk":"LOW","rule":"DO_NOT_REPLACE_WINNER_WITHOUT_TEST"})
    out.sort(key=lambda x:-len(x["signals"]))
    return {"status":"ok","fatigue_candidates":out[:50],"policy":{"minimum_clicks_each_window":20,"compare_windows_days":7,"write":False,"requires_experiment":True}}


def business_setup_plan(db: Session, account_id: str, brief: dict) -> dict:
    """Turn one client business brief into a deterministic advertising build plan.
    This does not publish anything: creation stays behind OAuth, validation and owner approval/guardrails.
    """
    company=str(brief.get("company") or brief.get("business") or "Компания").strip()
    products=brief.get("products") or brief.get("services") or []
    if isinstance(products,str): products=[x.strip() for x in products.replace(";",", ").split(",") if x.strip()]
    geo=brief.get("geo") or brief.get("regions") or []
    if isinstance(geo,str): geo=[x.strip() for x in geo.replace(";",", ").split(",") if x.strip()]
    audience=str(brief.get("audience") or "").strip()
    offer=str(brief.get("offer") or brief.get("usp") or "").strip()
    goal=str(brief.get("goal") or "LEADS").upper()
    budget=float(brief.get("monthly_budget") or 0)
    target_cpl=float(brief.get("target_cpl") or 0)
    missing=[]
    if not products: missing.append("products_or_services")
    if not geo: missing.append("geo")
    if not budget: missing.append("monthly_budget")
    if not target_cpl: missing.append("target_cpl")
    campaigns=[]
    for i,p in enumerate(products[:20],1):
        base={"name":f"{company} | {p} | Поиск","channel":"SEARCH","product":p,"geo":geo,"goal":goal,"budget_share_percent":round(70/max(1,len(products)),1),"strategy":"AUTO_CONVERSION_AFTER_GOAL_VALIDATION","status":"DRAFT"}
        campaigns.append(base)
        campaigns.append({**base,"name":f"{company} | {p} | РСЯ","channel":"NETWORK","budget_share_percent":round(30/max(1,len(products)),1)})
    return {"status":"NEEDS_INPUT" if missing else "READY_FOR_BUILD","missing":missing,"brief":{"company":company,"products":products,"geo":geo,"audience":audience,"offer":offer,"goal":goal,"monthly_budget":budget,"target_cpl":target_cpl},"plan":{"campaigns":campaigns,"campaign_count":len(campaigns),"semantic_tasks":["commercial_core","negative_seed","brand_separation","query_intent_groups"],"creative_tasks":["offer_variants","headlines","texts","sitelinks","images_for_network"],"measurement":["metrica_goal_validation","yclid_capture","utm_marking","calltracking","crm_attribution"],"launch_sequence":["validate_business_brief","validate_goals_and_tracking","build_semantics","build_campaigns","build_creatives","preflight_policy_check","owner_preview","publish","verify_api_state","collect_learning_data","optimize_to_business_result"]},"policy":{"publish":False,"fake_campaign_creation":False,"owner_preview_required":True,"financial_writes_require_connection":True}}


def result_influence_map(db: Session, account_id: str) -> dict:
    return {"status":"ok","loop":[
      {"stage":"1. Сбор факта","does":"Расход, показы, клики, запросы, устройства, гео, объявления, конверсии + CRM продажи.","cadence":"15–60 минут"},
      {"stage":"2. Диагноз","does":"Отделяет проблему трафика от сайта, качества лида и работы отдела продаж.","cadence":"после каждого обновления"},
      {"stage":"3. Решение","does":"Выбирает один измеримый рычаг: запросы, минус-фразы, бюджет, корректировки, креатив, стратегия, аудитория.","cadence":"по достаточности данных"},
      {"stage":"4. Безопасное действие","does":"Проверяет бюджет, стратегию, риск, лимит изменения, дубль действия и режим автономности.","cadence":"только после guardrails"},
      {"stage":"5. Проверка","does":"После API-записи перечитывает реальное состояние. API 200 сам по себе не считается успехом.","cadence":"сразу после действия"},
      {"stage":"6. Измерение результата","does":"Сравнивает CPL → квалификацию → CAC → продажи → выручку/ROMI и решает оставить, масштабировать или откатить.","cadence":"7/14/30 дней + аварийные сигналы"}
    ],"priority":"PROFIT > SALES > QUALIFIED_LEADS > LEADS > CONVERSIONS > CLICKS","promise":"BORIS влияет на управляемые рекламные факторы, но не обещает заранее конкретное число продаж: результат зависит также от спроса, предложения, сайта, цены и отдела продаж."}
