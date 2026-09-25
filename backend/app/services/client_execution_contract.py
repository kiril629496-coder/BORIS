"""Strict owner-command execution contract for client workspaces.

Business commands from a client workspace must produce command-scoped artifacts.
They must never be treated as completed merely because a coding/dev job exists,
and historical materials may be used only as source inputs, never as proof of a
new command result.
"""
from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text as sql_text

from app.models.storage import Storage

SCHEMA = "BORIS_EXECUTION_CONTRACT_V1"
JOB_KIND = "client_owner_contract"

_TEXT_MARKERS = (
    "текст", "объявлен", "копирайт", "описан", "заголов", "новые услуги",
)
_BANNER_MARKERS = ("баннер", "креатив", "картин", "изображен")
_BANNER_APPLY_MARKERS = (
    "примен", "постав", "добав", "замен", "прикреп", "опубликов", "на объявлен",
)
_BANNER_PREVIEW_MARKERS = (
    "пример", "покаж", "показ", "возьми из профиля", "есть красивые баннер", "которые уже есть",
)
_DEV_MARKERS = (
    "backend", "frontend", "api ", "endpoint", "код", "баг", "ошибк в модул",
    "интерфейс", "страниц", "компонент", "таблиц", "миграц", "тесты",
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def contract_storage_key(command_id: str) -> str:
    return f"owner_execution_contract:{str(command_id or '').strip()}"


def _loads(value: str | None, default):
    try:
        data = json.loads(value or "")
        return data
    except Exception:
        return default


def load_contract(db, account_id: str, command_id: str) -> dict | None:
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == contract_storage_key(command_id),
    ).order_by(Storage.id.desc()).first()
    data = _loads(row.value if row else None, None)
    return data if isinstance(data, dict) else None


def save_contract(db, contract: dict, *, update_thread: bool = True) -> dict:
    account_id = str(contract.get("account_id") or "")
    command_id = str(contract.get("command_id") or "")
    contract["updated_at"] = utcnow_iso()
    payload = json.dumps(contract, ensure_ascii=False)
    row = db.query(Storage).filter(
        Storage.account_id == account_id,
        Storage.key == contract_storage_key(command_id),
    ).order_by(Storage.id.desc()).first()
    if row:
        row.value = payload
    else:
        db.add(Storage(account_id=account_id, key=contract_storage_key(command_id), value=payload))
    if update_thread and contract.get("thread_id"):
        from app.models.control_plane import ControlWorkspaceThread
        thread = db.query(ControlWorkspaceThread).filter(
            ControlWorkspaceThread.id == str(contract["thread_id"])
        ).first()
        if thread:
            state = dict(thread.current_state_json or {})
            state["execution_mode"] = "client_operation"
            state["execution_contract"] = contract
            state["execution_contract_id"] = contract.get("execution_id")
            state["execution_contract_status"] = contract.get("status")
            state["executor"] = "client-operation"
            state["executor_status"] = contract.get("status")
            state["criteria_done"] = int(contract.get("criteria_done") or 0)
            state["criteria_total"] = int(contract.get("criteria_total") or 0)
            state["current_step"] = str(contract.get("current_step") or "Команда принята к фактическому исполнению")
            state["next_step"] = str(contract.get("next_step") or "BORIS выполняет обязательные результаты")
            # A business operation is not a development job. Clear stale dev refs
            # so the owner UI cannot accidentally present the old coding queue as
            # proof for this command.
            state["dev_job_id"] = None
            state["order_id"] = None
            state["order_status"] = None
            state["executor_wait_reason"] = contract.get("wait_reason")
            state["executor_blocker"] = contract.get("blocker")
            thread.current_state_json = state
            thread.status = "active" if contract.get("status") not in {"failed", "blocked"} else "blocked"
    db.flush()
    return contract


def _has_any(text: str, markers) -> bool:
    low = str(text or "").lower()
    return any(m in low for m in markers)


def is_client_operation(text: str, history_texts: list[str] | None = None) -> bool:
    current = str(text or "")
    low = current.lower()
    # Explicit client content/material commands win even if the user mentions a
    # screen while explaining where the result should be visible.
    if _has_any(low, _TEXT_MARKERS) or _has_any(low, _BANNER_MARKERS):
        return True
    if _has_any(low, _DEV_MARKERS):
        return False
    return False


def _latest_requested_count(current: str, history: list[str], *, default: int = 1) -> int:
    candidates = [current] + list(reversed(history or []))
    patterns = (
        r"(?:в количестве|количеств[ое]|сдела(?:й|ть)|нужно|надо)\s*(?:примерно\s*)?(\d{1,3})\s*(?:шт|штук|объяв|текст)",
        r"(\d{1,3})\s*(?:шт\.?|штук|объявлен|текст)",
    )
    for raw in candidates:
        low = str(raw or "").lower()
        for pat in patterns:
            m = re.search(pat, low)
            if m:
                return max(1, min(100, int(m.group(1))))
    return max(1, int(default))


def _preview_only_new_ads(text: str) -> bool:
    low = str(text or "").lower()
    return (
        ("показ" in low and "сначала" in low and ("добав" in low or "авито" in low))
        or "перед добавлением в авито" in low
        or "не публиков" in low
        or "не публикуй" in low
        or "не размещай" in low
        or "не запускай" in low
    )


