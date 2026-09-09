from __future__ import annotations

from sqlalchemy import text


def user_id(current_user) -> int:
    """
    Supports normal BORIS user objects and dictionary-like auth payloads.
    Fail closed when user identity cannot be resolved.
    """
    if current_user is None:
        raise RuntimeError("AUTH_USER_MISSING")

    if isinstance(current_user, dict):
        value = (
            current_user.get("id")
            or current_user.get("user_id")
            or current_user.get("sub")
        )
    else:
        value = (
            getattr(current_user, "id", None)
            or getattr(current_user, "user_id", None)
        )

    if value is None:
        raise RuntimeError("AUTH_USER_ID_MISSING")

    try:
        return int(value)
    except Exception as exc:
        raise RuntimeError("AUTH_USER_ID_INVALID") from exc


def ensure_default_pipeline(db, owner_user_id: int):
    pipeline = db.execute(
        text("""
            SELECT id, name
              FROM boris_crm_pipelines
             WHERE owner_user_id=:uid
               AND is_default=TRUE
             ORDER BY id
             LIMIT 1
        """),
        {"uid": owner_user_id},
    ).mappings().first()

    if pipeline:
        return pipeline["id"]

    pipeline_id = db.execute(
        text("""
            INSERT INTO boris_crm_pipelines
                (owner_user_id, name, code, is_default)
            VALUES
                (:uid, 'Продажи', 'sales', TRUE)
            RETURNING id
        """),
        {"uid": owner_user_id},
    ).scalar_one()

    stages = [
        ("Новый", "new", 10, "open"),
        ("Связались", "contacted", 20, "open"),
        ("Квалифицирован", "qualified", 30, "open"),
        ("В работе", "work", 40, "open"),
        ("КП / предложение", "proposal", 50, "open"),
        ("Договор", "contract", 60, "open"),
        ("Оплачено", "won", 70, "won"),
        ("Не реализовано", "lost", 80, "lost"),
    ]

    for name, code, position, semantic_type in stages:
        db.execute(
            text("""
                INSERT INTO boris_crm_stages
                    (
                        pipeline_id,
                        name,
                        code,
                        position,
                        semantic_type
                    )
                VALUES
                    (:pid,:name,:code,:position,:semantic)
            """),
            {
                "pid": pipeline_id,
                "name": name,
                "code": code,
                "position": position,
                "semantic": semantic_type,
            },
        )

    db.commit()
    return pipeline_id


def create_audit(
    db,
    owner_user_id: int,
    action: str,
    entity_type: str,
    entity_id=None,
    actor_type="user",
    actor_id=None,
    reason=None,
):
    db.execute(
        text("""
            INSERT INTO boris_crm_audit_log
                (
                    owner_user_id,
                    actor_type,
                    actor_id,
                    action,
                    entity_type,
                    entity_id,
                    reason
                )
            VALUES
                (
                    :uid,
                    :actor_type,
                    :actor_id,
                    :action,
                    :entity_type,
                    :entity_id,
                    :reason
                )
        """),
        {
            "uid": owner_user_id,
            "actor_type": actor_type,
            "actor_id": None if actor_id is None else str(actor_id),
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "reason": reason,
        },
    )


def find_contact_for_channel(
    db,
    owner_user_id: int,
    *,
    phone=None,
    email=None,
    avito_user_id=None,
    avito_chat_id=None,
):
    """
    Identity resolution v1.

    Deliberately conservative:
    never merge contacts using fuzzy names.
    """
    checks = [
        ("primary_phone", phone),
        ("primary_email", email),
        ("avito_user_id", avito_user_id),
        ("avito_chat_id", avito_chat_id),
    ]

    for field, value in checks:
        if not value:
            continue

        row = db.execute(
            text(f"""
                SELECT *
                  FROM boris_crm_contacts
                 WHERE owner_user_id=:uid
                   AND {field}=:value
                 ORDER BY id
                 LIMIT 1
            """),
            {"uid": owner_user_id, "value": str(value)},
        ).mappings().first()

        if row:
            return dict(row)

    return None

def crm_owner_user_id(current_user) -> int:
    """
    Resolve tenant owner for CRM.

    Owner -> own user id.
    Employee -> unique owner_user_id reachable through explicit
                user_account_access.

    Fail closed for missing or ambiguous employee scope.
    """
    from fastapi import HTTPException
    from sqlalchemy import text
    from app.crm.db import SessionLocal

    actor_id = user_id(current_user)

    role = ""

    if isinstance(current_user, dict):
        role = str(current_user.get("role") or "")
    else:
        role = str(getattr(current_user, "role", "") or "")

    role = role.strip().lower()

    if role != "employee":
        return int(actor_id)

    db = SessionLocal()

    try:
        owners = db.execute(
            text("""
                SELECT DISTINCT a.owner_user_id
                  FROM user_account_access x
                  JOIN accounts a
                    ON a.account_id=x.account_id
                 WHERE x.user_id=:uid
                   AND a.owner_user_id IS NOT NULL
                 ORDER BY a.owner_user_id
            """),
            {"uid": int(actor_id)}
        ).scalars().all()

        owners = sorted({
            int(x)
            for x in owners
            if x is not None
        })

        if not owners:
            raise HTTPException(
                status_code=403,
                detail="CRM employee has no owner scope",
            )

        if len(owners) != 1:
            raise HTTPException(
                status_code=403,
                detail="CRM employee owner scope is ambiguous",
            )

        return owners[0]

    finally:
        db.close()
