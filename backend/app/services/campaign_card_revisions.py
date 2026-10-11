"""Atomic versions and durable local Campaign content history."""
from datetime import datetime, timezone, timedelta
import json
import time
import uuid
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session

CONTENT_KEYS = ("title", "description", "price", "city", "address", "category",
                "category_path", "template_id", "params", "images", "banner",
                "banner_url", "phone", "manager", "delivery")
CONFLICT_MESSAGE = "Есть более новая версия карточки. Ваши изменения сохранены в форме."


def next_version(previous):
    now = datetime.now(timezone.utc)
    if previous is not None:
        prior = previous.replace(tzinfo=timezone.utc) if previous.tzinfo is None else previous
        now = max(now, prior + timedelta(microseconds=1))
    return now


def conflict(item_id=None):
    return HTTPException(409, detail={"code": "CARD_VERSION_CONFLICT",
        "message": CONFLICT_MESSAGE, "item_id": item_id})


def parse_version(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError):
        return None


def lock_versions(db, campaign_id, item_ids, expected, allowed_accounts):
    """Compare inside the transaction, under ordered PostgreSQL row locks."""
    from app.models.campaign_item import CampaignItem
    ids = sorted(set(int(x) for x in item_ids))
    if not ids:
        raise HTTPException(422, detail={"message": "Выберите объявления для изменения"})
    rows = (db.query(CampaignItem).filter(CampaignItem.campaign_id == campaign_id,
            CampaignItem.id.in_(ids), CampaignItem.account_id.in_(allowed_accounts or [""]))
            .order_by(CampaignItem.id).populate_existing().with_for_update().all())
    if len(rows) != len(ids):
        raise HTTPException(404, detail={"message": "Объявление не найдено"})
    versions = expected if isinstance(expected, dict) else {str(i): expected for i in ids}
    for row in rows:
        requested = parse_version(versions.get(str(row.id)))
        if requested is None or requested != parse_version(row.updated_at):
            raise conflict(row.id)
    db.info.setdefault("campaign_expected_versions", {}).update({str(row.id): row.updated_at.isoformat() for row in rows})
    db.info["campaign_committed_versions"] = dict(db.info["campaign_expected_versions"])
    pending = db.info.setdefault("campaign_revision_before", {})
    for row in rows:
        if row.id not in pending:
            pending[row.id] = (_snapshot(db.connection(), row.id), time.monotonic())
    return rows


def _snapshot(connection, item_id):
    from app.models.campaign_item import CampaignItem
    from app.models.media_link import MediaLink
    row = connection.execute(select(CampaignItem.__table__).where(
        CampaignItem.id == item_id)).mappings().first()
    if row is None:
        return None
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    data = {key: payload.get(key) for key in CONTENT_KEYS}
    data["city"] = payload.get("city") or row["geo"]
    data["hero_media_id"] = row["hero_media_id"]
    links = connection.execute(select(MediaLink.media_id, MediaLink.role, MediaLink.position)
        .where(MediaLink.entity_type == "campaign_item", MediaLink.entity_id == str(item_id))
        .order_by(MediaLink.position, MediaLink.id)).mappings().all()
    data["media"] = [dict(link) for link in links]
    return {"account_id": row["account_id"], "campaign_id": row["campaign_id"],
            "version": row["updated_at"].isoformat() if row["updated_at"] else None,
            "content": data}


def _before_flush(db, flush_context, instances):
    from app.models.campaign_item import CampaignItem
    from app.models.media_link import MediaLink
    items, media_ids = {}, set()
    for obj in list(db.dirty) + list(db.deleted) + list(db.new):
        if isinstance(obj, CampaignItem) and obj.id is not None:
            items[int(obj.id)] = obj
        elif isinstance(obj, MediaLink) and obj.entity_type == "campaign_item":
            try:
                media_ids.add(int(obj.entity_id))
            except (TypeError, ValueError):
                continue
    if not items and not media_ids:
        return
    pending = db.info.setdefault("campaign_revision_before", {})
    for item_id in sorted(set(items) | media_ids):
        _check_expected(db, item_id)
        if item_id not in pending:
            before = _snapshot(db.connection(), item_id)
            if before is not None:
                pending[item_id] = (before, time.monotonic())
        if item_id in media_ids:
            row = items.get(item_id) or db.get(CampaignItem, item_id)
            if row is not None and row not in db.deleted:
                row.updated_at = next_version(row.updated_at)
                items[item_id] = row
    db.info.setdefault("campaign_version_objects", {}).update(items)