def build_contract(*, thread_id: str, account_id: str, command_id: str,
                   command_text: str, history_texts: list[str] | None = None) -> dict:
    history = [str(x or "") for x in (history_texts or []) if str(x or "").strip()]
    merged = "\n".join(history[-5:] + [str(command_text or "")])
    wants_texts = _has_any(merged, _TEXT_MARKERS)
    wants_banners = _has_any(merged, _BANNER_MARKERS)
    text_count = _latest_requested_count(command_text, history, default=1) if wants_texts else 0
    merged_low = merged.lower()
    reuse_existing = wants_banners and any(x in merged_low for x in (
        "уже есть", "которые уже есть", "в профиле", "в кабинете", "старые баннер", "существующ",
    ))
    banner_apply_requested = wants_banners and _has_any(merged_low, _BANNER_APPLY_MARKERS)
    banner_preview_requested = wants_banners and (_has_any(merged_low, _BANNER_PREVIEW_MARKERS) or not banner_apply_requested)
    preview_only = _preview_only_new_ads(merged)
    requirements: list[dict[str, Any]] = []
    if wants_texts:
        requirements.append({
            "id": "texts",
            "kind": "text_ads",
            "label": "Новые тексты / объявления",
            "required_count": text_count,
            "completed_count": 0,
            "status": "pending",
            "artifact_ids": [],
            "must_be_created_after_command": True,
            "publish_allowed": not preview_only,
        })
    if wants_banners:
        if banner_preview_requested and not banner_apply_requested:
            requirements.append({
                "id": "banners",
                "kind": "banner_selection",
                "label": "Подобрать и показать релевантные баннеры",
                "required_count": 6,
                "completed_count": 0,
                "status": "pending",
                "artifact_ids": [],
                "source_policy": "reuse_existing" if reuse_existing else "existing_preferred",
                "must_be_created_after_command": True,
                "proof_policy": "new_command_scoped_selection_after_command",
            })
        else:
            requirements.append({
                "id": "banners",
                "kind": "banner_attachments",
                "label": "Релевантные баннеры на активных объявлениях",
                "required_count": None,
                "required_scope": "all_relevant_active",
                "completed_count": 0,
                "status": "pending",
                "artifact_ids": [],
                "source_policy": "reuse_existing" if reuse_existing else "existing_preferred",
                "must_be_created_after_command": True,
                "proof_policy": "new_pairing_or_application_after_command",
            })
    now = utcnow_iso()
    return {
        "schema": SCHEMA,
        "execution_id": "op_" + uuid.uuid4().hex[:16],
        "mode": "client_operation",
        "thread_id": str(thread_id),
        "account_id": str(account_id),
        "command_id": str(command_id),
        "command_text": str(command_text or ""),
        "command_at": now,
        "created_at": now,
        "updated_at": now,
        "status": "queued",
        "current_step": "Команда принята; BORIS обязан создать проверяемые результаты",
        "next_step": "Выполнить все обязательные артефакты и затем проверить фактический результат",
        "requirements": requirements,
        "artifacts": [],
        "criteria_done": 0,
        "criteria_total": len(requirements),
        "publication": {
            "new_ads_allowed": not preview_only,
            "new_ads_published": 0,
            "existing_listing_media_change_requested": wants_banners,
        },
        "strict_rules": {
            "historical_artifacts_never_count_as_new_result": True,
            "artifact_must_reference_command_id": True,
            "completion_requires_all_requirements": True,
            "completion_requires_evidence": True,
            "stale_dev_job_never_counts": True,
        },
        "evidence": [],
        "wait_reason": None,
        "blocker": None,
    }


def enqueue_contract_job(db, contract: dict, *, owner_user_id=None):
    from app.services import jobs
    count = 0
    for r in contract.get("requirements") or []:
        try:
            count += int(r.get("required_count") or 1)
        except Exception:
            count += 1
    row, _created = jobs.enqueue(
        db,
        JOB_KIND,
        payload={
            "thread_id": contract.get("thread_id"),
            "account_id": contract.get("account_id"),
            "command_id": contract.get("command_id"),
            "execution_id": contract.get("execution_id"),
        },
        owner_user_id=owner_user_id,
        account_id=contract.get("account_id"),
        fingerprint=f"client-owner-contract:{contract.get('thread_id')}:{contract.get('command_id')}",
        progress_total=max(1, count),
    )
    contract["job_id"] = int(row.id)
    contract["status"] = "queued" if str(row.status) in {"queued", "running", "validating"} else str(row.status)
    contract["current_step"] = "Исполнительный наряд создан; результат считается только по артефактам"
    save_contract(db, contract)
    return row


def _tokenize(value: str) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9]+", str(value or "").lower())
    stop = {"услуга","услуги","помощь","москва","москве","для","под","при","это","или","как","ваш","ваша","ваши","недвижимости","недвижимость","квартира","квартиры","квартир"}
    out=set()
    for w in words:
        if len(w) < 4 or w in stop:
            continue
        out.add(w[:7])
    return out


