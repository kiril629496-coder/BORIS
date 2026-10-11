from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Scoped backend certification snapshots no frontend. Keep backend assertions on
# the candidate and inspect the canonical frontend source for UI-only assertions.
FRONTEND = ROOT.parent / "frontend"
if not FRONTEND.is_dir():
    FRONTEND = Path("/root/BORIS/frontend")
assert FRONTEND.is_dir(), "Frontend contract source is required"


def test_campaign_external_write_has_secret_free_lifecycle_audit():
    src = (ROOT / "app/api/avito.py").read_text(encoding="utf-8")
    block = src[src.index('def feed_send_to_avito'):src.index('def feed_autoload_profile', src.index('def feed_send_to_avito')) if 'def feed_autoload_profile' in src[src.index('def feed_send_to_avito'):] else src.index('@router.', src.index('def feed_send_to_avito') + 100)]
    assert "_feed_send_write_audit" in block
    for stage in ('"requested"', '"submitted"', '"reconciled"', '"unknown"', '"scheduled"'):
        assert stage in block
    assert 'source="campaign.external_write"' in block
    assert "client_secret" not in block[block.index("def _feed_send_write_audit"):block.index("db = SessionLocal()", block.index("def _feed_send_write_audit"))]


def test_campaign_timeline_is_account_scoped_and_humanized():
    api = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    ui = (FRONTEND / "app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    block = api[api.index('@router.get("/timeline")'):api.index('@router.get("/ab-tests")')]
    assert "account_id not in _allowed_accounts" in block
    assert "FROM action_log" in block
    assert "campaign.external_write" not in block  # query stays generic over campaign.* sources
    assert '"actor":actor_names' in block
    assert "campaign-owner-timeline" in ui
    assert "Что BORIS сделал" in ui
    assert "/api/campaign/timeline?account_id=" in ui


def test_owner_queue_diagnostics_expose_progress_without_payload_or_secrets():
    src = (ROOT / "app/services/reliability.py").read_text(encoding="utf-8")
    block = src[src.index("def queue_breakdown"):src.index("# Process-local aggregate retry budgets")]
    for token in ('"account_id"', '"kind"', '"age_sec"', '"progress_done"', '"progress_total"', '"blocked_reason"'):
        assert token in block
    assert '"payload_json"' not in block
    assert '"items": items[:80]' in block


def test_campaign_critical_mutations_require_manage_ads():
    src = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    for start, end in [
        ('@router.put("/inventory-policy")', 'def _campaign_inventory_plan_snapshot'),
        ('@router.post("/inventory-policy/run")', '@router.get("/inventory-policy/status")'),
        ('@router.post("/ab-tests/action")', '@router.get("/{campaign_id}")'),
        ('@router.post("/{campaign_id}/guardian/deliver")', '@router.get("/{campaign_id}/guardian/status")'),
        ('@router.post("/{campaign_id}/owner_activation")', '@router.post("/{campaign_id}/safe_export")'),
        ('@router.post("/{campaign_id}/workflow/mutate")', '@router.post("/{campaign_id}/workflow/bulk-transform")'),
    ]:
        block = src[src.index(start):src.index(end, src.index(start))]
        assert "_require_manage_ads" in block
    bulk = src[src.index('@router.post("/{campaign_id}/workflow/bulk-transform")'):src.index('@router.post("/{campaign_id}/workflow/price-confirm")')]
    assert "_require_manage_ads" in bulk


def test_campaign_manage_permission_distinguishes_viewer_from_ad_manager(monkeypatch):
    from types import SimpleNamespace
    from app.api import campaigns

    class Result:
        def __init__(self, value):
            self.value = value
        def first(self):
            return (1,) if self.value else None

    class DB:
        def __init__(self, manage=False, owned=False):
            self.manage = manage
            self.owned = owned
        def execute(self, stmt, params):
            sql = str(stmt)
            if "owner_user_id" in sql:
                return Result(self.owned)
            if "can_manage_ads=TRUE" in sql:
                return Result(self.manage)
            return Result(False)

    viewer = SimpleNamespace(id=42, role="client")
    owner = SimpleNamespace(id=1, role="owner")
    assert campaigns._can_manage_ads(DB(manage=False), viewer, "acc") is False
    assert campaigns._can_manage_ads(DB(manage=True), viewer, "acc") is True
    assert campaigns._can_manage_ads(DB(), owner, "acc") is True


def test_mass_editor_is_account_scoped_and_confirmed_write_needs_manage_ads():
    src = (ROOT / "app/api/mass_editor.py").read_text(encoding="utf-8")
    assert "def _mass_account_access" in src
    assert 'column = "can_manage_ads" if manage else "can_view"' in src
    for signature in [
        "def characteristics(",
        "def get_current_job(",
        "def get_job(",
        "def isolated_status(",
        "def gateway_status(",
        "def gateway_identity_check_status(",
        "def execution_readiness(",
    ]:
        pos = src.index(signature)
        block = src[pos:pos+900]
        assert "_require_mass_route_account" in block
    job = src[src.index("def create_job("):src.index('@router.get("/jobs/current")')]
    assert "manage=bool(req.confirmed)" in job
    for signature in [
        "def create_scan_job(",
        "def isolated_verification_code(",
        "def gateway_pair_code(",
        "def gateway_identity_check(",
        "async def isolated_session_probe(",
    ]:
        pos = src.index(signature)
        block = src[pos:pos+900]
        assert "manage=True" in block


def test_mass_editor_access_distinguishes_view_and_manage(monkeypatch):
    from types import SimpleNamespace
    from app.api import mass_editor

    class Result:
        def __init__(self, value):
            self.value = value
        def first(self):
            return (1,) if self.value else None

    class DB:
        def __init__(self, view=False, manage=False, owned=False):
            self.view = view
            self.manage = manage
            self.owned = owned
        def execute(self, stmt, params):
            sql = str(stmt)
            if "owner_user_id" in sql:
                return Result(self.owned)
            if "can_manage_ads" in sql:
                return Result(self.manage)
            if "can_view" in sql:
                return Result(self.view)
            return Result(False)
        def close(self):
            pass

    viewer = SimpleNamespace(id=11, role="client")
    owner = SimpleNamespace(id=1, role="owner")
    monkeypatch.setattr(mass_editor, "SessionLocal", lambda: DB(view=True, manage=False))
    assert mass_editor._mass_account_access(viewer, "acc", manage=False) is True
    assert mass_editor._mass_account_access(viewer, "acc", manage=True) is False
    monkeypatch.setattr(mass_editor, "SessionLocal", lambda: DB(view=True, manage=True))
    assert mass_editor._mass_account_access(viewer, "acc", manage=True) is True
    monkeypatch.setattr(mass_editor, "SessionLocal", lambda: DB())
    assert mass_editor._mass_account_access(owner, "any-account", manage=True) is True


def test_direct_avito_feed_send_requires_manage_ads_for_resolved_http_user():
    src = (ROOT / "app/api/avito.py").read_text(encoding="utf-8")
    block = src[src.index('def feed_send_to_avito'):src.index('# GLOBAL PUBLICATION GUARD', src.index('def feed_send_to_avito'))]
    assert "can_manage_ads=TRUE" in block
    assert "owner_user_id" in block
    assert "Недостаточно прав для размещения объявлений" in block
    assert '_uid = int(getattr(user, "id", 0) or 0)' in block


def test_campaign_timeline_redacts_historical_secrets():
    src = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    block = src[src.index('@router.get("/timeline")'):src.index('@router.get("/ab-tests")')]
    assert "def _timeline_safe" in block
    for secret_name in ("authorization","access_token","refresh_token","client_secret","password","cookie","session_token"):
        assert secret_name in block
    assert "Bearer [скрыто]" in block
    assert '"before":_timeline_safe' in block
    assert '"after":_timeline_safe' in block
    assert '"reason":_timeline_safe' in block


def test_campaign_timeline_executes_and_redacts_secret_values():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.api import campaigns

    class Result:
        def __init__(self, rows):
            self.rows = rows
        def fetchall(self):
            return self.rows

    class DB:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if "SELECT account_id FROM accounts" in sql:
                return Result([("acc",)])
            if "FROM action_log" in sql:
                return Result([(
                    991,
                    datetime.now(timezone.utc),
                    "boris_auto",
                    "Передал token=super-secret в Avito",
                    "объявления",
                    "Автозагрузка",
                    "authorization=Bearer-secret",
                    "access_token=abc123456789",
                    "cookie=session123456",
                    "campaign.external_write",
                    "req-991",
                )])
            return Result([])

    out = campaigns.campaign_timeline(
        account_id="acc",
        limit=12,
        db=DB(),
        user=SimpleNamespace(id=1, role="owner"),
    )
    assert out["status"] == "ok"
    assert out["total"] == 1
    rendered = " ".join(str(v) for v in out["items"][0].values())
    assert "super-secret" not in rendered
    assert "abc123456789" not in rendered
    assert "session123456" not in rendered
    assert "[скрыто]" in rendered


def test_campaign_history_tasks_and_editing_are_productized():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    overview=(FRONTEND/"app/dashboard/campaign/page.tsx").read_text(encoding="utf-8")
    ab=(FRONTEND/"app/dashboard/campaign/AvitoABTestsPanel.tsx").read_text(encoding="utf-8")
    detail=(FRONTEND/"app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    selector=(FRONTEND/"app/components/shell/AccountSelector.tsx").read_text(encoding="utf-8")

    timeline=api[api.index('@router.get("/timeline")'):api.index('@router.get("/ab-tests")')]
    assert '"source":"real_action_log"' in timeline
    assert '"campaign.item_patch","campaign.bulk_transform"' in timeline
    assert 'title=f"{actor} обновил {count} {_word}"' in timeline
    assert 're.sub(r"<[^>]+>"' in timeline

    tasks=api[api.index('@router.get("/tasks")'):api.index('@router.get("/ab-tests")')]
    for token in (
        '"created_at"', '"started_at"', '"finished_at"', '"last_progress_at"',
        '"duration_total_seconds"', '"duration_active_seconds"', '"duration_waiting_seconds"',
        '"progress_percent"', '"retries"', '"source":"background_jobs+mass_editor_jobs"',
    ):
        assert token in tasks

    assert 'workspaceTab==="history"' in overview
    assert 'setWorkspaceTab("history")' in overview
    assert 'workspaceTab==="tasks"' in overview
    assert 'data-testid="campaign-owner-timeline"' in overview
    assert 'data-testid="campaign-task-history"' in overview
    assert "/api/campaign/tasks?account_id=" in overview
    assert "clientTaskSeconds" in overview and "formatDuration" in overview

    assert "cleanClientText" in ab
    assert '.replace(/<[^>]+>/g," ")' in ab
    assert '"?edit="+x.item_id+"&tab=ad' in ab
    assert '"?edit="+x.item_id+"&tab=photos' in ab
    assert '"?edit="+x.item_id+"&tab=history' in ab

    assert "URLSearchParams" in detail
    assert '["history","История"]' in detail
    assert 'query.get("edit")' in detail
    assert 'data-editor-open={open===it.id?"true":"false"}' in detail
    assert "История изменений карточки" in detail

    assert "accountPickerPopover" in selector
    assert "Найти аккаунт…" in selector
    assert '["work","Рабочие"]' in selector
    assert '["qa","QA"]' in selector
    assert "<select" not in selector


def test_campaign_timeline_groups_real_item_patch_burst():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.api import campaigns

    now=datetime.now(timezone.utc)
    rows=[]
    for i,name in enumerate(("Карточка А","Карточка Б","Карточка В")):
        rows.append((
            100-i,
            now-timedelta(milliseconds=i*500),
            "user",
            "Изменил черновик объявления",
            "объявление",
            name,
            "",
            "",
            "правка в конструкторе кампании",
            "campaign.item_patch",
            "",
        ))

    class Result:
        def __init__(self, rows):
            self.rows=rows
        def fetchall(self):
            return self.rows

    class DB:
        def execute(self, stmt, params=None):
            sql=str(stmt)
            if "SELECT account_id FROM accounts" in sql:
                return Result([("acc",)])
            if "FROM action_log" in sql:
                return Result(rows)
            return Result([])

    out=campaigns.campaign_timeline(
        account_id="acc",limit=20,db=DB(),
        user=SimpleNamespace(id=1,role="owner"),
    )
    assert out["source"]=="real_action_log"
    assert out["total"]==1
    event=out["items"][0]
    assert event["count"]==3
    assert event["action"]=="Сотрудник обновил 3 объявления"
    assert set(event["objects"])=={"Карточка А","Карточка Б","Карточка В"}


def test_published_card_editor_allows_local_revision_but_inflight_stays_locked(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.api import campaigns

    terminal = {"owner_activation": {"status": "completed"}, "step": "publication_completed"}
    monkeypatch.setattr(campaigns, "_ff_state_load", lambda campaign: ({}, terminal))
    assert campaigns._ff_assert_card_editor_mutable(SimpleNamespace()) is terminal

    inflight = {"owner_activation": {"status": "processing"}, "step": "publication_processing"}
    monkeypatch.setattr(campaigns, "_ff_state_load", lambda campaign: ({}, inflight))
    try:
        campaigns._ff_assert_card_editor_mutable(SimpleNamespace())
        assert False, "in-flight publication must remain immutable"
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["code"] == "campaign_publication_in_flight"


def test_post_publication_editor_revision_is_local_and_owner_gated():
    from app.api import campaigns

    ff = {"owner_activation": {"status": "completed"}, "step": "publication_completed"}
    campaigns._ff_mark_post_publication_revision(ff, 42, ["title", "images_count"])
    state = ff["post_publication_revision"]
    assert state["state"] == "local_changes_pending_validation"
    assert state["item_ids"] == [42]
    assert state["changed_fields"] == ["images_count", "title"]
    assert state["requires_owner_publication"] is True
    assert state["external_mutation"] is False


def test_published_card_editor_routes_are_narrow_and_frontend_exposes_editor():
    api = (ROOT / "app/api/campaigns.py").read_text(encoding="utf-8")
    ui = (FRONTEND / "app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    mutate = api[api.index('@router.post("/{campaign_id}/workflow/mutate")'):api.index('@router.post("/{campaign_id}/workflow/bulk-transform")')]
    price = api[api.index('@router.post("/{campaign_id}/workflow/price-confirm")'):api.index('@router.post("/{campaign_id}/workflow/text-file")')]
    category = api[api.index('@router.post("/{campaign_id}/workflow/category/{item_id}")'):api.index('@router.get("/{campaign_id}/workflow/preview")')]
    assert "_ff_assert_card_editor_mutable" in mutate
    assert "_ff_mark_post_publication_revision" in mutate
    assert "_ff_assert_card_editor_mutable" in price
    assert "_ff_mark_post_publication_revision" in price
    assert "_ff_assert_card_editor_mutable" in category
    assert "_ff_mark_post_publication_revision" in category
    assert '"external_mutation":False' in api
    assert "const cardEditorLocked = publicationInFlight" in ui
    assert 'query.get("edit")' in ui
    assert "Редактируется версия в BORIS." in ui
    assert "Опубликованное объявление в Avito не изменится автоматически." in ui


def test_campaign_task_history_includes_mass_editor_timer_source():
    src=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    block=src[src.index('@router.get("/tasks")'):src.index('@router.get("/ab-tests")')]
    assert "MASS_EDITOR_TASK_TIMERS_V1" in block
    assert "mass_editor_job:%" in block
    assert "Массовое редактирование объявлений" in block
    assert "duration_total_seconds" in block
    assert "duration_active_seconds" in block
    assert "duration_waiting_seconds" in block
    assert '"source":"background_jobs+mass_editor_jobs"' in block


def test_ab_deep_link_fetches_exact_item_without_page_assumption():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    ui=(FRONTEND/"app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert '@router.get("/{campaign_id}/item/{item_id}")' in api
    assert 'exact=await apiGet("/api/campaign/"+id+"/item/"+requestedId)' in ui
    assert 'setItems((prev)=>[item as Item,...prev.filter((x)=>Number(x.id)!==requestedId)])' in ui


def test_campaign_photo_reorder_is_exact_scoped_and_client_visible():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    ui=(FRONTEND/"app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert "media_order_ids: list[int] = []" in api
    block=api[api.index("CAMPAIGN_MEDIA_REORDER_V1"):api.index("if body.hero_media_id:",api.index("CAMPAIGN_MEDIA_REORDER_V1"))]
    assert "set(ordered_ids)!=set(current_ids)" in block
    assert '"code":"campaign_media_order_scope_mismatch"' in block
    assert 'link.position=pos' in block
    assert 'link.role="hero" if pos==0 else "gallery"' in block
    assert 'row.hero_media_id=int(ordered_ids[0])' in block
    assert 'p["media_semantic_evidence"]=[]' in block
    assert "media_order_changed_requires_recheck" in block
    assert "const reorderPhotos" in ui
    assert "media_order_ids:ids" in ui
    assert 'draggable={!cardEditorLocked&&!feedBusy}' in ui
    assert "Перетащите, чтобы изменить порядок" in ui
    assert ">Раньше</button>" in ui
    assert ">Позже</button>" in ui


def test_ab_current_working_set_excludes_superseded_and_stale_drafts():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    block=api[api.index("AB_CURRENT_WORKING_SET_V1"):api.index("formula_names=",api.index("AB_CURRENT_WORKING_SET_V1"))]
    assert 'lifecycle=="superseded"' in block
    assert 'identity.startswith("superseded")' in block
    assert 'lifecycle in {"published","publishing"}' in block
    assert 'provider in {"active","old","inactive","closed","expired","removed"}' in block
    assert "return bool(finish_marker)" in block
    assert "published_rows=account_rows" in block
    assert "_ab_row_is_current(row" in block


def test_campaign_timeline_exposes_real_group_duration():
    api=(ROOT/"app/api/campaigns.py").read_text(encoding="utf-8")
    ui=(FRONTEND/"app/dashboard/campaign/CampaignTimeline.tsx").read_text(encoding="utf-8")
    block=api[api.index('@router.get("/timeline")'):api.index('@router.get("/tasks")')]
    assert '"duration_ms":duration_ms' in block
    assert 'group["when"]-group["_last_when"]' in block
    assert "выполнялось " in ui
    assert "item.duration_ms" in ui


def test_subcategory_settings_are_human_and_have_no_gray_primary_category_selects():
    feed=(ROOT/"app/api/feed_contract.py").read_text(encoding="utf-8")
    ui=(FRONTEND/"app/dashboard/campaign/[id]/page.tsx").read_text(encoding="utf-8")
    assert '"AvitoId"' in feed and '"AuctionPrice"' in feed and '"AuctionPriceLastDate"' in feed
    assert "AVITO_FIELD_LABELS" in ui
    assert "AVITO_ADVANCED_OPTIONAL_FIELDS" in ui
    assert "avitoDependencyText" in ui
    assert "Доставка, опт и документы" in ui
    assert "function BorisSelect" in ui
    assert '<select value={bulkTemplateId}' not in ui
    assert '<select value={draft.template_id' not in ui
    bulk=ui[ui.index("Категория и характеристики Avito"):ui.index("Фото и баннеры для выбранных")]
    assert "BorisSelect" in bulk
    assert "avitoFieldLabel(f)" in bulk
    assert "avitoDependencyText(f.depends_on" in bulk
    intake=ui[ui.index("const renderField=(f:any)=>"):ui.index("return typeof document",ui.index("const renderField=(f:any)=>"))]
    assert "avitoFieldLabel(f)" in intake
    assert "avitoDependencyText(f.depends_on" in intake
