"""Pinned original prepare body for AST-only regression extraction; never called against an app.
Source jobs.py SHA256 a7d74fab14ad4fb61998db15bd6c85c5637630e8e332c36e3ecd6f5327081399
"""

def _run_campaign_prepare(db, job, payload):
    """Persistent zero-touch campaign preparation. Safe to resume after tab/server restart."""
    if payload.get("content_mode") == "local_verified_v2":
        from app.services.local_content_worker import run as run_local_content
        return run_local_content(db, job, payload)
    C, campaign, user, allowed, account_id = _campaign_job_context(db, job, payload)
    _, _live_ff = C._ff_state_load(campaign)
    if C._ff_publication_locked(_live_ff):
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,
                "publication_status":str((_live_ff.get("owner_activation") or {}).get("status") or ""),
                "message":"Кампания уже передана Avito. Подготовка остановлена, BORIS только следит за итогом публикации."}, "completed"
    _locked, _activation, _locked_ff = _campaign_mutation_locked(C, campaign)
    if _locked:
        return {"stage":"publication_locked","campaign_id":campaign.id,"account_id":account_id,
                "upload_id":_activation.get("upload_id"),
                "message":"Кампания уже передана Avito. BORIS не меняет тексты/фото/баннеры до завершения отчёта публикации."}, "awaiting_confirmation"
    total = 8
    payload = _campaign_checkpoint(db, job, payload, "preflight", 0, total)
    _, ff = C._ff_state_load(campaign)
    if not ff:
        return {"stage":"needs_brief","message":"Сначала опишите, что нужно подготовить."}, "awaiting_confirmation"
    # Resolve direction/category intake FIRST. It has richer account/company evidence
    # and can satisfy newly introduced Avito fields automatically. Running the legacy
    # compact-question builder first would ask the owner for facts BORIS already knows.
    intake = C._ff_direction_intake(db, campaign, ff, C._ff_items(db, campaign.id, allowed))
    if not intake.get("all_complete"):
        return {"stage":"needs_input","intake":intake,"message":"Нужны обязательные факты Avito."}, "awaiting_confirmation"
    # Persist the exact trusted intake BORIS just resolved so downstream materialize
    # sees the same values in this very run. Without this, newly discovered official
    # fields could be resolved in the preflight view but disappear when materialize
    # reloads Feed Factory state, turning the owner into an unnecessary operator.
    _state=dict(ff.get("direction_intake") or {}); _intake_changed=False
    for _d in (intake.get("directions") or []):
        if not bool(_d.get("complete")):
            continue
        _direction=str(_d.get("direction") or "").strip()
        if not _direction:
            continue
        _entry={
            "direction":_direction,"template_id":str(_d.get("template_id") or ""),
            "category_path":str(_d.get("category_path") or ""),
            "contract_version":str(_d.get("contract_version") or ""),
            "mode":str(_d.get("mode") or "shared"),"answers":dict(_d.get("answers") or {}),
            "confirmed":True,"updated_at":datetime.now(timezone.utc).isoformat(),
            "confirmation_source":"zero_touch_trusted_evidence",
        }
        if _state.get(_direction)!=_entry:
            _state[_direction]=_entry; _intake_changed=True
            try:
                C._ff_profile_save(db,account_id,_direction,_entry)
            except Exception as _suppressed_exc:
                observe_suppressed(__name__, _suppressed_exc, line=875)
    if _intake_changed:
        ff["direction_intake"]=_state
        C._ff_state_save(db,campaign,ff)
    questions = C._ff_questions(db, campaign, ff)
    if questions:
        return {"stage":"needs_input","questions":questions,"message":"Нужны только неизвестные обязательные данные."}, "awaiting_confirmation"

    # ACCOUNT PORTFOLIO EVIDENCE: before copy generation, learn from the client's
    # own live Avito inventory regardless of whether BORIS created those ads.
    # This is read-only, account-scoped and cached in campaign references.
    refs=dict(ff.get("references") or {})
    if not str(refs.get("portfolio_evidence_text") or "").strip():
        try:
            from app.api.avito import get_items2
            # get_items2 performs external Avito HTTP. Persist/release the ORM
            # transaction first; keeping it idle across network latency causes
            # PostgreSQL idle-in-transaction kills and can poison the worker pool.
            db.commit()
            _portfolio=get_items2(account_id) or {}
            _items=list(_portfolio.get("resources") or [])
            _ranked=sorted(_items,key=lambda x:(int(x.get("contacts") or 0),float(x.get("conversion") or 0),int(x.get("views") or 0)),reverse=True)[:10]
            if _ranked:
                _lines=[]
                for _x in _ranked:
                    _lines.append("Avito ID %s | %s | цена %s ₽ | статус %s | просмотры %s | контакты %s | конверсия %s%% | %s" % (
                        _x.get("id"),str(_x.get("title") or "").strip(),_x.get("price") or 0,_x.get("status") or "",_x.get("views") or 0,_x.get("contacts") or 0,_x.get("conversion") or 0,_x.get("url") or ""))
                refs["portfolio_evidence_text"]="\n".join(_lines)
                refs["portfolio_evidence"]={"source":"Avito API","items":_ranked,"count":len(_ranked),"read_only":True}
                ff["references"]=refs; C._ff_state_save(db,campaign,ff)
        except Exception as _portfolio_exc:
            # Portfolio enrichment improves copy but must never crash unrelated clients.
            print("[campaign portfolio evidence] %s: %s" % (account_id,str(_portfolio_exc)[:200]),flush=True)

    # If the owner's brief explicitly requires competitor analysis, do it BEFORE texts.
    # Never silently replace a requested market audit with generic copy.
    _brief_low=str(ff.get("brief") or "").lower()
    refs=dict(ff.get("references") or {})
    _need_comp=("конкурент" in _brief_low and ("топ-10" in _brief_low or "топ 10" in _brief_low or "анализ" in _brief_low))
    from app.services.avito_content_policy import paid_competitor_audit_enabled as _content_paid_competitor_audit
    _need_paid_comp=bool(_need_comp and _content_paid_competitor_audit())
    if _need_comp and not _need_paid_comp and not (refs.get("competitor_audit") or refs.get("competitor_audit_text")):
        refs["competitor_audit_skipped"]={
            "reason":"local_first_paid_competitor_audit_disabled",
            "policy":"avito-content-autonomy-v1",
            "safe_alternative":"client_portfolio_and_verified_context",
        }
        ff["references"]=refs
        C._ff_state_save(db,campaign,ff)
    if _need_paid_comp and not (refs.get("competitor_audit") or refs.get("competitor_audit_text")):
        try:
            _rows=C._ff_items(db,campaign.id,allowed)
            _first=C._ff_payload(_rows[0]) if _rows else {}
            _query=str(_first.get("direction") or (_rows[0].segment if _rows else "") or "").strip()
            _city=str(_first.get("city") or (_rows[0].geo if _rows else "") or "Москва").strip()
            db.commit()
            _audit=C.ff_competitor_copywriter(campaign.id,C.FFCompetitorCopyBody(query=_query,city=_city,item_ids=[],apply=False),db,user)
            if int((_audit or {}).get("count") or 0)<3:
                return {"stage":"competitor_retry","retryable":True,"message":"BORIS повторно собирает реальную выдачу конкурентов перед созданием текстов."}, "retry"
            _,ff=C._ff_state_load(campaign)
        except Exception as _comp_exc:
            return {"stage":"competitor_retry","retryable":True,"message":"BORIS повторно собирает реальную выдачу конкурентов перед созданием текстов.","internal_reason":type(_comp_exc).__name__}, "retry"

    payload = _campaign_checkpoint(db, job, payload, "materialize", 1, total)
    mat = C.ff_workflow_materialize(campaign.id, C.FFMaterializeBody(account_id=account_id), db, user)
    _mat_status=str(mat.get("status") or "")
    if _mat_status == "billing_limit":
        return {"stage":"billing_limit","campaign_id":campaign.id,"account_id":account_id,
                "unit":"listings","remaining":mat.get("remaining",0),"pending_charge":mat.get("pending_charge",0),
                "message":mat.get("message") or "Достигнут лимит объявлений по тарифу."}, "awaiting_confirmation"
    if _mat_status == "billing_pending":
        return {"stage":"billing_pending","campaign_id":campaign.id,"account_id":account_id,
                "unit":"listings","pending_charge":mat.get("pending_charge",0),"retryable":True,
                "message":mat.get("message") or "BORIS повторно сверяет тарифный учёт. Повторно карточки не создаются."}, "retry"
    item_ids = [int(x) for x in (mat.get("item_ids") or [])]
    if not item_ids:
        # Imported/legacy campaigns can already contain complete cards while the
        # Feed Factory matrix is empty. Reuse those cards instead of failing the
        # interface job and asking the owner to recreate the campaign.
        item_ids=[int(r.id) for r in C._ff_items(db,campaign.id,allowed)]
        if item_ids:
            _,_ff=C._ff_state_load(campaign); _ff["item_ids"]=item_ids; C._ff_state_save(db,campaign,_ff)
    if not item_ids:
        raise RuntimeError("campaign has no materialized or existing items")

    preview = C.campaign_preview(campaign.id, db, user)
    existing = {int(x.get("id")):x for x in (preview.get("items") or [])}
    text_pending = [i for i in item_ids if not str((existing.get(i) or {}).get("title") or "").strip() or not str((existing.get(i) or {}).get("description") or "").strip()]
    payload = _campaign_checkpoint(db, job, payload, "texts", 2, total, {"item_ids":item_ids})
    if text_pending:
        # Generate in bounded chunks so a large 20–1000 item campaign keeps making
        # durable progress and never looks frozen while one giant request runs.
        chunk=text_pending[:5]
        text_result = C.ff_workflow_texts(
            campaign.id,
            C.FFTextBody(
                item_ids=chunk,
                goal="message",
                length="medium",
                idempotency_key=f"campaign_prepare:{int(job.id)}",
            ),
            db,
            user,
        )
        if text_result.get("errors"):
            raise RuntimeError("text generation failed for items: %s" % [x.get("id") for x in text_result.get("errors") or []])
        if len(text_pending)>len(chunk):
            return {"stage":"texts","campaign_id":campaign.id,"account_id":account_id,
                    "done":len(existing)-len(text_pending)+len(chunk),"total":len(item_ids),"retryable":True,
                    "message":"BORIS продолжает готовить объявления."}, "retry"

    preview = C.campaign_preview(campaign.id, db, user)
    existing = {int(x.get("id")):x for x in (preview.get("items") or [])}
    def _prepared_photo_count(item):
        return _campaign_prepared_photo_count(item)
    # The durable owner order is authoritative for gallery size. Legacy tasks
    # default to three semantic photos; an explicit brief such as "8 изображений
    # на каждое объявление" raises the exact contract up to the safe cap of eight.
    _, _ff_media = C._ff_state_load(campaign)
    _media_order = dict((_ff_media or {}).get("order_contract") or {})
    media_target=max(1,min(int(_media_order.get("media_per_item") or 3),8))

    # CAMPAIGN_MEDIA_OVERFLOW_NORMALIZE_V2:
    # Exact order means e.g. 8 ordinary photos + one later banner. An overfull
    # pre-banner stack must be normalized, not treated as missing forever.
    media_overfull=[i for i in item_ids if _prepared_photo_count(existing.get(i) or {}) > media_target]
    if media_overfull:
        from app.models.campaign_item import CampaignItem as _OverflowItem
        from app.models.media_link import MediaLink as _OverflowLink
        from app.models.media_asset import MediaAsset as _OverflowAsset
        _overflow_failed=[]
        for _iid in media_overfull:
            _row=db.query(_OverflowItem).filter(
                _OverflowItem.id==int(_iid), _OverflowItem.campaign_id==campaign.id
            ).first()
            _p=C._ff_payload(_row) if _row is not None else {}
            _mids=[]
            for _raw in (((_p.get("photo_stack") or {}).get("media_ids") or [])):
                try: _mid=int(_raw)
                except Exception: _mid=0
                if _mid and _mid not in _mids:
                    _mids.append(_mid)
            if len(_mids) < media_target and _row is not None:
                _pairs=(db.query(_OverflowLink,_OverflowAsset)
                        .join(_OverflowAsset,_OverflowAsset.id==_OverflowLink.media_id)
                        .filter(_OverflowLink.entity_type=="campaign_item",
                                _OverflowLink.entity_id==str(_iid),
                                _OverflowAsset.deleted_at.is_(None))
                        .order_by(_OverflowLink.position.asc(),_OverflowLink.id.asc()).all())
                _banner_url=str(_p.get("banner_url") or "").strip()
                for _lnk,_asset in _pairs:
                    _provider=str(getattr(_asset,"source_provider","") or "").lower()
                    _ref=str(getattr(_asset,"source_ref","") or "")
                    _asset_url="/images/"+str(getattr(_asset,"storage_key","") or "").lstrip("/")
                    _is_banner=(
                        (_provider in {"openai","boris_local"} and str(getattr(_lnk,"role","") or "")=="hero")
                        or _ref in {"campaign_openai_gpt_image_2","campaign_openai_visual_local_text_v1","campaign_stock_visual_local_text_v1"}
                        or bool(_banner_url and _asset_url==_banner_url)
                    )
                    if not _is_banner and int(_asset.id) not in _mids:
                        _mids.append(int(_asset.id))
            _mids=_mids[:media_target]
            if len(_mids) != media_target:
                _overflow_failed.append(int(_iid))
                continue
            _trim=C.ff_workflow_photo_stack(
                campaign.id,
                C.FFPhotoStackBody(
                    item_ids=[int(_iid)], media_ids=_mids,
                    photos_per_item=media_target, randomize=False, after_banner=True,
                ),
                db,
                user,
            )
            if str(_trim.get("status") or "")!="ok" or int(_iid) not in set(_trim.get("changed") or []):
                _overflow_failed.append(int(_iid))
        if _overflow_failed:
            return {
                "stage":"media_retry","campaign_id":campaign.id,"account_id":account_id,
                "pending_item_ids":_overflow_failed,"retryable":True,
                "message":"BORIS нормализует фотостек до точного количества заказа."
            }, "retry"
        preview=C.campaign_preview(campaign.id,db,user)
        existing={int(x.get("id")):x for x in (preview.get("items") or [])}

    media_pending = [i for i in item_ids if _prepared_photo_count(existing.get(i) or {}) < media_target]
    payload = _campaign_checkpoint(db, job, payload, "media", 3, total)
    if media_pending:
        # CAMPAIGN_DUPLICATE_QA_COST_ANOMALY_V1:
        # Stop spawning new paid QA work when this campaign has already consumed
        # far more Vision calls than its conservative unique-check envelope.
        _qa_guard=_campaign_qa_anomaly_snapshot(db,account_id,campaign.id,len(item_ids),media_target)
        if _qa_guard.get("media_anomaly"):
            record_event(db,job_module(job.kind),"duplicate_qa_cost_anomaly",
                         "BORIS остановил новые платные проверки: обнаружено аномально много повторных Vision-вызовов",
                         severity="warning",account_id=account_id,
                         details={"campaign_id":campaign.id,**_qa_guard},
                         trace_id=str(payload.get("_trace_id") or "") or None)
            return {
                "stage":"cost_anomaly","code":"DUPLICATE_QA_COST_ANOMALY",
                "campaign_id":campaign.id,"account_id":account_id,
                "unit":"media_qa","external_action":False,
                "provider_calls":_qa_guard["media_calls"],
                "expected_calls":_qa_guard["media_expected_calls"],
                "call_limit":_qa_guard["media_call_limit"],
                "cost_rub":_qa_guard["media_cost_rub"],
                "message":"BORIS остановил новые платные проверки фотографий: расход Vision заметно выше ожидаемого. Нужна автоматическая диагностика повторов перед продолжением."
            }, "awaiting_confirmation"
        # CAMPAIGN_MEDIA_DURABLE_SHARDS_V1:
        # Parent coordinates only; canonical child jobs perform media work in the
        # same background_jobs queue. Existing worker slots + SKIP LOCKED provide
        # real parallelism without a second scheduler or duplicate provider engine.
        import os as _os
        try:
            _shard_size=max(8,min(int(_os.environ.get("BORIS_CAMPAIGN_MEDIA_SHARD_SIZE","10") or 10),10))
        except Exception:
            _shard_size=10
        try:
            _max_shards=max(1,min(int(_os.environ.get("BORIS_CAMPAIGN_MEDIA_MAX_SHARDS","4") or 4),4))
        except Exception:
            _max_shards=4
        _children=_campaign_media_shard_jobs(db,account_id,campaign.id,int(job.id))
        _active=[x for x in _children if str(x.status or "") in {"queued","running","validating"}]
        _claimed=[]
        for _child in _active:
            for _raw in (dict(_child.payload_json or {}).get("item_ids") or []):
                try: _ci=int(_raw)
                except Exception: _ci=0
                if _ci>0 and _ci not in _claimed:
                    _claimed.append(_ci)
        _retry_window=_campaign_media_retry_window(media_pending,_children)
        _held=_retry_window["held_item_ids"]
        _slots=max(0,_max_shards-len(_active))
        _plan=_campaign_media_shard_plan(media_pending,_claimed+_held,shard_size=_shard_size,slots=_slots)
        if not _active and not _plan and _held:
            return {
                "stage":"media_shard_backoff","campaign_id":campaign.id,"account_id":account_id,
                "done":max(0,len(item_ids)-len(media_pending)),"total":len(item_ids),
                "pending_item_ids":[int(x) for x in media_pending],
                "held_item_ids":_held,"child_jobs_active":0,
                "retry_after_seconds":_retry_window["retry_after_seconds"],
                "diagnostic_code":"MEDIA_CHILD_NO_PROGRESS_BACKOFF",
                "retryable":True,"external_action":False,
                "message":"Повторные попытки подбора фотографий временно ограничены: предыдущие проходы не дали результата. BORIS сохранит прогресс и повторит проверку после паузы."
            }, "retry"
        _spawned=[]
        if _plan:
            _wave=int(payload.get("_media_shard_wave") or 0)+1
            payload["_media_shard_wave"]=_wave
            payload["_media_shard_size"]=_shard_size
            payload["_media_shard_limit"]=_max_shards
            job.payload_json=payload
            for _chunk in _plan:
                _cp={
                    "campaign_id":int(campaign.id),"account_id":account_id,
                    "item_ids":[int(x) for x in _chunk],
                    "media_target":int(media_target),
                    "_parent_campaign_prepare_job_id":int(job.id),
                    "_media_shard_wave":int(_wave),
                    "_priority":10,
                }
                _fp=_campaign_media_shard_fingerprint(campaign.id,int(job.id),_wave,_chunk)
                _child,_created=enqueue(
                    db,"campaign_media_shard",_cp,
                    owner_user_id=campaign.owner_user_id,account_id=account_id,
                    fingerprint=_fp,progress_total=len(_chunk),
                )
                _spawned.append(int(_child.id))
            db.commit()
        _active_ids=[int(x.id) for x in _active]+_spawned
        return {
            "stage":"media_shards","campaign_id":campaign.id,"account_id":account_id,
            "done":max(0,len(item_ids)-len(media_pending)),"total":len(item_ids),
            "pending_item_ids":[int(x) for x in media_pending],
            "child_jobs_active":len(_active_ids),
            "child_job_ids":_active_ids,
            "shard_size":_shard_size,"shard_limit":_max_shards,
            "retryable":True,
            "message":"BORIS параллельно подбирает и проверяет фотографии."
        }, "retry"

    preview = C.campaign_preview(campaign.id, db, user)
    existing = {int(x.get("id")):x for x in (preview.get("items") or [])}
    _, _ff_banner_gate = C._ff_state_load(campaign)
    _explicit_banner_required = _campaign_banner_required(_ff_banner_gate)
    banner_pending = []
    if _explicit_banner_required:
        for i in item_ids:
            x = existing.get(i) or {}
            has_openai = any(str(g.get("source_provider") or "").lower() == "openai" for g in (x.get("gallery") or []))
            if not str(x.get("banner") or "").strip() and not has_openai:
                banner_pending.append(i)
    payload = _campaign_checkpoint(db, job, payload, "banners", 4, total)
    if banner_pending:
        _qa_guard=_campaign_qa_anomaly_snapshot(db,account_id,campaign.id,len(item_ids),media_target)
        if _qa_guard.get("banner_anomaly"):
            record_event(db,job_module(job.kind),"duplicate_qa_cost_anomaly",
                         "BORIS остановил новые платные проверки баннеров: обнаружено аномально много повторных Vision-вызовов",
                         severity="warning",account_id=account_id,
                         details={"campaign_id":campaign.id,**_qa_guard},
                         trace_id=str(payload.get("_trace_id") or "") or None)
            return {
                "stage":"cost_anomaly","code":"DUPLICATE_QA_COST_ANOMALY",
                "campaign_id":campaign.id,"account_id":account_id,
                "unit":"banner_qa","external_action":False,
                "provider_calls":_qa_guard["banner_calls"],
                "expected_calls":_qa_guard["banner_expected_calls"],
                "call_limit":_qa_guard["banner_call_limit"],
                "cost_rub":_qa_guard["banner_cost_rub"],
                "message":"BORIS остановил новые платные проверки баннеров: расход Vision заметно выше ожидаемого. Нужна диагностика повторов перед продолжением."
            }, "awaiting_confirmation"
        # Per-item tariff/idempotency guards remain authoritative; only reduce
        # scheduler churn by processing a larger bounded durable wave.
        try:
            _banner_batch_size=max(3,min(10,int(__import__("os").environ.get("BORIS_CAMPAIGN_BANNER_BATCH_SIZE","10") or 10)))
        except Exception:
            _banner_batch_size=10
        banner_chunk=banner_pending[:_banner_batch_size]
        _confirmed_paid_items = len(banner_chunk) if _explicit_banner_required else 0
        _banner_intent_key = "campaign_prepare:%s:banners:%s" % (
            int(job.id),
            ",".join(str(int(x)) for x in banner_chunk),
        )
        banner_result = C.ff_workflow_banners(
            campaign.id,
            C.FFBannerBody(
                item_ids=banner_chunk,
                goal="offer",
                idempotency_key=_banner_intent_key,
                confirmed_paid_items=_confirmed_paid_items,
            ),
            db,
            user,
        )
        if banner_result.get("status")=="paid_confirmation_required":
            return {"stage":"banner_confirmation_required","campaign_id":campaign.id,"account_id":account_id,
                    "pending_item_ids":banner_chunk,"paid_images":banner_result.get("paid_images",0),
                    "message":banner_result.get("message") or "Для этой кампании требуется явное подтверждение платной генерации баннеров."}, "awaiting_confirmation"
        if banner_result.get("status")=="cost_cap_exceeded":
            return {"stage":"banner_cost_cap","campaign_id":campaign.id,"account_id":account_id,
                    "pending_item_ids":banner_chunk,"estimated_cost_rub":banner_result.get("estimated_cost_rub"),
                    "max_cost_rub":banner_result.get("max_cost_rub",4.0),
                    "message":banner_result.get("message") or "Генерация баннеров заблокирована лимитом стоимости."}, "awaiting_confirmation"
        if banner_result.get("status")=="billing_limit":
            return {"stage":"billing_limit","campaign_id":campaign.id,"account_id":account_id,
                    "unit":"banners","remaining":banner_result.get("remaining",0),
                    "pending_charge":banner_result.get("pending_charge",0),
                    "message":banner_result.get("message") or "Достигнут лимит баннеров по тарифу."}, "awaiting_confirmation"
        if banner_result.get("status")=="billing_pending":
            return {"stage":"billing_pending","campaign_id":campaign.id,"account_id":account_id,
                    "unit":"banners","pending_charge":banner_result.get("pending_charge",0),"retryable":True,
                    "message":banner_result.get("message") or "BORIS повторно сверяет тарифный учёт баннеров без новой генерации."}, "retry"
        if banner_result.get("errors"):
            # If banner+gallery semantic QA identified concrete bad stock, remove
            # only those links now and return to AUTO-MEDIA. Repeating BANNERS with
            # the same rejected gallery is a hot loop and can never self-heal.
            from app.models.media_link import MediaLink as _BL
            from app.models.campaign_item import CampaignItem as _BI
            import json as _bj
            _repair_ids=[]
            for _err in (banner_result.get("errors") or []):
                try: _iid=int(_err.get("id") or 0)
                except Exception: _iid=0
                if not _iid: continue
                _rr=db.query(_BI).filter(_BI.id==_iid).first()
                if not _rr: continue
                _pp=C._ff_payload(_rr)
                from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_fp
                _fp=_semantic_fp(_pp)
                _badids=set()
                for _rej in (_pp.get("media_semantic_rejections") or []):
                    if isinstance(_rej,dict) and str(_rej.get("content_fingerprint") or "")==_fp:
                        try: _badids.add(int(_rej.get("media_id") or 0))
                        except Exception as _suppressed_exc: observe_suppressed(__name__, _suppressed_exc, line=1057)
                if _badids:
                    db.query(_BL).filter(_BL.entity_type=="campaign_item",_BL.entity_id==str(_iid),_BL.media_id.in_(_badids)).delete(synchronize_session=False)
                    _pp["images"]=[]; _pp["media_semantic_evidence"]=[]; _pp["media_autocuration"]={"removed":len(_badids),"reason":"banner_semantic_mismatch_return_to_media"}
                    _rr.payload_json=_bj.dumps(_pp,ensure_ascii=False); _rr.hero_media_id=None; _rr.status="draft"; _repair_ids.append(_iid)
            if _repair_ids:
                db.commit()
                return {"stage":"media_retry","campaign_id":campaign.id,"account_id":account_id,"pending_item_ids":_repair_ids,"retryable":True,"message":"BORIS автоматически заменяет фотографии, которые не соответствуют объявлению."}, "retry"
            # Generated creative can occasionally fail visual QA; retry it rather
            # than terminating the entire client campaign.
            return {"stage":"banners","campaign_id":campaign.id,"account_id":account_id,
                    "pending_item_ids":[x.get("id") for x in banner_result.get("errors") or []],"retryable":True,
                    "message":"BORIS продолжает готовить и проверять баннеры."}, "retry"
        if len(banner_pending)>len(banner_chunk):
            # CAMPAIGN_BANNER_CHUNK_DURABLE_PROGRESS_V1:
            # A successful 3-item banner chunk is real progress even though the
            # workflow remains on the same stage. Recompute the remaining banner
            # scope and expose done/total/pending coordinates so run_once's
            # no-progress guard does not mistake successful chunks for a hot loop.
            _banner_preview_after=C.campaign_preview(campaign.id,db,user)
            _banner_existing_after={int(x.get("id")):x for x in (_banner_preview_after.get("items") or [])}
            _banner_remaining_after=[]
            for _iid in item_ids:
                _x=_banner_existing_after.get(int(_iid)) or {}
                _has_openai_after=any(
                    str(_g.get("source_provider") or "").lower()=="openai"
                    for _g in (_x.get("gallery") or [])
                )
                if not str(_x.get("banner") or "").strip() and not _has_openai_after:
                    _banner_remaining_after.append(int(_iid))
            _banner_done_after=max(0,len(item_ids)-len(_banner_remaining_after))
            return {
                "stage":"banners",
                "campaign_id":campaign.id,
                "account_id":account_id,
                "done":_banner_done_after,
                "total":len(item_ids),
                "pending_item_ids":_banner_remaining_after,
                "retryable":True,
                "message":"BORIS продолжает готовить баннеры.",
            }, "retry"

    payload = _campaign_checkpoint(db, job, payload, "internal_validation", 5, total)
    val = C.campaign_validation(campaign.id, db, user)
    if not val.get("ok"):
        # Text/category edits intentionally invalidate item-specific media proofs.
        # Refresh ONLY stale/missing semantic proofs against the current payload;
        # never bypass a genuine visual mismatch or low-confidence result.
        stale_codes={"media_semantic_proof_stale","media_semantic_proof_missing","media_semantic_link_payload_drift"}
        recuration_codes={"media_semantic_mismatch","media_semantic_low_confidence","media_semantic_unreviewed"}
        reports=list(val.get("items") or [])
        refresh_ids=[]
        safe_to_refresh=True
        for report in reports:
            errs=list(report.get("errors") or [])
            codes={str(e.get("code") or "") for e in errs}
            # Semantic mismatches are not accepted, but they ARE autonomously
            # repairable. Re-run/consume the current proof, persist negative memory,
            # unlink only rejected placements, then let the next media cycle refill
            # the exact 3-photo contract. Non-media errors still fail closed.
            if errs and codes and codes.issubset(stale_codes | recuration_codes | {"media_semantic_no_links"}):
                refresh_ids.append(int(report.get("id") or 0))
            elif errs:
                safe_to_refresh=False
        if refresh_ids and safe_to_refresh:
            from app.models.media_link import MediaLink
            from app.models.media_asset import MediaAsset
            from app.models.campaign_item import CampaignItem
            from app.services.media_semantic_validator import classify_item_media_vision, stamp_item_semantic_evidence
            from types import SimpleNamespace as _SNS
            import json as _json
            _campaign_id=int(campaign.id); _refresh_set=set(refresh_ids)
            for row in C._ff_items(db,_campaign_id,allowed):
                if int(row.id) not in _refresh_set: continue
                p=C._ff_payload(row); _row_id=int(row.id); _row_account=str(row.account_id)
                # MEDIA_LINK_PAYLOAD_RECONCILE_V1: payload.images is the concrete
                # publication source. If local image URLs changed while old MediaLinks
                # survived, rebuild links before semantic proof refresh. Never let a
                # stale green proof certify a different image list.
                from urllib.parse import unquote as _unquote
                def _payload_local_key(_g):
                    _raw=""; _storage=""
                    if isinstance(_g,dict):
                        _storage=str(_g.get("storage_key") or "").strip()
                        _raw=str(_g.get("url") or _g.get("image") or _g.get("src") or "").strip()
                    else:
                        _raw=str(_g or "").strip()
                    if _storage:
                        return _unquote(_storage.lstrip("/"))
                    if "/images/" in _raw:
                        return _unquote(_raw.split("/images/",1)[1].split("?",1)[0].lstrip("/"))
                    if _raw.startswith("images/"):
                        return _unquote(_raw[len("images/"):].split("?",1)[0].lstrip("/"))
                    return ""
                _payload_images=list(p.get("images") or [])
                _desired_keys=[]
                for _g in _payload_images:
                    _k=_payload_local_key(_g)
                    if not _k:
                        _desired_keys=[]
                        break
                    _desired_keys.append(_k)
                _current_linked=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                                 .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None))
                                 .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                _current_keys=[(int(_l.position),str(_a.storage_key or "").lstrip("/")) for _l,_a in _current_linked]
                _wanted_keys=[(int(_pos),str(_key)) for _pos,_key in enumerate(_desired_keys)]
                if _desired_keys and _current_keys!=_wanted_keys:
                    _desired_assets=[]
                    for _pos,_key in enumerate(_desired_keys):
                        _asset=(db.query(MediaAsset)
                                .filter(MediaAsset.account_id==_row_account,
                                        MediaAsset.storage_key==_key,
                                        MediaAsset.deleted_at.is_(None))
                                .order_by(MediaAsset.id.desc()).first())
                        if not _asset:
                            _desired_assets=[]
                            break
                        _desired_assets.append(_asset)
                    db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id)).delete(synchronize_session=False)
                    db.flush()
                    p["media_semantic_evidence"]=[]
                    if _desired_assets and len(_desired_assets)==len(_desired_keys):
                        for _pos,_asset in enumerate(_desired_assets):
                            db.add(MediaLink(entity_type="campaign_item",entity_id=str(_row_id),media_id=int(_asset.id),position=int(_pos)))
                        row.hero_media_id=int(_desired_assets[0].id) if _desired_assets else None
                        p["media_link_reconcile"]={"status":"relinked","placements":len(_desired_assets),"reason":"payload_image_changed"}
                    else:
                        row.hero_media_id=None
                        p["media_link_reconcile"]={"status":"needs_media_refill","placements":0,"reason":"payload_image_asset_unresolved"}
                    row.payload_json=_json.dumps(p,ensure_ascii=False)
                    row.content_hash=C.CS.content_hash(p)
                    db.flush()
                _existing_links=db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id)).count()
                if not _existing_links:
                    _gallery=[]
                    for _g in (p.get("gallery") or p.get("images") or []):
                        try:
                            _mid=int((_g.get("media_id") if isinstance(_g,dict) else _g) or 0)
                        except Exception:
                            _mid=0
                        if _mid and _mid not in _gallery: _gallery.append(_mid)
                    for _pos,_mid in enumerate(_gallery,1):
                        _asset=db.query(MediaAsset).filter(MediaAsset.id==_mid,MediaAsset.deleted_at.is_(None)).first()
                        if _asset:
                            db.add(MediaLink(entity_type="campaign_item",entity_id=str(_row_id),media_id=int(_mid),position=int(_pos)))
                    if _gallery: db.commit()
                links=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                       .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None))
                       .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                # External paid vision must not run inside the worker's ORM transaction.
                # Snapshot exact media identity/placement, close the read transaction,
                # validate, then re-load mutable rows only if the proof passes.
                detached=[(_SNS(position=int(l.position)),_SNS(id=int(a.id),storage_key=str(a.storage_key),tags=list(a.tags or []),account_id=str(a.account_id or ''),source_provider=str(a.source_provider or ''),source_ref=str(a.source_ref or ''))) for l,a in links]
                db.commit()
                try:
                    verdicts=classify_item_media_vision(p,detached,account_id=_row_account,provenance={"campaign_id":_campaign_id,"item_id":_row_id,"job_id":int(job.id),"source":"campaign_job_internal_validation"})
                except RuntimeError as _vision_exc:
                    _vmsg=str(_vision_exc or "")
                    if _vmsg.startswith("VISION_DAILY_CAP"):
                        raise ProviderDeferred("openai_vision_daily_cap", 3600) from _vision_exc
                    if _vmsg.startswith("VISION_BURST_CAP"):
                        raise ProviderDeferred("openai_vision_burst_cap", 300) from _vision_exc
                    if _vmsg.startswith("VISION_BUDGET_NOT_ALLOWED"):
                        raise ProviderDeferred("openai_vision_budget", 300) from _vision_exc
                    if _vmsg.startswith("VISION_BUDGET_RESERVE_FAILED") or _vmsg.startswith("VISION_DB_POOL_BUSY_RETRYABLE"):
                        raise ProviderDeferred("openai_vision_capacity", 60) from _vision_exc
                    raise
                bad=[v for v in verdicts if (not bool(v.get("match"))) or float(v.get("confidence") or 0)<0.80]
                row=db.query(CampaignItem).filter(CampaignItem.id==_row_id).first()
                fresh=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                       .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None))
                       .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                vby={(str(a.storage_key),int(l.position)):v for (l,a),v in zip(detached,verdicts)}
                if not row:
                    db.rollback(); continue
                if bad:
                    # Durable per-card negative memory. The next media cycle must not
                    # relink the same rejected stock assets or buy the same Vision
                    # verdict again for this unchanged advertisement.
                    from app.services.media_semantic_validator import semantic_content_fingerprint as _semantic_fp2
                    _fp=str(_semantic_fp2(p))
                    _rejections=[]
                    for _old in (p.get("media_semantic_rejections") or []):
                        if isinstance(_old,dict) and str(_old.get("content_fingerprint") or "")==_fp:
                            _rejections.append(_old)
                    _known_rej={int(x.get("media_id") or 0) for x in _rejections if str(x.get("media_id") or "").isdigit()}
                    for (_dl,_da),_dv in zip(detached,verdicts):
                        try: _dc=float(_dv.get("confidence") or 0)
                        except Exception: _dc=0.0
                        if (not bool(_dv.get("match"))) or _dc<0.80:
                            if int(_da.id) not in _known_rej:
                                _rejections.append({"media_id":int(_da.id),"content_fingerprint":_fp,"subject":str(_dv.get("subject") or "")[:180],"reason":str(_dv.get("reason") or "")[:240],"confidence":_dc})
                                _known_rej.add(int(_da.id))
                    p["media_semantic_rejections"]=_rejections[-40:]
                    # Autonomous fail-closed curation: unlink only the bad placement(s)
                    # from this card. Never delete the underlying asset because it may
                    # be shared by another campaign. Good placements keep their paid proof.
                    _kept=[]; _kept_verdicts=[]; _bad_link_ids=[]
                    for _l,_a in fresh:
                        _v=vby.get((str(_a.storage_key),int(_l.position)))
                        if _v is None:
                            _bad_link_ids.append(int(_l.id)); continue
                        if bool(_v.get("match")) and float(_v.get("confidence") or 0)>=0.80:
                            _kept.append((_l,_a)); _kept_verdicts.append(_v)
                        else:
                            _bad_link_ids.append(int(_l.id))
                    if not _kept:
                        # Every placement failed. Persist the negative memory first,
                        # then clear only this card's links/payload so the next prepare
                        # pass deterministically returns to AUTO-MEDIA. The underlying
                        # assets stay in the library for other cards/accounts.
                        db.query(MediaLink).filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id)).delete(synchronize_session=False)
                        p["images"]=[]; p["banner_url"]=""; p["banner_qa"]={}; p["media_semantic_evidence"]=[]
                        p["media_autocuration"]={"removed":len(fresh),"kept":0,"reason":"all_media_rejected_return_to_media"}
                        row.payload_json=_json.dumps(p,ensure_ascii=False); row.content_hash=C.CS.content_hash(p); row.hero_media_id=None; row.status="draft"
                        db.commit(); continue
                    if _bad_link_ids:
                        db.query(MediaLink).filter(MediaLink.id.in_(_bad_link_ids)).delete(synchronize_session=False)
                        db.flush()
                    _urls=[]
                    for _l,_a in _kept:
                        _key=str(_a.storage_key or "").strip()
                        if _key:
                            _urls.append(_key if _key.startswith(("http://","https://")) else "/images/"+_key.lstrip("/"))
                    p["images"]=_urls
                    _banner_url=str(p.get("banner_url") or "")
                    # If the banner itself survived semantic QA, keep it. Only clear
                    # banner state when its concrete media placement was rejected.
                    _kept_providers={str(getattr(_a,"source_provider","") or "").lower() for _l,_a in fresh}
                    _kept_keys={str(getattr(_a,"storage_key","") or "") for _l,_a in fresh}
                    _banner_survived=bool(_banner_url and ("openai" in _kept_providers or "boris_local" in _kept_providers or any("/banners/campaign_openai/" in k for k in _kept_keys)))
                    if _banner_url and not _banner_survived:
                        p["banner_url"]=""; p["banner_qa"]={}
                    # Re-load kept rows after DELETE so positions/ids are current and
                    # stamp only evidence that survived curation.
                    _kept_ids={int(_a.id) for _l,_a in _kept}
                    fresh=(db.query(MediaLink,MediaAsset).join(MediaAsset,MediaAsset.id==MediaLink.media_id)
                           .filter(MediaLink.entity_type=="campaign_item",MediaLink.entity_id==str(_row_id),MediaAsset.deleted_at.is_(None),MediaAsset.id.in_(_kept_ids))
                           .order_by(MediaLink.position.asc(),MediaLink.id.asc()).all())
                    _vbystorage={str(a.storage_key):v for (l,a),v in zip(_kept,_kept_verdicts)}
                    ordered=[_vbystorage.get(str(a.storage_key)) for l,a in fresh]
                    if any(v is None for v in ordered):
                        db.rollback(); continue
                    stamp_item_semantic_evidence(p,fresh,ordered)
                    p["media_autocuration"]={"removed":len(_bad_link_ids),"kept":len(fresh),"reason":"vision_mismatch_or_low_confidence"}
                else:
                    ordered=[vby.get((str(a.storage_key),int(l.position))) for l,a in fresh]
                    if any(v is None for v in ordered):
                        db.rollback(); continue
                    stamp_item_semantic_evidence(p,fresh,ordered)
                row.payload_json=_json.dumps(p,ensure_ascii=False); row.content_hash=C.CS.content_hash(p)
                db.commit()
            val=C.campaign_validation(_campaign_id,db,user)
        if not val.get("ok"):
            _post_codes={str(e.get("code") or "") for rr in (val.get("items") or []) for e in (rr.get("errors") or [])}
            _media_refill_codes={"photo_stack_incomplete","images_missing","media_semantic_no_links","media_semantic_proof_missing","media_semantic_proof_stale","media_semantic_link_payload_drift"}
            if _post_codes and _post_codes.issubset(_media_refill_codes):
                _pending=[int(rr.get("id") or 0) for rr in (val.get("items") or []) if rr.get("errors")]
                return {"stage":"media_retry","campaign_id":campaign.id,"account_id":account_id,
                        "pending_item_ids":[x for x in _pending if x],"retryable":True,
                        "message":"BORIS автоматически добирает недостающие проверенные фотографии после смысловой очистки."}, "retry"
            raise RuntimeError("internal validation blocked %s item(s)" % int(val.get("blocked") or 0))

    _, _ff_order = C._ff_state_load(campaign)
    _order = C._ff_order_progress(db,campaign,_ff_order,account_id,stage="quantity_gate",persist=True)
    _requested=int(_order.get("requested_total") or 0)
    _created=int(_order.get("created_count") or 0)
    _qa=int(_order.get("qa_pass_count") or 0)
    if _requested and _created < _requested:
        return {"stage":"materialize","campaign_id":campaign.id,"account_id":account_id,
                "requested":_requested,"created":_created,"qa_pass":_qa,"retryable":True}, "retry"
    if _requested and _created > _requested:
        return {"stage":"order_scope_blocked","campaign_id":campaign.id,"account_id":account_id,
                "requested":_requested,"created":_created,"qa_pass":_qa}, "partial"
    if _requested and _qa != _requested:
        return {"stage":"order_qa_blocked","campaign_id":campaign.id,"account_id":account_id,
                "requested":_requested,"created":_created,"qa_pass":_qa}, "partial"

    payload = _campaign_checkpoint(db, job, payload, "official_avito_validation", 6, total)
    feed = C.campaign_feed_validate(campaign.id, C.FFOwnerActivationBody(account_id=account_id, confirmed=False), db, user)
    _, ff_after = C._ff_state_load(campaign)
    intake_after = C._ff_direction_intake(db, campaign, ff_after, C._ff_items(db, campaign.id, allowed))
    if not intake_after.get("all_complete"):
        return {"stage":"needs_input","intake":intake_after,"feed":feed,"message":"Avito запросил неизвестный факт. После ответа задача продолжится с checkpoint."}, "awaiting_confirmation"
    if feed.get("validation_state") == "CHECKING":
        return {"stage":"official_avito_validation","campaign_id":campaign.id,"account_id":account_id,
                "done":6,"total":total,"retryable":True,
                "message":"Avito ещё формирует официальный отчёт. BORIS продолжит проверку автоматически; действий от клиента не требуется."}, "retry"
    if feed.get("validation_state") != "READY":
        raise RuntimeError(feed.get("validation_message") or feed.get("blocked_reason") or "official Avito validation failed")

    payload = _campaign_checkpoint(db, job, payload, "ready_for_review", 8, total)
    # Review artifact is a presentation layer, not a publication action. Generate it
    # automatically from the same canonical CampaignItem/MediaLink state that passed
    # Avito XML validation so the owner never needs a hand-built HTML result page.
    _review_page=None
    _review_page_error=None
    try:
        from app.services.campaign_review_page import generate_campaign_review_page
        _review_page=generate_campaign_review_page(db,campaign.id,account_id,expected_xml_sha=str(feed.get("current_sha") or ""))
        _,_ff_ready=C._ff_state_load(campaign)
        _ff_ready["review_page"]=_review_page
        C._ff_state_save(db,campaign,_ff_ready)
    except Exception as _review_exc:
        # A display artifact must never invalidate an otherwise verified Avito feed.
        # Keep the campaign ready and expose a bounded diagnostic for automatic repair.
        _review_page_error=type(_review_exc).__name__+":"+str(_review_exc)[:180]
    # READY_FOR_REVIEW_IS_TERMINAL_PREP_V1: preparation is finished here; only the separate owner-confirmed deliver action may publish. Persist the same stage on the Campaign row so list/dashboard views do not misleadingly keep showing a completed zero-touch campaign as «draft».
    if str(campaign.status or "").lower() not in {"superseded"}:
        campaign.status="ready_for_review"
        # Item.status describes preparation readiness, not publication approval.
        # Once canonical validation and official Avito validation both passed, keep
        # every live item aligned with the terminal campaign state so the UI never
        # shows a ready campaign containing a misleading «draft» card.
        from app.models.campaign_item import CampaignItem as _ReadyCampaignItem
        (db.query(_ReadyCampaignItem)
           .filter(_ReadyCampaignItem.campaign_id==campaign.id,
                   _ReadyCampaignItem.account_id==account_id,
                   _ReadyCampaignItem.status!="superseded")
           .update({_ReadyCampaignItem.status:"ready"},synchronize_session=False))
        db.commit()
    _, _ff_order_ready = C._ff_state_load(campaign)
    _order_ready = C._ff_order_progress(db,campaign,_ff_order_ready,account_id,stage="ready_for_review",persist=False)
    _order_ready["status"]="READY_FOR_OWNER_REVIEW"
    _order_ready["blocking_reason"]=None
    _order_ready["completed_at"]=datetime.now(timezone.utc).isoformat()
    C._ff_order_event(_order_ready,"ORDER_READY",requested_total=int(_order_ready.get("requested_total") or 0))
    _ff_order_ready["order_contract"]=_order_ready
    C._ff_state_save(db,campaign,_ff_order_ready)
    return {"stage":"ready_for_review","campaign_id":campaign.id,"account_id":account_id,
            "items":feed.get("items"),"photos":feed.get("photos"),"validation_state":"READY",
            "avito_connected":feed.get("avito_connected"),"autoload_ready":feed.get("autoload_ready"),"can_send":feed.get("can_send"),
            "blocked_reason":feed.get("blocked_reason"),"review_page":_review_page,"review_page_error":_review_page_error,
            "message":("Кампания полностью подготовлена и проверена Avito. Нужна только финальная команда владельца на размещение."
                       if feed.get("autoload_ready") else
                       "Кампания подготовлена и XML проверен. Для отправки нужно включить доступ к автозагрузке в этом аккаунте Avito.")}, "completed"