def infer_missing_services(db, account_id: str) -> list[str]:
    from app.models.account import Account
    account = db.query(Account).filter(Account.account_id == account_id).first()
    company = str(getattr(account, "company_description", "") or "")
    lines=[]
    for raw in company.splitlines():
        clean=re.sub(r"^[\s•·\-–—⁠]+", "", raw).strip(" ;")
        if len(clean) >= 4:
            lines.append(clean)
    feed_row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == "feed_items").first()
    feed = _loads(feed_row.value if feed_row else None, [])
    corpus = "\n".join(str((x or {}).get("title") or "") for x in feed if isinstance(x,dict))
    corpus_tokens=_tokenize(corpus)
    missing=[]
    for line in lines:
        toks=_tokenize(line)
        if not toks:
            continue
        # A service is represented if at least one distinctive service token is
        # already present in published/feed titles. This deliberately ignores
        # generic words such as квартира/недвижимость.
        if not (toks & corpus_tokens):
            missing.append(line)
    return missing


def _find_command_batch(db, account_id: str, command_id: str) -> dict | None:
    row=db.query(Storage).filter(Storage.account_id==account_id, Storage.key=="gen_ads_history").first()
    history=_loads(row.value if row else None, [])
    for batch in history if isinstance(history,list) else []:
        if isinstance(batch,dict) and str(batch.get("command_id") or "") == str(command_id):
            return batch
    return None


def _persist_text_batch(db, contract: dict, ads: list[dict], topics: list[str], quality_report: dict | None = None) -> dict:
    now=utcnow_iso(); account_id=contract["account_id"]; command_id=contract["command_id"]
    batch={
        "id": int(datetime.now(timezone.utc).timestamp()*1000),
        "command_id": command_id,
        "execution_id": contract.get("execution_id"),
        "task_id": contract.get("execution_id"),
        "created_at": now,
        "topic": "; ".join(topics) if topics else "owner client command",
        "source": "owner_execution_contract",
        "preview_only": not bool((contract.get("publication") or {}).get("new_ads_allowed")),
        "quality_report": quality_report or {},
        "ads": ads,
    }
    current=db.query(Storage).filter(Storage.account_id==account_id, Storage.key=="gen_ads").first()
    raw_ads=json.dumps(ads,ensure_ascii=False)
    if current: current.value=raw_ads
    else: db.add(Storage(account_id=account_id,key="gen_ads",value=raw_ads))
    hist=db.query(Storage).filter(Storage.account_id==account_id, Storage.key=="gen_ads_history").first()
    history=_loads(hist.value if hist else None, [])
    if not isinstance(history,list): history=[]
    history=[x for x in history if not (isinstance(x,dict) and str(x.get("command_id") or "")==command_id)]
    history.insert(0,batch)
    raw_hist=json.dumps(history[:100],ensure_ascii=False)
    if hist: hist.value=raw_hist
    else: db.add(Storage(account_id=account_id,key="gen_ads_history",value=raw_hist))
    return batch


def _normalize_quality_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _text_quality_report(ads: list[dict], required: int) -> dict:
    """Strict acceptance for owner-requested ad texts.

    Quantity alone is not a result. Every accepted text must have a meaningful
    title, a substantive description, an explicit chat CTA and no exact duplicate
    title/description inside the command batch.
    """
    rows=[x for x in (ads or []) if isinstance(x,dict)][:max(0,int(required or 0))]
    issues=[]; titles=[]; descriptions=[]
    for idx,ad in enumerate(rows):
        title=str(ad.get("title") or ad.get("name") or "").strip()
        desc=str(ad.get("description") or ad.get("text") or ad.get("body") or "").strip()
        nt=_normalize_quality_text(title); nd=_normalize_quality_text(desc)
        titles.append(nt); descriptions.append(nd)
        row_issues=[]
        if len(title)<18: row_issues.append("title_too_short")
        if len(title)>80: row_issues.append("title_too_long")
        if len(desc)<550: row_issues.append("description_too_short")
        if len(desc)>1800: row_issues.append("description_too_long")
        if not any(x in nd for x in ("напишите", "сообщени", "чат")): row_issues.append("cta_missing")
        if row_issues:
            issues.append({"index":idx,"title":title[:100],"issues":row_issues,"description_length":len(desc)})
    if len(rows)<int(required or 0):
        issues.append({"batch":"count","issues":["insufficient_count"],"actual":len(rows),"required":int(required or 0)})
    if len([x for x in titles if x]) != len(set(x for x in titles if x)):
        issues.append({"batch":"titles","issues":["duplicate_titles"]})
    if len([x for x in descriptions if x]) != len(set(x for x in descriptions if x)):
        issues.append({"batch":"descriptions","issues":["duplicate_descriptions"]})
    return {"passed":not issues,"required":int(required or 0),"checked":len(rows),"issues":issues}


def _clean_fact_lines(value: str) -> list[str]:
    out=[]
    for raw in str(value or "").splitlines():
        clean=re.sub(r"^[\s•·\-–—⁠]+", "", raw).strip(" ;")
        if clean and clean not in out:
            out.append(clean)
    return out