def _after_flush(db, flush_context):
    for item_id, row in db.info.pop("campaign_version_objects", {}).items():
        if row not in db.deleted and row.updated_at is not None:
            db.info.setdefault("campaign_written_versions", {})[str(item_id)] = row.updated_at.isoformat()
            if str(item_id) in db.info.get("campaign_expected_versions", {}):
                db.info["campaign_expected_versions"][str(item_id)] = row.updated_at.isoformat()


def _before_commit(db):
    # Include link changes flushed earlier by MediaService; history and content
    # commit together. Never synthesize before/after for old audit-only records.
    db.flush()
    pending = db.info.pop("campaign_revision_before", {})
    if not pending:
        return
    from app.models.storage import Storage
    actor = dict(db.info.get("campaign_revision_actor") or {
        "actor_id": None, "actor": "Система", "actor_type": "system"})
    for item_id, (before, started) in pending.items():
        after = _snapshot(db.connection(), item_id)
        if after is None:
            continue
        changed = [key for key in after["content"]
                   if before["content"].get(key) != after["content"].get(key)]
        if not changed:
            continue
        revision = uuid.uuid4().hex
        record = {"revision": revision, "item_id": item_id,
            "campaign_id": after["campaign_id"], **actor,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_ms": round((time.monotonic() - started) * 1000),
            "changed_fields": changed, "before": before["content"], "after": after["content"],
            "before_version": before["version"], "after_version": after["version"],
            "reason": db.info.get("campaign_revision_reason") or "Изменение карточки",
            "job_id": db.info.get("campaign_revision_job_id")}
        db.add(Storage(account_id=after["account_id"],
            key=f"campaign_card_revision:{item_id}:{revision}",
            value=json.dumps(record, ensure_ascii=False)))


def _after_rollback(db):
    if "campaign_committed_versions" in db.info:
        db.info["campaign_expected_versions"] = dict(db.info["campaign_committed_versions"])
    for key in ("campaign_revision_before", "campaign_version_objects", "campaign_written_versions"):
        db.info.pop(key, None)



def _before_media_bulk_write(state):
    """Legacy MediaService uses ORM bulk deletes, which bypass before_flush."""
    if not (state.is_delete or state.is_update):
        return
    table = getattr(state.statement, "table", None)
    if getattr(table, "name", "") != "media_links":
        return
    from app.models.media_link import MediaLink
    from app.models.campaign_item import CampaignItem
    query = select(MediaLink.entity_id).where(MediaLink.entity_type == "campaign_item")
    if state.statement.whereclause is not None:
        query = query.where(state.statement.whereclause)
    ids = {int(value) for value in state.session.execute(query).scalars()
           if str(value).isdigit()}
    pending = state.session.info.setdefault("campaign_revision_before", {})
    for item_id in sorted(ids):
        _check_expected(state.session, item_id)
        if item_id not in pending:
            before = _snapshot(state.session.connection(), item_id)
            if before is not None:
                pending[item_id] = (before, time.monotonic())
        row = state.session.get(CampaignItem, item_id)
        if row is not None:
            row.updated_at = next_version(row.updated_at)


def _check_expected(db, item_id):
    expected = db.info.get("campaign_expected_versions", {}).get(str(item_id))
    if expected is None:
        return
    from app.models.campaign_item import CampaignItem
    actual = db.connection().execute(select(CampaignItem.updated_at).where(
        CampaignItem.id == item_id).with_for_update()).scalar()
    if parse_version(actual) != parse_version(expected):
        db.info["campaign_version_conflict"] = item_id
        raise conflict(item_id)


def _after_commit(db):
    if "campaign_expected_versions" in db.info:
        db.info["campaign_committed_versions"] = dict(db.info["campaign_expected_versions"])


from contextlib import contextmanager


