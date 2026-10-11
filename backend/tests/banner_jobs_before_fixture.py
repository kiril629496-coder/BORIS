# Historical source fixture. Full original SHA256 a7d74fab14ad4fb61998db15bd6c85c5637630e8e332c36e3ecd6f5327081399.

def _campaign_checkpoint(db, job, payload, stage, done, total=8, extra=None):
    p = dict(payload or {})
    p["stage"] = stage
    p["campaign_id"] = int(p.get("campaign_id") or 0)
    p["checkpoint_at"] = datetime.now(timezone.utc).isoformat()
    if extra:
        p.update(extra)
    job.payload_json = p
    job.progress_done = int(done)
    job.progress_total = int(total)
    db.commit()
    return p

def _run_campaign_banners(db, job, payload):
    """Regenerate campaign banners independently from XML/category readiness.

    Uses the authoritative Campaign workflow and account scope. This lets creative
    work continue even when Avito still waits for seller-only required facts.
    """
    C,campaign,user,allowed,account_id=_campaign_job_context(db,job,payload)
    _locked,_activation,_locked_ff=_campaign_mutation_locked(C,campaign)
    if _locked:
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,"upload_id":_activation.get("upload_id"),"message":"Кампания уже передана Avito; изменения заморожены до итогового отчёта."}, "awaiting_confirmation"
    all_ids=[int(x) for x in (payload.get("item_ids") or [])]
    rows=C._ff_items(db,campaign.id,allowed)
    if not all_ids:
        all_ids=[int(r.id) for r in rows]
    remaining=[int(x) for x in (payload.get("remaining") or all_ids)]
    total=max(1,len(all_ids)); done=total-len(remaining)
    if not remaining:
        return {"stage":"banners_done","campaign_id":campaign.id,"account_id":account_id,"done":total,"total":total}, "completed"
    # Premium FULL_AI finalizes Vision QA in groups of four. Keep the job
    # checkpoint at the same granularity so this durable worker does not
    # accidentally degrade the production path back to 1 image / 1 Vision call.
    chunk=remaining[:4]
    _chunk_key="-".join(str(int(x)) for x in chunk)
    _retry_key="chunk:"+_chunk_key
    _retry_generation=int((payload.get("retry_counts") or {}).get(_retry_key) or 0)
    _rows_by_id={int(r.id):r for r in rows}

    def _banner_passed(_iid):
        _row=_rows_by_id.get(int(_iid))
        if _row is None:
            return False
        _p=C._ff_payload(_row)
        return bool(
            str(_p.get("banner_url") or "").strip()
            and str(_p.get("banner_provider") or "").strip().lower()=="openai_full_ai_premium"
            and bool((_p.get("banner_qa") or {}).get("passed"))
        )

    # PARTIAL_BATCH_REGENERATION_V1:
    # Retry only items that still have no approved Premium banner. Successful
    # siblings stay bound and exactly-once. Every retry generation gets a new
    # idempotency suffix so a QA_FAILED artifact can actually be regenerated
    # instead of replaying the same failed paid image forever.
    _pending_chunk=[int(x) for x in chunk if not _banner_passed(x)]
    if not _pending_chunk:
        remaining=remaining[len(chunk):]
        done=total-len(remaining)
        _campaign_checkpoint(db,job,payload,"banners",done,total,{"remaining":remaining,"item_ids":all_ids})
        if not remaining:
            C._campaign_guardian_enqueue(db,campaign,user,account_id,"campaign_prepare")
            return {"stage":"banners_done","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":False,
                    "message":"Баннеры прошли контроль. BORIS проверяет объявления и Avito-фид."}, "completed"
        return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":True}, "retry"

    result=C.ff_workflow_banners(
        campaign.id,
        C.FFBannerBody(
            item_ids=_pending_chunk,
            goal="offer",
            force=False,
            idempotency_key=f"campaign_banners:{int(job.id)}:{_chunk_key}:r{_retry_generation}",
            confirmed_paid_items=len(_pending_chunk),
        ),
        db,
        user,
    )
    if result.get("status")=="billing_limit":
        _campaign_checkpoint(db,job,payload,"billing_limit",done,total,{"remaining":remaining,"item_ids":all_ids})
        return {"stage":"billing_limit","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,
                "unit":"banners","remaining":result.get("remaining",0),"pending_charge":result.get("pending_charge",0),
                "message":result.get("message") or "Достигнут лимит баннеров по тарифу."}, "awaiting_confirmation"
    if result.get("status")=="billing_pending":
        _campaign_checkpoint(db,job,payload,"billing_pending",done,total,{"remaining":remaining,"item_ids":all_ids})
        return {"stage":"billing_pending","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,
                "unit":"banners","pending_charge":result.get("pending_charge",0),"retryable":True,
                "message":result.get("message") or "BORIS повторно сверяет тарифный учёт без новой генерации."}, "retry"
    errors=result.get("errors") or []
    if errors:
        retries=dict(payload.get("retry_counts") or {})
        retries[_retry_key]=int(retries.get(_retry_key) or 0)+1
        blocked_item=int((errors[0] or {}).get("id") or _pending_chunk[0])
        # Re-read the chunk after ff_workflow_banners: siblings that passed QA
        # are already bound and must count toward truthful progress even while
        # one failed sibling is being regenerated on the next retry generation.
        _fresh_rows={int(r.id):r for r in C._ff_items(db,campaign.id,allowed) if int(r.id) in set(chunk)}
        _passed_now=0
        for _iid in chunk:
            _row=_fresh_rows.get(int(_iid))
            if _row is None:
                continue
            _p=C._ff_payload(_row)
            if (
                str(_p.get("banner_url") or "").strip()
                and str(_p.get("banner_provider") or "").strip().lower()=="openai_full_ai_premium"
                and bool((_p.get("banner_qa") or {}).get("passed"))
            ):
                _passed_now+=1
        _effective_done=min(total,done+_passed_now)
        if retries[_retry_key] <= 4:
            _campaign_checkpoint(db,job,payload,"banners",_effective_done,total,{"remaining":remaining,"item_ids":all_ids,"retry_counts":retries})
            return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,"done":_effective_done,"total":total,
                    "retryable":True,"batch_size":len(chunk),"retry_generation":retries[_retry_key],
                    "pending_in_chunk":max(0,len(chunk)-_passed_now)}, "retry"
        _campaign_checkpoint(db,job,payload,"banners",_effective_done,total,{"remaining":remaining,"item_ids":all_ids,"retry_counts":retries,"qa_blocked_item":blocked_item})
        return {"stage":"banners_qa_blocked","campaign_id":campaign.id,"account_id":account_id,"done":_effective_done,"total":total,
                "blocked_item":blocked_item,"batch_size":len(chunk),"pending_in_chunk":max(0,len(chunk)-_passed_now),
                "message":"Баннер не прошёл контроль качества после ограниченного числа безопасных перегенераций."}, "partial"
    remaining=remaining[len(chunk):]; done=total-len(remaining)
    _campaign_checkpoint(db,job,payload,"banners",done,total,{"remaining":remaining,"item_ids":all_ids})
    if not remaining:
        # Creative completion must continue automatically into the canonical
        # validation/XML preparation chain. No owner click is required.
        C._campaign_guardian_enqueue(db,campaign,user,account_id,"campaign_prepare")
        return {"stage":"banners_done","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":False,"message":"Баннеры прошли контроль. BORIS проверяет объявления и Avito-фид."}, "completed"
    return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,"done":done,"total":total,"retryable":True}, "retry"

def _continuation_progress_basis(result):
    """Stable continuation coordinates; bounded retry generations count as progress."""
    result = dict(result or {})
    return {
        "stage": str(result.get("stage") or ""),
        "done": int(result.get("done") or 0),
        "total": int(result.get("total") or 0),
        "pending_item_ids": sorted(
            [int(x) for x in (result.get("pending_item_ids") or []) if str(x).isdigit()]
        ),
        "unit": str(result.get("unit") or ""),
        "retry_generation": (
            int(result.get("retry_generation") or 0)
            if result.get("retry_generation") is not None
            else None
        ),
        "pending_in_chunk": (
            int(result.get("pending_in_chunk") or 0)
            if result.get("pending_in_chunk") is not None
            else None
        ),
        "batch_size": (
            int(result.get("batch_size") or 0)
            if result.get("batch_size") is not None
            else None
        ),
    }