def _deterministic_contract_texts(*, topics: list[str], required: int, company: str, clients: str) -> list[dict]:
    """Provider-independent fail-safe copywriter using only confirmed account facts."""
    service_lines=_clean_fact_lines(company)
    client_lines=_clean_fact_lines(clients)
    service_summary="; ".join(service_lines[:6]) or "услуги компании"
    client_summary="; ".join(client_lines[:6]) or "клиенты, которым подходит услуга"
    angles=(
        "разбор ситуации и понятный план действий",
        "подготовка без лишних обещаний и догадок",
        "сопровождение по шагам от первого вопроса до следующего действия",
        "проверка исходных данных до обращения или оформления",
        "помощь с выбором следующего практического шага",
    )
    title_suffixes=("разбор и сопровождение","консультация по ситуации","помощь по шагам","подготовка и сопровождение","разбор перед обращением")
    ads=[]
    stable_topics=[str(x or "").strip() for x in topics if str(x or "").strip()] or ["Услуги компании"]
    for i in range(max(0,int(required or 0))):
        topic=stable_topics[i % len(stable_topics)]
        base_title=re.sub(r"\s+", " ", topic).strip(" .;:-")
        suffix=title_suffixes[i % len(title_suffixes)]
        title=f"{base_title} — {suffix}"
        if len(title)>80:
            title=(base_title[:max(18,76-len(suffix))].rstrip()+" — "+suffix)[:80].rstrip(" -—")
        angle=angles[i % len(angles)]
        desc=(
            f"Если вам нужно направление «{base_title}», лучше начинать не с обещаний, а с разбора вашей конкретной ситуации. "
            f"Задача этой услуги — {angle}: уточнить цель, собрать исходные данные, определить, что уже готово и чего не хватает, а затем дать последовательность следующих действий. "
            f"По профилю клиента подтверждены направления: {service_summary}. Мы не подменяем решение банка, продавца, площадки или другой внешней стороны и не обещаем результат, который от нас не зависит. "
            f"Кому это может быть актуально по данным профиля: {client_summary}. Перед стартом уточняем именно вашу задачу и не добавляем условия, которых нет в исходных данных. "
            "В работе важны понятные шаги: сначала цель и вводные, затем проверка ограничений и документов, после этого — подготовка следующего действия и сопровождение по фактической ситуации. "
            "Напишите в чат, что именно хотите решить, на каком этапе находитесь и какие исходные данные уже есть. По сообщению можно начать с предметного разбора без общих обещаний."
        )
        ads.append({"title":title,"description":desc,"service_topic":topic,"source":"deterministic_owner_contract_fallback"})
    return ads


def _generate_texts_for_contract(db, contract: dict, required: int) -> tuple[list[dict], list[str], dict]:
    account_id=contract["account_id"]
    missing=infer_missing_services(db,account_id)
    from app.models.account import Account
    account=db.query(Account).filter(Account.account_id==account_id).first()
    company=str(getattr(account,"company_description","") or "")
    clients=str(getattr(account,"company_client_description","") or "")
    topics=missing or [x.strip() for x in company.splitlines() if x.strip()] or ["услуги компании"]
    # Do not pin a PostgreSQL connection while an external AI provider may take
    # tens of seconds. Holding the worker transaction here previously caused both
    # QueuePool starvation and stale SSL connections after the model returned.
    db.commit()
    # The strict owner contract must not depend on an external AI/provider to
    # produce the baseline result. Build a factual local draft first; external AI
    # is an optional enhancer only when explicitly enabled by operations.
    import os
    deterministic=_deterministic_contract_texts(
        topics=topics,required=required,company=company,clients=clients
    )
    deterministic_report=_text_quality_report(deterministic,required)
    external_enabled=str(os.environ.get("BORIS_OWNER_CONTRACT_EXTERNAL_AI","0") or "0").strip().lower() in {"1","true","yes","on"}
    if deterministic_report.get("passed") and not external_enabled:
        deterministic_report["provider_errors"]=[]
        deterministic_report["fallback_used"]=True
        deterministic_report["provider_mode"]="deterministic_default"
        return deterministic,topics,deterministic_report
    allocation=[]
    base=required//len(topics); rem=required%len(topics)
    for i,t in enumerate(topics):
        allocation.append((t,base+(1 if i<rem else 0)))
    ads=[]; provider_errors=[]
    from app.api.avito import GenerateAdsRequest, generate_ads
    for idx,(topic,count) in enumerate(allocation):
        if count<=0: continue
        result=generate_ads(GenerateAdsRequest(
            topic=topic,
            count=count,
            extra=(
                "Используй только подтверждённые факты клиента. Не обещай одобрение, ставку, срок, скидку, гарантию или бесплатность, если этого нет в фактах. "
                "Каждое описание должно быть содержательным, не короче 550 символов, с конкретными шагами и призывом написать в чат. "
                "Не публикуй: результат нужен владельцу на предварительную проверку. "
                f"Подтверждённые услуги: {company}. Целевая аудитория: {clients}."
            ),
            goal="message",
            use_my_ads=False,
            length="long",
            account_id=account_id,
            idempotency_key=f"owner-contract:{contract['command_id']}:texts:{idx}",
        ))
        if str(result.get("status") or "")!="ok":
            provider_errors.append(str(result.get("message") or "unknown")[:300])
            break
        for ad in result.get("ads") or []:
            if isinstance(ad,dict):
                item=dict(ad); item["service_topic"]=topic; ads.append(item)
    ads=ads[:required]
    report=_text_quality_report(ads,required)
    if not report.get("passed"):
        fallback=_deterministic_contract_texts(topics=topics,required=required,company=company,clients=clients)
        fallback_report=_text_quality_report(fallback,required)
        fallback_report["provider_errors"]=provider_errors
        fallback_report["fallback_used"]=True
        if not fallback_report.get("passed"):
            raise RuntimeError("text_quality_contract_failed:"+json.dumps(fallback_report,ensure_ascii=False)[:500])
        return fallback,topics,fallback_report
    report["provider_errors"]=provider_errors
    report["fallback_used"]=False
    return ads,topics,report


