"""Isolated DB tests: actual ORM compare-and-swap and transactional media history."""
import json
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.models.campaign import Campaign
from app.models.campaign_item import CampaignItem
from app.models.media_link import MediaLink
from app.models.storage import Storage
from app.services.campaign_card_revisions import lock_versions, next_version


@pytest.fixture
def engine(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "cards.sqlite"))
    for model in (Campaign, CampaignItem, MediaLink, Storage):
        model.__table__.create(engine)
    with Session(engine) as db:
        db.add(Campaign(id=1, default_account_id="qa-only"))
        db.flush()
        db.add(CampaignItem(id=101, campaign_id=1, account_id="qa-only",
                           payload_json=json.dumps({"title": "До", "price": 100, "images": ["a", "b"]})))
        db.commit()
        db.add_all([MediaLink(id=201, media_id=301, entity_type="campaign_item", entity_id="101", role="hero", position=0),
                    MediaLink(id=202, media_id=302, entity_type="campaign_item", entity_id="101", role="gallery", position=1)])
        db.commit()
        db.query(Storage).delete()
        db.commit()
    yield engine
    engine.dispose()


def records(db):
    return [json.loads(x.value) for x in db.query(Storage).order_by(Storage.id).all()]


def test_stale_orm_writer_cannot_overwrite_or_append_revision(engine):
    with Session(engine) as first, Session(engine) as second:
        a, b = first.get(CampaignItem, 101), second.get(CampaignItem, 101)
        a.payload_json = json.dumps({"title": "Первый"})
        first.commit()
        b.payload_json = json.dumps({"title": "Второй"})
        with pytest.raises(StaleDataError):
            second.commit()
        second.rollback()
    with Session(engine) as db:
        assert json.loads(db.get(CampaignItem, 101).payload_json)["title"] == "Первый"
        history = records(db)
        assert len(history) == 1
        assert history[0]["before"]["title"] == "До"
        assert history[0]["after"]["title"] == "Первый"


def test_expected_version_fails_closed_and_tenant_scope_is_preserved(engine):
    with Session(engine) as db:
        row = db.get(CampaignItem, 101)
        version = row.updated_at.isoformat()
        assert lock_versions(db, 1, [101], {"101": version}, ["qa-only"])[0].id == 101
        for expected in (None, "bad", "2020-01-01T00:00:00Z"):
            with pytest.raises(HTTPException) as error:
                lock_versions(db, 1, [101], expected, ["qa-only"])
            assert error.value.status_code == 409
            assert error.value.detail["code"] == "CARD_VERSION_CONFLICT"
        with pytest.raises(HTTPException) as error:
            lock_versions(db, 1, [101], version, ["different-tenant"])
        assert error.value.status_code == 404


def test_media_intermediate_flush_keeps_original_before_and_final_after(engine):
    with Session(engine) as db:
        row = db.get(CampaignItem, 101)
        before_version = row.updated_at
        db.info["campaign_revision_actor"] = {"actor_id": 7, "actor": "QA сотрудник", "actor_type": "employee"}
        db.delete(db.get(MediaLink, 201))
        db.flush()
        link = db.get(MediaLink, 202)
        link.role, link.position = "hero", 0
        row.hero_media_id = 302
        payload = json.loads(row.payload_json)
        payload["images"] = ["b"]
        row.payload_json = json.dumps(payload)
        db.commit()
        assert row.updated_at != before_version
        history = records(db)
        assert len(history) == 1
        assert [x["media_id"] for x in history[0]["before"]["media"]] == [301, 302]
        assert history[0]["after"]["media"] == [{"media_id": 302, "role": "hero", "position": 0}]
        assert history[0]["actor_id"] == 7
        assert {"images", "media", "hero_media_id"}.issubset(history[0]["changed_fields"])


def test_rollback_discards_content_and_revision(engine):
    with Session(engine) as db:
        row = db.get(CampaignItem, 101)
        row.payload_json = json.dumps({"title": "Не сохранять"})
        db.flush()
        db.rollback()
        assert json.loads(db.get(CampaignItem, 101).payload_json)["title"] == "До"
        assert not records(db)


def test_versions_are_monotonic_even_with_future_timestamp():
    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert next_version(future) > future


def test_response_versions_belong_to_own_write_not_later_refresh(engine):
    with Session(engine) as first, Session(engine) as second:
        a = first.get(CampaignItem, 101)
        a.payload_json = json.dumps({"title": "Первый"})
        first.commit()
        own_version = first.info["campaign_written_versions"]["101"]
        b = second.get(CampaignItem, 101)
        b.payload_json = json.dumps({"title": "Второй"})
        second.commit()
        first.refresh(a)
        assert a.updated_at.isoformat() != own_version
        assert first.info["campaign_written_versions"]["101"] == own_version