@contextmanager
def job_revision_scope(db, campaign_id, ids, expected, allowed, job_id, reason):
    keys = ("campaign_expected_versions", "campaign_committed_versions",
            "campaign_revision_actor", "campaign_revision_job_id", "campaign_revision_reason",
            "campaign_version_conflict")
    previous = {key: db.info.get(key) for key in keys}
    for key in keys:
        db.info.pop(key, None)
    try:
        # Pre-version jobs have no invented historical token; pin their actual
        # execution snapshot and still protect the subsequent provider round-trip.
        if expected is None:
            from app.models.campaign_item import CampaignItem
            expected = {str(row.id): row.updated_at.isoformat()
                        for row in db.query(CampaignItem).filter(
                            CampaignItem.campaign_id == campaign_id,
                            CampaignItem.id.in_(ids),
                            CampaignItem.account_id.in_(allowed)).all()}
        lock_versions(db, campaign_id, ids, expected, allowed)
        db.info["campaign_revision_actor"] = {"actor_id": None, "actor": "BORIS", "actor_type": "boris"}
        db.info["campaign_revision_job_id"] = job_id
        db.info["campaign_revision_reason"] = reason or "BORIS изменил карточку"
        yield
        if db.info.get("campaign_version_conflict"):
            raise conflict(db.info["campaign_version_conflict"])
    finally:
        for key in keys:
            if previous[key] is None:
                db.info.pop(key, None)
            else:
                db.info[key] = previous[key]


def install():
    if event.contains(Session, "before_flush", _before_flush):
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "after_flush_postexec", _after_flush)
    event.listen(Session, "before_commit", _before_commit)
    event.listen(Session, "after_rollback", _after_rollback)
    event.listen(Session, "after_commit", _after_commit)
    event.listen(Session, "do_orm_execute", _before_media_bulk_write)


# Preserve the exact versions written by this request. A post-commit refresh
# could already observe somebody else's edit and must not become its successor token.
from fastapi.routing import APIRoute
from starlette.responses import Response
from sqlalchemy.orm.exc import StaleDataError


class CampaignRevisionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                response = await original(request)
            except StaleDataError:
                raise conflict()
            db = getattr(request.state, "campaign_revision_db", None)
            if db is not None and db.info.get("campaign_version_conflict"):
                raise conflict(db.info["campaign_version_conflict"])
            versions = db.info.get("campaign_written_versions", {}) if db is not None else {}
            if (versions or request.method == "GET") and 200 <= response.status_code < 300 and "application/json" in response.headers.get("content-type", ""):
                payload = json.loads(response.body)
                if isinstance(payload, dict):
                    payload["card_revision_contract"] = 1
                    if versions:
                        payload["item_versions"] = dict(versions)
                    headers = dict(response.headers)
                    headers.pop("content-length", None)
                    return Response(json.dumps(payload, ensure_ascii=False), status_code=response.status_code,
                                    headers=headers, background=response.background)
            return response
        return handler


def guard_request(request, db, user, body, allowed_accounts):
    import re
    request.state.campaign_revision_db = db
    db.info["campaign_http_request"] = True
    db.info["campaign_revision_actor"] = {
        "actor_id": getattr(user, "id", None),
        "actor": str(getattr(user, "email", "") or "Сотрудник"),
        "actor_type": "owner" if getattr(user, "role", "") == "owner" else "employee",
    }
    db.info["campaign_revision_reason"] = "Правка карточки в BORIS"
    path = request.url.path.rstrip("/")
    match = re.fullmatch(r"/api/campaign/(\d+)/(?:item/(\d+)|workflow/(mutate|autopilot-mutate|price-confirm|category/\d+|bulk-transform|photo-stack|banners|auto-media|enhance|texts))", path)
    if not match:
        return
    if match.group(3) == "bulk-transform" and (body.get("preview_only", True) or not body.get("confirmed")):
        return
    ids = body.get("item_ids") or []
    if match.group(2):
        ids = [int(match.group(2))]
    elif str(match.group(3) or "").startswith("category/"):
        ids = [int(path.rsplit("/", 1)[1])]
    lock_versions(db, int(match.group(1)), ids, body.get("expected_updated_at"), allowed_accounts)