def _select_existing_banners(db, contract: dict, required: int) -> list[dict]:
    """Create a NEW command-scoped selection from existing banner assets.

    Old banner files are allowed as source material, but the artifact that counts
    is this selection, created after the owner command and linked to command_id.
    """
    from app.models.banner import Banner
    account_id=contract["account_id"]
    rows=(db.query(Banner).filter(Banner.account_id==account_id)
          .order_by(Banner.created_at.desc(),Banner.id.desc()).limit(max(1,required*4)).all())
    selected=[]
    seen=set()
    for b in rows:
        filename=str(getattr(b,"filename","") or "")
        if not filename.lower().endswith((".png",".jpg",".jpeg",".webp")):
            continue
        folder=str(getattr(b,"folder","") or "").strip("/")
        rel=f"/images/{account_id}/banners/{folder}/{filename}" if folder else f"/images/{account_id}/banners/{filename}"
        if rel in seen:
            continue
        seen.add(rel)
        selected.append({
            "banner_id": int(b.id),
            "url": rel,
            "theme_label": str(getattr(b,"theme_label","") or ""),
            "source_created_at": b.created_at.isoformat() if getattr(b,"created_at",None) else None,
            "source_is_historical": True,
            "selected_at": utcnow_iso(),
            "command_id": contract["command_id"],
        })
        if len(selected)>=required:
            break
    return selected


def _relevant_banner_pairings(db, contract: dict) -> tuple[list[dict], int, int]:
    """Create command-scoped pairings from existing banners and apply to feed.

    Historical banners are inputs only. The counted artifact is the new pairing
    and feed mutation created for THIS command.
    """
    from app.models.banner import Banner
    from app.api.avito import get_items2
    account_id=contract["account_id"]
    banners=(db.query(Banner).filter(Banner.account_id==account_id)
             .order_by(Banner.created_at.desc(),Banner.id.desc()).all())
    banners=[b for b in banners if str(getattr(b,"filename","") or "").lower().endswith((".png",".jpg",".jpeg"))]
    if not banners:
        return [],0,0
    live=get_items2(account_id)
    live_items=(live.get("resources") or live.get("items") or []) if isinstance(live,dict) else []
    live_active=[x for x in live_items if str((x or {}).get("status") or "active").lower()=="active"]
    if not live_active:
        raise RuntimeError("active_inventory_unavailable")
    live_titles=Counter(str((x or {}).get("title") or "").strip() for x in live_active)

    feed_row=db.query(Storage).filter(Storage.account_id==account_id,Storage.key=="feed_items").with_for_update().first()
    feed=_loads(feed_row.value if feed_row else None, [])
    if not isinstance(feed,list): feed=[]
    remaining=Counter(live_titles)
    live_feed=[]
    for item in feed:
        if not isinstance(item,dict): continue
        title=str(item.get("title") or "").strip()
        if remaining[title]>0:
            remaining[title]-=1; live_feed.append(item)
    # Include non-exact title variants only when a banner has positive semantic
    # overlap with the feed item itself; this keeps mortgage banners off unrelated ads.
    def _domain_flags(value: str) -> set[str]:
        low=str(value or "").lower()
        out=set()
        if any(x in low for x in ("ипотек", "кредит", "залог", "рефинанс", "консолидац")):
            out.add("finance")
        if any(x in low for x in ("подбор квартир", "подобрать жиль", "подбор жилья", "покупаем квартир", "покупайте квартир", "идеальной квартир")):
            out.add("apartment_selection")
        return out

    pairings=[]; changed=0
    for item in live_feed:
        item_text=str(item.get("title") or "")+" "+str(item.get("description") or "")[:800]
        itoks=_tokenize(item_text)
        item_domains=_domain_flags(item_text)
        best=None; best_score=0
        for b in banners:
            banner_text=str(getattr(b,"theme_label","") or "")+" "+str(getattr(b,"prompt","") or "")[:500]
            banner_domains=_domain_flags(banner_text)
            # Never put an apartment-selection creative onto a finance/mortgage ad.
            # Relevance beats filling every slot.
            if "finance" in item_domains and "finance" not in banner_domains:
                continue
            if "apartment_selection" in banner_domains and "finance" in item_domains:
                continue
            btoks=_tokenize(banner_text)
            score=len(itoks & btoks)
            if score>best_score:
                best_score=score; best=b
        if best is None or best_score<=0:
            continue
        rel=f"/images/{account_id}/banners/{best.folder}/{best.filename}"
        images=list(item.get("images") or [])
        before=list(images)
        images=[x for x in images if str(x)!=rel]
        images.insert(0,rel)
        item["images"]=images
        if images!=before: changed+=1
        pairings.append({
            "artifact_id": f"pair:{item.get('id')}:{best.id}",
            "command_id": contract["command_id"],
            "created_at": utcnow_iso(),
            "feed_item_id": str(item.get("id") or ""),
            "listing_title": str(item.get("title") or ""),
            "banner_id": int(best.id),
            "banner_url": rel,
            "banner_created_at": best.created_at.isoformat() if best.created_at else None,
            "source_is_historical": True,
            "semantic_score": best_score,
            "feed_applied": True,
        })
    if feed_row and changed:
        feed_row.value=json.dumps(feed,ensure_ascii=False)
    return pairings,len(pairings),changed