def test_legacy_bulk_hero_delete_keeps_before_snapshot_and_bumps_version(engine):
    with Session(engine) as db:
        row = db.get(CampaignItem, 101)
        previous = row.updated_at
        db.query(MediaLink).filter(MediaLink.entity_type == "campaign_item", MediaLink.entity_id == "101", MediaLink.role == "hero").delete(synchronize_session=False)
        db.commit()
        assert row.updated_at != previous
        history = records(db)
        assert len(history) == 1
        assert len(history[0]["before"]["media"]) == 2
        assert len(history[0]["after"]["media"]) == 1


def test_provider_roundtrip_cannot_overwrite_after_commit_expired_the_row(engine):
    with Session(engine) as first, Session(engine) as second:
        row = first.get(CampaignItem, 101)
        version = row.updated_at.isoformat()
        stale_payload = json.loads(row.payload_json)
        lock_versions(first, 1, [101], version, ["qa-only"])
        first.commit()  # Existing provider paths deliberately release DB locks here.
        other = second.get(CampaignItem, 101)
        other.payload_json = json.dumps({"title": "Правка сотрудника"})
        second.commit()
        stale_payload["title"] = "Запоздавшая генерация"
        row.payload_json = json.dumps(stale_payload)
        with pytest.raises(HTTPException) as error:
            first.commit()
        assert error.value.detail["code"] == "CARD_VERSION_CONFLICT"
        first.rollback()
        assert json.loads(first.get(CampaignItem, 101).payload_json)["title"] == "Правка сотрудника"


def test_own_sequential_commits_advance_expected_version(engine):
    with Session(engine) as db:
        row = db.get(CampaignItem, 101)
        lock_versions(db, 1, [101], row.updated_at.isoformat(), ["qa-only"])
        for title in ("Первая правка", "Вторая правка"):
            row.payload_json = json.dumps({"title": title})
            db.commit()
        assert len(records(db)) == 2


def test_job_scope_pins_version_and_does_not_leak_actor_to_next_job(engine):
    from app.services.campaign_card_revisions import job_revision_scope
    with Session(engine) as db:
        version = db.get(CampaignItem, 101).updated_at.isoformat()
        with job_revision_scope(db, 1, [101], {"101": version}, ["qa-only"], 99, "Улучшить заголовок"):
            db.get(CampaignItem, 101).payload_json = json.dumps({"title": "Работа BORIS"})
            db.commit()
        assert "campaign_expected_versions" not in db.info
        assert "campaign_revision_actor" not in db.info
        entry = records(db)[0]
        assert entry["actor_type"] == "boris" and entry["job_id"] == 99


def test_protected_worker_candidate_pauses_on_conflict_without_replay(engine):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    backend = Path(__file__).resolve().parents[1]
    candidate = backend / "app/services/jobs.py"
    # Before the protected operator transaction this is explicitly a candidate
    # test. Scoped certification after apply must exercise its own staged worker.
    if "with job_revision_scope" not in candidate.read_text():
        candidate = backend.parent / "outputs/campaign-final-20261003/jobs-protected.candidate.py"
    tree = ast.parse(candidate.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_campaign_enhance")
    with Session(engine) as db:
        version = db.get(CampaignItem, 101).updated_at.isoformat()
        calls = []
        def provider(campaign_id, body, session, user):
            calls.append("one-attempt")
            row = session.get(CampaignItem, 101)
            stale = json.loads(row.payload_json)
            session.commit()
            with Session(engine) as other:
                other.get(CampaignItem, 101).payload_json = json.dumps({"title": "Сотрудник опередил"})
                other.commit()
            stale["title"] = "Результат старого задания"
            row.payload_json = json.dumps(stale)
            session.commit()
            return {"errors": []}
        C = SimpleNamespace(FFTextBody=lambda **kwargs: SimpleNamespace(**kwargs), ff_workflow_texts=provider)
        campaign = SimpleNamespace(id=1)
        namespace = {
            "_campaign_job_context": lambda *args: (C, campaign, SimpleNamespace(id=2), ["qa-only"], "qa-only"),
            "_campaign_mutation_locked": lambda *args: (False, {}, {}),
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(candidate), "exec"), namespace)
        result, status = namespace["_run_campaign_enhance"](db, SimpleNamespace(id=99), {
            "prompt": "Улучшить текст", "item_ids": [101], "remaining": [101], "expected_updated_at": {"101": version}})
        assert status == "awaiting_confirmation"
        assert result["code"] == "CARD_VERSION_CONFLICT"
        assert calls == ["one-attempt"]
        assert json.loads(db.get(CampaignItem, 101).payload_json)["title"] == "Сотрудник опередил"
