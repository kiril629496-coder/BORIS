"""Opt-in real PostgreSQL Campaign mutation gate; all providers are test doubles.

Uses an empty socket-only disposable cluster, never the default BORIS database.
The exact adapter/photo-stack source is compiled without its unrelated routes.
Models, SQL, row versions, media writes and revision hooks are real.
"""
import ast
import hashlib
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

URL = os.getenv("BORIS_QA_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="requires isolated socket-only QA PostgreSQL")


@pytest.fixture
def world(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from fastapi import HTTPException
    from PIL import Image

    parsed = make_url(URL)
    cluster = Path(os.environ["BORIS_QA_CLUSTER_ROOT"]).resolve()
    assert parsed.host is None and parsed.password is None
    assert str(parsed.query.get("host", "")).startswith(str(cluster) + "/")
    admin = create_engine(URL, pool_pre_ping=True)
    schema = "boris_qa_campaign_" + uuid.uuid4().hex
    with admin.connect() as conn:
        assert conn.execute(text("SHOW listen_addresses")).scalar() == ""
        assert Path(conn.execute(text("SHOW data_directory")).scalar()).resolve().is_relative_to(cluster)
        assert not conn.execute(text("SELECT to_regclass('public.campaign_items')")).scalar()
        conn.execute(text(f'CREATE SCHEMA "{schema}"')); conn.commit()
    engine = create_engine(URL, pool_size=8, max_overflow=2,
        connect_args={"options": f"-csearch_path={schema},pg_catalog -cstatement_timeout=12000 -clock_timeout=4000"})
    from app.models.campaign import Campaign
    from app.models.campaign_item import CampaignItem
    from app.models.media_asset import MediaAsset
    from app.models.media_link import MediaLink
    from app.models.storage import Storage
    from app.services import campaign_service as CS
    from app.services import media_service as M, media_storage as MS
    from app.services import media_semantic_validator as V, banner_full_ai_premium as P
    from app.services import campaign_banner_media_contract as MC
    from app.services import campaign_editor_jobs as E
    from app.services import subscription_gate as SG
    tables = [Campaign.__table__, CampaignItem.__table__, MediaAsset.__table__, MediaLink.__table__, Storage.__table__]
    for table in tables:
        table.create(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    image_root = tmp_path / "images"; image_root.mkdir()
    monkeypatch.setattr(MS, "IMAGES_DIR", str(image_root))
    original_file_check = MC.linked_file_current
    monkeypatch.setattr(MC, "linked_file_current", lambda a, image_root_ignored=None: original_file_check(a, image_root))
    original_link_proof = MC.premium_link_proof
    def local_proof(*args, **kwargs):
        kwargs["image_root"] = image_root
        return original_link_proof(*args, **kwargs)
    monkeypatch.setattr(MC, "premium_link_proof", local_proof)
    monkeypatch.setattr(E, "maybe_enqueue", lambda *a, **k: None)
    monkeypatch.setattr(SG, "base_subscription_access", lambda *a, **k: {"allowed": True})
    def forbidden(*args, **kwargs):
        raise AssertionError("external HTTP/Avito/paid provider forbidden in integration gate")
    import requests, httpx, urllib.request
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(V, "_vision_chat_json", forbidden)
    names = {"_campaign_or_404", "_ff_items", "_ff_payload", "_ff_state_load", "_ff_publication_locked",
        "_ff_assert_card_editor_mutable", "_ff_assert_media_commit_mutable", "_ff_mark_post_publication_revision", "_ff_state_save",
        "_ff_is_existing_banner_asset", "_ff_premium_paid_image_override", "_ff_premium_banner_ready",
        "_ff_rebind_premium_gallery_evidence", "_ff_full_ai_premium_banner_batch", "ff_workflow_photo_stack", "_ff_contract_missing"}
    source_path = Path(__file__).resolve().parents[1] / "app/api/campaigns.py"
    tree = ast.parse(source_path.read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []; node.returns = None
            node.args.defaults = [ast.Constant(None) if isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and value.func.id=="Depends" else value for value in node.args.defaults]
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                arg.annotation = None
            selected.append(node)
    assert len(selected) == len(names)
    namespace = {"json": json, "HTTPException": HTTPException, "CS": CS,
        "observe_suppressed": lambda *a, **k: None,
        "_allowed_accounts": lambda d, u: list(u.accounts),
        "_campaign_provider_call": lambda fn, *a, **k: fn(*a, **k)}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(source_path), "exec"), namespace)
    receipts = {}; image_calls = []; qa_batches = []; approve_calls = []; media_calls = []
    after_create = []; after_qa = []; fail_items = set(); duplicate_items = set()
    user = NS(id=1, role="owner", accounts=["tenant-a", "tenant-b"])
    counter = iter(range(1, 1000000))

    def asset(account="tenant-a", media_type="photo", source_ref=None, provider="upload", session=None):
        target_db = session or db
        serial = next(counter)
        key = f"{account}/asset_{serial}.png"; path = image_root / key; path.parent.mkdir(exist_ok=True)
        Image.new("RGB", (120, 120), (serial % 255, (serial * 37) % 255, (serial * 71) % 255)).save(path)
        obj = MediaAsset(owner_user_id=1, account_id=account, filename=path.name, storage_key=key,
            mime="image/png", width=120, height=120, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            media_type=media_type, status="approved", source_type="upload", source_provider=provider,
            source_ref=source_ref, tags=[])
        target_db.add(obj); target_db.flush(); return obj

    def create(**kwargs):
        image_calls.append(kwargs)
        item = kwargs["provenance"]["item_id"]
        bid = hashlib.sha256(kwargs["idempotency_key"].encode()).hexdigest()[:24]
        if (bid, 1) in receipts:
            return dict(receipts[(bid, 1)])
        with Session() as provider_db:
            obj = asset(kwargs["account_id"], "banner", f"premium:{bid}:v1", "openai_full_ai_premium", provider_db)
            mid = obj.id; sha = obj.sha256; path = image_root / obj.storage_key
            provider_db.commit()
        phash = "f" * 16 if item in duplicate_items else hashlib.sha256(str(item).encode()).hexdigest()[:16]
        row = {"status": "ok", "banner_id": bid, "version": 1, "account_id": kwargs["account_id"],
            "absolute_path": str(path), "url":"/images/"+str(path.relative_to(image_root)), "artifact_sha256": sha, "artifact_current": True,
            "image_outcome": "succeeded", "vision_qa_deferred": True, "preview_status": "VISION_QA_PENDING",
            "cost_reconciled": True, "cost_sla_pass": True, "total_cost_rub": 2.15,
            "technical_qa": {"passed": True, "artifact_sha256": sha, "perceptual_hash": phash},
            "qa_version": P.VISION_QA_VERSION, "qa_passed": False, "receipt_current": False,
            "provenance": kwargs["provenance"], "media_id": mid}
        receipts[(bid, 1)] = row
        for hook in after_create[:]: hook(item)
        return dict(row)

    def finalize(*, account_id, entries, provenance):
        qa_batches.append([dict(x) for x in entries]); out = {}
        for entry in entries:
            row = receipts[(entry["banner_id"], entry["version"])]
            okay = row["provenance"]["item_id"] not in fail_items
            row.update(receipt_current=True, qa_terminal=True, qa_passed=okay,
                vision_qa_mode="batch_v2", vision_qa_batch_size=len(entries),
                preview_status="DRAFT_PREVIEW" if okay else "VISION_QA_FAILED")
            if not okay:
                row.update(retry_action="regenerate_quality", retry_allowed=False,
                    qa_rejection_reason="confirmed quality rejection")
            out[f"{entry['banner_id']}:v{entry['version']}"] = dict(row)
        for hook in after_qa[:]: hook()
        return {"ok": True, "previews": out, "provider_calls": 1, "batch_provider_calls": 1}

    def read(account_id, banner_id, version):
        row = receipts.get((banner_id, version), {})
        return dict(row) if row.get("account_id") == account_id else {"status": "not_found"}

    def approve(account_id, banner_id, version):
        approve_calls.append((account_id, banner_id, version))
        row = read(account_id, banner_id, version)
        return {"status": "ok", "media": {"id": row["media_id"]}}

    def reject(account_id, banner_id, version, reason="", quality_rejection=False):
        row = receipts[(banner_id, version)]
        row.update(qa_passed=False, qa_terminal=True, receipt_current=True,
            qa_rejection_reason=reason, retry_action="regenerate_quality", retry_allowed=False)
        return dict(row)

    def photos(payload, pairs, **kwargs):
        media_calls.append(len(pairs))
        return [{"match": True, "confidence": 0.96, "subject": "verified furniture", "reason": "match"} for _ in pairs]
    monkeypatch.setattr(P, "create_preview", create)
    monkeypatch.setattr(P, "finalize_preview_vision_batch", finalize)
    monkeypatch.setattr(P, "read_current_preview", read)
    monkeypatch.setattr(P, "approve_preview", approve)
    monkeypatch.setattr(P, "reject_preview", reject)
    monkeypatch.setattr(P, "status_payload", lambda: {"enabled": True, "engine": "full_ai_premium", "model": "gpt-image-2", "quality": "low", "estimated_cost_rub": 2.6})
    monkeypatch.setattr(V, "classify_item_media_vision", photos)

    def setup(count, account="tenant-a", published=False):
        ff = {"step": "publication_completed", "owner_activation": {"status": "completed"}} if published else {"step": "banners"}
        campaign = Campaign(name="ISOLATED QA", default_account_id=account, owner_user_id=1,
            settings_json=json.dumps({"feed_factory": ff}))
        db.add(campaign); db.flush(); rows = []
        for index in range(count):
            payload = {"title": f"Мебель на заказ {index}", "description": "Подтвержденное описание мебели", "direction": "Мебель",
                "category": "Мебель", "feed_factory": True}
            row = CampaignItem(campaign_id=campaign.id, account_id=account, status="draft", payload_json=json.dumps(payload))
            db.add(row); db.flush()
            gallery = [asset(account) for _ in range(8)]
            for pos, item in enumerate(gallery): M.link(db, item, "campaign_item", str(row.id), "hero" if pos == 0 else "gallery", pos)
            db.flush(); row.hero_media_id=gallery[0].id
            payload["images"]=[M.asset_public(a)["original_url"] for a in gallery]
            pairs = db.query(MediaLink, MediaAsset).join(MediaAsset, MediaAsset.id == MediaLink.media_id).filter(MediaLink.entity_id == str(row.id)).order_by(MediaLink.position).all()
            V.stamp_item_semantic_evidence(payload, pairs, [{"match":True,"confidence":0.96,"subject":"furniture","reason":"verified"} for _ in pairs])
            row.payload_json=json.dumps(payload,ensure_ascii=False); rows.append(row)
        db.commit(); return campaign, rows

    def run(campaign, rows, force=False):
        body=NS(force=force,idempotency_key=f"qa-order-{campaign.id}",confirmed_paid_items=len(rows),
            item_idempotency_keys={str(r.id):f"job:{campaign.id}:item:{r.id}:r0" for r in rows},
            item_budget_intent_keys={str(r.id):f"job:{campaign.id}:item:{r.id}" for r in rows})
        return namespace["_ff_full_ai_premium_banner_batch"](campaign.id,campaign,{},rows,body,db,user)

    state=NS(db=db,Session=Session,Campaign=Campaign,Item=CampaignItem,Media=MediaAsset,Link=MediaLink,Storage=Storage,
        ns=namespace,setup=setup,run=run,asset=asset,user=user,image_root=image_root,validator=V,
        image_calls=image_calls,qa_batches=qa_batches,approve_calls=approve_calls,media_calls=media_calls,
        after_create=after_create,after_qa=after_qa,fail_items=fail_items,duplicate_items=duplicate_items,receipts=receipts)
    try:
        yield state
    finally:
        db.rollback(); db.close(); engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE')); conn.commit()
        admin.dispose()


def snapshot(w, row_id):
    w.db.expire_all()
    row=w.db.get(w.Item,row_id)
    links=w.db.query(w.Link).filter(w.Link.entity_type=="campaign_item",w.Link.entity_id==str(row_id)).order_by(w.Link.position,w.Link.id).all()
    return {"payload":row.payload_json,"hero":row.hero_media_id,"version":row.updated_at,
        "links":[(x.id,x.media_id,x.position,x.role) for x in links]}


@pytest.mark.parametrize("count,expected",[(1,[1]),(2,[2]),(4,[4]),(5,[4,1]),(8,[4,4])])
def test_real_adapter_batches_and_nine_media(world,count,expected):
    w=world; campaign,rows=w.setup(count)
    out=w.run(campaign,rows)
    assert out["status"]=="ok",out
    assert len(out["changed"])==count
    assert [len(batch) for batch in w.qa_batches]==expected
    assert len(w.image_calls)==count
    assert all(x["reference_path"] is None and x["defer_vision_qa"] is True for x in w.image_calls)
    for row in rows:
        current=snapshot(w,row.id); payload=json.loads(current["payload"])
        assert len(current["links"])==len(payload["images"])==9
        assert len({x[1] for x in current["links"]})==9
        assert current["links"][0][1]==current["hero"]
        assert [x[2] for x in current["links"]]==list(range(9))
        assert w.ns["_ff_premium_banner_ready"](w.db,w.db.get(w.Item,row.id),payload)
        assert not w.validator.validate_linked_media_semantics(w.db,w.db.get(w.Item,row.id),payload)


def test_repeat_and_photo_noop_preserve_links_hashes_and_revision(world):
    w=world; campaign,rows=w.setup(1); row_id=rows[0].id
    assert w.run(campaign,rows)["status"]=="ok"
    before=snapshot(w,row_id); history=w.db.query(w.Storage).count()
    assert w.run(campaign,rows)["skipped"]==[row_id]
    body=NS(item_ids=[row_id],media_ids=[x[1] for x in before["links"]][1:],photos_per_item=8,randomize=False,after_banner=True)
    out=w.ns["ff_workflow_photo_stack"](campaign.id,body,w.db,w.user)
    assert out["skipped"]==[row_id]
    assert snapshot(w,row_id)==before
    assert len(w.image_calls)==1 and len(w.qa_batches)==1 and not w.media_calls
    assert w.db.query(w.Storage).count()==history


def test_rejected_neighbor_does_not_rollback_successful_cards(world):
    w=world; campaign,rows=w.setup(4); rejected=rows[1].id; w.fail_items.add(rejected)
    before=snapshot(w,rejected)
    out=w.run(campaign,rows)
    assert out["status"]=="partial" and len(out["changed"])==3
    assert snapshot(w,rejected)==before
    assert len(w.qa_batches)==1 and len(w.qa_batches[0])==4
    assert len(w.approve_calls)==3
    assert all(len(snapshot(w,i)["links"])==9 for i in out["changed"])


def test_late_text_writer_is_not_overwritten(world):
    w=world; campaign,rows=w.setup(1); item_id=rows[0].id; before=snapshot(w,item_id)
    def edit(_):
        with w.Session() as other:
            row=other.get(w.Item,item_id); payload=json.loads(row.payload_json)
            payload["title"]="Более новая подтвержденная версия"
            row.payload_json=json.dumps(payload,ensure_ascii=False); other.commit()
    w.after_create.append(edit)
    out=w.run(campaign,rows)
    assert not out["changed"] and out["errors"][0]["code"]=="CARD_VERSION_CONFLICT"
    after=snapshot(w,item_id)
    assert json.loads(after["payload"])["title"]=="Более новая подтвержденная версия"
    assert after["links"]==before["links"] and after["hero"]==before["hero"]
    assert not w.approve_calls


def test_two_account_boundary_blocks_before_paid_call(world):
    w=world; campaign,rows=w.setup(1,"tenant-a"); _,foreign=w.setup(1,"tenant-b")
    out=w.run(campaign,[*rows,*foreign])
    assert out["status"]=="blocked" and not w.image_calls and not w.qa_batches
    before=snapshot(w,rows[0].id)
    alien=w.asset("tenant-b"); w.db.commit()
    body=NS(item_ids=[rows[0].id],media_ids=[alien.id],photos_per_item=1,randomize=False,after_banner=True)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        w.ns["ff_workflow_photo_stack"](campaign.id,body,w.db,w.user)
    assert exc.value.status_code==409 and snapshot(w,rows[0].id)==before


def test_terminal_publication_creates_only_local_revision(world):
    w=world; campaign,rows=w.setup(1,published=True)
    assert w.run(campaign,rows)["status"]=="ok"
    w.db.refresh(campaign); ff=json.loads(campaign.settings_json)["feed_factory"]
    assert ff["owner_activation"]["status"]=="completed"
    assert ff["post_publication_revision"]["external_mutation"] is False
    assert ff["post_publication_revision"]["requires_owner_publication"] is True
    assert ff["step"]=="validation" and ff["ready_for_owner_approval"] is False
    records=[json.loads(x.value) for x in w.db.query(w.Storage).all() if x.key.startswith("campaign_card_revision:")]
    assert records and "media" in records[-1]["changed_fields"]


def test_cross_chunk_visual_duplicate_is_rejected_before_next_qa(world):
    w=world; campaign,rows=w.setup(2); w.duplicate_items.update(x.id for x in rows)
    assert w.run(campaign,rows[:1])["status"]=="ok"
    out=w.run(campaign,rows[1:])
    assert not out["changed"] and out["errors"][0]["code"]=="BANNER_DUPLICATE"
    assert len(w.qa_batches)==1


def test_selected_new_photo_changed_during_qa_is_not_attached(world,monkeypatch):
    w=world; campaign,rows=w.setup(1); item_id=rows[0].id; before=snapshot(w,item_id)
    chosen=w.asset(); selected_id=chosen.id; w.db.commit()
    def mutate(payload,pairs,**kwargs):
        with w.Session() as other:
            replacement=other.get(w.Media,selected_id)
            path=w.image_root/replacement.storage_key; path.write_bytes(b"new bytes after QA snapshot")
            replacement.sha256=hashlib.sha256(path.read_bytes()).hexdigest(); other.commit()
        return [{"match":True,"confidence":0.96}]
    monkeypatch.setattr(w.validator,"classify_item_media_vision",mutate)
    body=NS(item_ids=[item_id],media_ids=[selected_id],photos_per_item=1,randomize=False,after_banner=True)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        w.ns["ff_workflow_photo_stack"](campaign.id,body,w.db,w.user)
    assert exc.value.status_code==409 and snapshot(w,item_id)==before


def test_intake_questions_do_not_accept_present_empty_required_facts(world,monkeypatch):
    from app.api import feed_contract
    fields={"Width":{"required":True,"allowed":[]},"Height":{"required":True,"allowed":[]},
        "Mirror":{"required":True,"allowed":[False,True]}}
    monkeypatch.setattr(feed_contract,"resolve_feed_contract",lambda *a,**k:{"ok":True,"fields":fields})
    missing=world.ns["_ff_contract_missing"](world.db,"isolated-template",{"Width":"","Height":"  ","Mirror":False})
    assert {x["field"] for x in missing}=={"Width","Height"}


def test_automatic_task_cannot_commit_after_publication_finishes(world,monkeypatch):
    from app.services import job_execution_clock as clock
    w=world; campaign,rows=w.setup(1); item_id=rows[0].id; before=snapshot(w,item_id)
    monkeypatch.setattr(clock,"current_job_provenance",lambda:{"job_kind":"campaign_banners"})
    def publish(_):
        with w.Session() as other:
            row=other.get(w.Campaign,campaign.id)
            row.settings_json=json.dumps({"feed_factory":{"step":"publication_completed","owner_activation":{"status":"completed"}}})
            other.commit()
    w.after_create.append(publish)
    out=w.run(campaign,rows)
    assert not out["changed"] and out["errors"][0]["code"]=="campaign_publication_locked"
    assert snapshot(w,item_id)==before and not w.approve_calls