def _upsert_artifact(contract: dict, artifact: dict) -> None:
    aid=str(artifact.get("artifact_id") or "")
    kind=str(artifact.get("kind") or "")
    command_id=str(artifact.get("command_id") or "")
    # One owner command has one current proof row per requirement kind. Retries
    # may generate a newer batch, but stale proof rows must not survive beside it:
    # otherwise acceptance could accidentally combine quantity from one attempt
    # with quality/evidence from another attempt.
    singleton_kind=kind in {"text_ads","banner_selection","banner_attachments"} and bool(command_id)
    arts=[]
    for row in (contract.get("artifacts") or []):
        if not isinstance(row,dict):
            arts.append(row); continue
        same_id=str(row.get("artifact_id") or "")==aid
        same_scope=singleton_kind and str(row.get("kind") or "")==kind and str(row.get("command_id") or "")==command_id
        if same_id or same_scope:
            continue
        arts.append(row)
    arts.append(artifact)
    contract["artifacts"]=arts


def verify_contract(contract: dict) -> dict:
    done=0
    arts=contract.get("artifacts") or []
    for req in contract.get("requirements") or []:
        kind=req.get("kind")
        matching=[a for a in arts if isinstance(a,dict) and a.get("kind")==kind and str(a.get("command_id") or "")==str(contract.get("command_id") or "")]
        if kind=="text_ads":
            # Acceptance is atomic per artifact: quantity and quality must belong
            # to the SAME command-scoped proof row. Never combine count from one
            # retry with quality_passed from another retry.
            required=max(0,int(req.get("required_count") or 0))
            qualified=[a for a in matching if bool(a.get("quality_passed")) and int(a.get("count") or 0)>=required]
            quality_counts=[int(a.get("count") or 0) for a in matching if bool(a.get("quality_passed"))]
            count=max(quality_counts or [0])
            quality_ok=bool(qualified)
            req["completed_count"]=count if quality_ok else 0
            req["quality_passed"]=quality_ok
            req["status"]="done" if quality_ok else "pending"
        elif kind=="banner_selection":
            count=sum(int(a.get("count") or 0) for a in matching)
            required=max(1,int(req.get("required_count") or 1))
            req["completed_count"]=count
            req["status"]="done" if count>=required else "pending"
        elif kind=="banner_attachments":
            count=sum(int(a.get("count") or 0) for a in matching)
            required=req.get("required_count")
            req["completed_count"]=count
            # Banner source files can be old; only a command-scoped pairing/feed
            # application counts. Live Avito proof is tracked separately and must
            # not be fabricated from the local feed.
            local_ok=bool(required is not None and int(required or 0)>0 and count>=int(required or 0))
            live_ok=any(bool(a.get("live_verified")) for a in matching)
            req["status"]="done" if local_ok and live_ok else ("waiting_external" if local_ok else "pending")
        if req.get("status")=="done": done+=1
    contract["criteria_done"]=done
    contract["criteria_total"]=len(contract.get("requirements") or [])
    if done==contract["criteria_total"] and contract["criteria_total"]>0:
        contract["status"]="completed"
        contract["current_step"]="Все обязательные результаты подтверждены"
        contract["next_step"]="Нет — задача выполнена по строгому контракту"
    elif any(r.get("status")=="waiting_external" for r in contract.get("requirements") or []):
        contract["status"]="waiting_external"
        contract["current_step"]="Локальная работа выполнена; ждём независимое подтверждение Avito"
        contract["next_step"]="Проверить live Avito и только после этого закрыть задачу"
    elif contract.get("status") not in {"blocked","failed"}:
        contract["status"]="running"
    return contract


def _run_background_job_once(db, job, payload):
    account_id=str(payload.get("account_id") or job.account_id or "")
    command_id=str(payload.get("command_id") or "")
    contract=load_contract(db,account_id,command_id)
    if not contract:
        raise RuntimeError("execution_contract_missing")
    contract["status"]="running"
    contract["current_step"]="BORIS выполняет обязательные результаты"
    save_contract(db,contract); db.commit()

    # Texts: an exact command-scoped batch is idempotent proof. Anything older
    # or from another command cannot satisfy the requirement.
    for req in contract.get("requirements") or []:
        if req.get("kind")!="text_ads": continue
        required=int(req.get("required_count") or 0)
        batch=_find_command_batch(db,account_id,command_id)
        ads=list((batch or {}).get("ads") or [])
        quality_report=_text_quality_report(ads,required)
        if not quality_report.get("passed"):
            ads,topics,quality_report=_generate_texts_for_contract(db,contract,required)
            batch=_persist_text_batch(db,contract,ads,topics,quality_report)
            db.commit()
        else:
            # Legacy command-scoped batches can predate the quality field. Re-verify
            # from actual content instead of trusting missing/old metadata.
            if batch is not None:
                batch["quality_report"]=quality_report
        artifact={
            "artifact_id": f"texts:{(batch or {}).get('id')}",
            "kind":"text_ads","command_id":command_id,
            "created_at":(batch or {}).get("created_at") or utcnow_iso(),
            "count":len((batch or {}).get("ads") or []),
            "quality_passed":bool(quality_report.get("passed")),
            "quality_report":quality_report,
            "storage_key":"gen_ads_history","batch_id":(batch or {}).get("id"),
            "preview_only":bool((batch or {}).get("preview_only")),
        }
        _upsert_artifact(contract,artifact)
        req["artifact_ids"]=[artifact["artifact_id"]]
        contract["evidence"].append({"type":"db_artifact","key":"gen_ads_history","batch_id":artifact["batch_id"],"command_id":command_id})
        save_contract(db,contract); db.commit()

    for req in contract.get("requirements") or []:
        if req.get("kind")=="banner_selection":
            required=max(1,int(req.get("required_count") or 1))
            selected=_select_existing_banners(db,contract,required)
            artifact={
                "artifact_id": f"banner-selection:{command_id}",
                "kind":"banner_selection","command_id":command_id,
                "created_at":utcnow_iso(),"count":len(selected),
                "selected":selected,"source_policy":req.get("source_policy"),
            }
            _upsert_artifact(contract,artifact)
            req["artifact_ids"]=[artifact["artifact_id"]]
            contract["evidence"].append({"type":"banner_selection","count":len(selected),"command_id":command_id})
            save_contract(db,contract); db.commit()
            continue
        if req.get("kind")!="banner_attachments": continue
        pairings,count,changed=_relevant_banner_pairings(db,contract)
        req["required_count"]=count
        artifact={
            "artifact_id": f"banner-pairings:{command_id}",
            "kind":"banner_attachments","command_id":command_id,
            "created_at":utcnow_iso(),"count":count,
            "changed_feed_items":changed,"pairings":pairings,
            "source_policy":req.get("source_policy"),
            "live_verified":False,
        }
        _upsert_artifact(contract,artifact)
        req["artifact_ids"]=[artifact["artifact_id"]]
        contract["evidence"].append({"type":"feed_mutation","count":changed,"command_id":command_id})
        save_contract(db,contract); db.commit()

    verify_contract(contract)
    save_contract(db,contract); db.commit()
    result={
        "stage":"strict_contract",
        "execution_id":contract.get("execution_id"),
        "status":contract.get("status"),
        "done":int(contract.get("criteria_done") or 0),
        "total":int(contract.get("criteria_total") or 0),
        "message":contract.get("current_step"),
    }
    final="completed" if contract.get("status")=="completed" else ("retry" if contract.get("status")=="waiting_external" else "partial")
    return result,final


def _retryable_contract_error(exc: Exception) -> bool:
    low=(type(exc).__name__+": "+str(exc)).lower()
    return any(token in low for token in (
        "locknotavailable", "lock timeout", "canceling statement due to lock timeout",
        "timeout", "temporarily deferred", "providerdeferred", "rate limit", "429",
        "service unavailable", "503", "connection reset", "connection refused",
        "operationalerror", "ssl connection has been closed", "connection has been closed unexpectedly",
        "server closed the connection unexpectedly", "terminating connection due to administrator command",
    ))


def run_background_job(db, job, payload):
    """Execute one contract iteration and keep contract truth in sync with failures.

    A transient provider/DB lock is never allowed to leave the owner screen stuck
    on a fake `running` state. Retryable failures are requeued; terminal failures
    are written into the contract itself before the queue row is finalized.
    """
    try:
        return _run_background_job_once(db,job,payload)
    except Exception as exc:
        try:
            db.rollback()
        except Exception as rollback_exc:
            # Preserve the original execution error as authority; rollback failure
            # is secondary but must not disappear silently from release/runtime QA.
            from app.services.exception_observability import observe_suppressed
            observe_suppressed(__name__, rollback_exc, line=655)
        account_id=str(payload.get("account_id") or getattr(job,"account_id","") or "")
        command_id=str(payload.get("command_id") or "")
        contract=load_contract(db,account_id,command_id) if account_id and command_id else None
        retryable=_retryable_contract_error(exc)
        attempts=int(getattr(job,"attempts",0) or 0)
        if contract:
            contract["last_error"]={
                "type":type(exc).__name__,"message":str(exc)[:500],"at":utcnow_iso(),
                "retryable":retryable,"attempt":attempts,
            }
            if retryable and attempts < 12:
                contract["status"]="queued_retry"
                contract["current_step"]="Временный технический блокер; BORIS автоматически повторяет исполнение"
                contract["next_step"]="Повторить этот же наряд без участия владельца и продолжить с незакрытого результата"
                contract["wait_reason"]=type(exc).__name__+": "+str(exc)[:240]
                contract["blocker"]=None
            else:
                contract["status"]="failed"
                contract["current_step"]="Исполнение остановлено: подтверждён технический сбой"
                contract["next_step"]="Требуется системное исправление; готовым результатом задача не считается"
                contract["blocker"]=type(exc).__name__+": "+str(exc)[:300]
                contract["wait_reason"]=None
            save_contract(db,contract)
            db.commit()
        result={
            "stage":"strict_contract_retry" if retryable and attempts < 12 else "strict_contract_failed",
            "execution_id":contract.get("execution_id") if contract else payload.get("execution_id"),
            "status":contract.get("status") if contract else ("queued_retry" if retryable else "failed"),
            "done":int((contract or {}).get("criteria_done") or 0),
            "total":int((contract or {}).get("criteria_total") or 0),
            "message":str(exc)[:300],
        }
        return result,("retry" if retryable and attempts < 12 else "failed")


def _contract_reconciliation_decision(contract: dict, job_status: str, attempts: int = 0) -> str:
    """Return the fail-closed action required to keep queue state equal to truth.

    The artifact contract is authoritative. A queue row may never remain
    completed when acceptance criteria are not complete, and a fully proven
    contract must not be replayed merely because an old queue row says failed.
    """
    total=int(contract.get("criteria_total") or 0)
    done=int(contract.get("criteria_done") or 0)
    cstatus=str(contract.get("status") or "")
    jstatus=str(job_status or "")
    if total <= 0:
        return "fail_closed_zero_requirements"
    proven=bool(cstatus=="completed" and done==total)
    if proven:
        return "noop" if jstatus in {"running","completed"} else "complete_job"
    if jstatus=="completed":
        if cstatus in {"failed","blocked"}:
            return "fail_job"
        return "requeue_job" if int(attempts or 0) < 12 else "fail_job"
    if jstatus=="failed" and cstatus=="queued_retry" and int(attempts or 0) < 12:
        return "requeue_job"
    return "noop"


def reconcile_contract_truth_once(limit: int = 200) -> dict:
    """Guardian pass for owner execution contracts.

    This is intentionally idempotent and touches only non-running queue rows.
    It repairs contradictions such as job=completed while artifacts are still
    incomplete, and synchronizes proven contracts back to the owner workspace.
    """
    from app.db.session import SessionLocal
    from app.models.background_job import BackgroundJob

    db=SessionLocal()
    stats={"checked":0,"completed":0,"requeued":0,"failed_closed":0,"synced":0,"missing_contract":0,"errors":0}
    try:
        rows=(db.query(BackgroundJob)
              .filter(BackgroundJob.kind==JOB_KIND)
              .order_by(BackgroundJob.id.desc())
              .limit(max(1,min(int(limit or 200),1000))).all())
        for job in rows:
            stats["checked"]+=1
            try:
                payload=dict(job.payload_json or {})
                account_id=str(payload.get("account_id") or job.account_id or "")
                command_id=str(payload.get("command_id") or "")
                contract=load_contract(db,account_id,command_id) if account_id and command_id else None
                if not contract:
                    stats["missing_contract"]+=1
                    continue
                before=json.dumps(contract,ensure_ascii=False,sort_keys=True,default=str)
                verify_contract(contract)
                decision=_contract_reconciliation_decision(contract,str(job.status or ""),int(job.attempts or 0))
                now=datetime.now(timezone.utc)
                if decision=="complete_job":
                    if str(job.status or "")!="running":
                        job.status="completed"
                        job.error_text=None
                        job.scheduled_at=None
                        job.finished_at=now
                        job.result_json={
                            "stage":"strict_contract_guardian",
                            "execution_id":contract.get("execution_id"),
                            "status":"completed",
                            "done":int(contract.get("criteria_done") or 0),
                            "total":int(contract.get("criteria_total") or 0),
                            "message":"Все обязательные результаты подтверждены",
                        }
                        stats["completed"]+=1
                elif decision=="requeue_job":
                    if str(job.status or "")!="running":
                        job.status="queued"
                        job.error_text=None
                        job.started_at=None
                        job.finished_at=None
                        job.scheduled_at=now
                        contract["status"]="queued_retry"
                        contract["current_step"]="Guardian обнаружил незакрытые критерии; BORIS продолжает исполнение"
                        contract["next_step"]="Довести каждый обязательный результат до подтверждённой приёмки"
                        contract["wait_reason"]=None
                        contract["blocker"]=None
                        stats["requeued"]+=1
                elif decision in {"fail_job","fail_closed_zero_requirements"}:
                    if str(job.status or "")!="running":
                        job.status="failed"
                        job.scheduled_at=None
                        job.finished_at=now
                        reason=("Строгий контракт не содержит ни одного проверяемого требования"
                                if decision=="fail_closed_zero_requirements"
                                else "Очередь сообщала завершение без подтверждённой приёмки")
                        job.error_text=reason
                        contract["status"]="failed"
                        contract["current_step"]="Строгая приёмка остановила ложное завершение"
                        contract["next_step"]="Сформировать проверяемые требования и выполнить их; готовым результатом это не считается"
                        contract["blocker"]=reason
                        stats["failed_closed"]+=1
                after=json.dumps(contract,ensure_ascii=False,sort_keys=True,default=str)
                if before!=after or decision!="noop":
                    save_contract(db,contract)
                    stats["synced"]+=1
                db.commit()
            except Exception:
                db.rollback()
                stats["errors"]+=1
        return stats
    finally:
        db.close()


def sync_contract_from_job(db, state: dict) -> tuple[dict,bool]:
    command_id=str(state.get("last_command_id") or "")
    embedded=state.get("execution_contract") or {}
    account_id=str(state.get("account_id") or embedded.get("account_id") or "")
    contract=load_contract(db,account_id,command_id) if command_id and account_id else None
    if not contract:
        return state,False
    before=json.dumps(state.get("execution_contract") or {},ensure_ascii=False,sort_keys=True,default=str)
    state["execution_mode"]="client_operation"
    state["execution_contract"]=contract
    state["execution_contract_id"]=contract.get("execution_id")
    state["execution_contract_status"]=contract.get("status")
    state["executor"]="client-operation"
    state["executor_status"]=contract.get("status")
    state["criteria_done"]=contract.get("criteria_done")
    state["criteria_total"]=contract.get("criteria_total")
    state["current_step"]=contract.get("current_step")
    state["next_step"]=contract.get("next_step")
    state["executor_wait_reason"]=contract.get("wait_reason")
    state["executor_blocker"]=contract.get("blocker")
    after=json.dumps(contract,ensure_ascii=False,sort_keys=True,default=str)
    return state,before!=after
