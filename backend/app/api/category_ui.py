# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.auth import get_current_user
from app.db.session import SessionLocal
from app.models.account import Account
from app.services.niche_resolver import (
    resolve_niche,
    derive_niche_from_profile,
)
from app.services.exception_observability import observe_suppressed


router = APIRouter(
    prefix="/api/category-ui",
    tags=["category-ui"],
)


class DetectRequest(BaseModel):
    account_id: Optional[str] = None
    business_text: str = ""


class ConfirmRequest(BaseModel):
    account_id: Optional[str] = None
    niche: str
    business_text: str = ""


def _user_id(user) -> int:
    try:
        return int(getattr(user, "id"))
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Не удалось определить пользователя",
        )


def _resolve_account(db, user, requested: Optional[str]) -> str:
    uid = _user_id(user)

    requested = (requested or "").strip()

    if requested:
        owned = db.execute(
            text("""
                SELECT 1
                FROM accounts
                WHERE account_id=:a
                  AND owner_user_id=:u
                LIMIT 1
            """),
            {"a": requested, "u": uid},
        ).first()

        if owned:
            return requested

        granted = db.execute(
            text("""
                SELECT 1
                FROM user_account_access
                WHERE user_id=:u
                  AND account_id=:a
                  AND can_view=TRUE
                LIMIT 1
            """),
            {"u": uid, "a": requested},
        ).first()

        if granted:
            return requested

        raise HTTPException(
            status_code=403,
            detail="Нет доступа к аккаунту",
        )

    own = str(
        getattr(user, "account_id", "") or ""
    ).strip()

    if own:
        return own

    rows = db.execute(
        text("""
            SELECT DISTINCT account_id
            FROM (
                SELECT account_id
                FROM accounts
                WHERE owner_user_id=:u

                UNION

                SELECT account_id
                FROM user_account_access
                WHERE user_id=:u
                  AND can_view=TRUE
            ) q
            WHERE account_id IS NOT NULL
              AND account_id <> ''
            ORDER BY account_id
        """),
        {"u": uid},
    ).fetchall()

    ids = [str(x[0]) for x in rows if x and x[0]]

    if len(ids) == 1:
        return ids[0]

    if not ids:
        raise HTTPException(
            status_code=403,
            detail="Нет доступного аккаунта",
        )

    raise HTTPException(
        status_code=409,
        detail="Сначала выберите аккаунт",
    )


@router.get("/status")
def category_status(
    account_id: Optional[str] = None,
    user=Depends(get_current_user),
):
    db = SessionLocal()

    try:
        acc = _resolve_account(db, user, account_id)

        row = db.execute(
            text("""
                SELECT
                    account_id,
                    name,
                    company_niche,
                    company_description,
                    company_website
                FROM accounts
                WHERE account_id=:a
                LIMIT 1
            """),
            {"a": acc},
        ).mappings().first()

        if not row:
            raise HTTPException(404, "Аккаунт не найден")

        resolved = resolve_niche(acc, db=db) or {}

        return {
            "status": "ok",
            "account": dict(row),
            "resolved": resolved,
        }

    finally:
        db.close()


@router.post("/detect")
def category_detect(
    req: DetectRequest,
    user=Depends(get_current_user),
):
    db = SessionLocal()

    try:
        acc = _resolve_account(db, user, req.account_id)

        row = db.execute(
            text("""
                SELECT
                    account_id,
                    name,
                    company_niche,
                    company_description
                FROM accounts
                WHERE account_id=:a
                LIMIT 1
            """),
            {"a": acc},
        ).mappings().first()

        if not row:
            raise HTTPException(404, "Аккаунт не найден")

        text_value = (req.business_text or "").strip()

        if not text_value:
            existing = resolve_niche(acc, db=db) or {}

            return {
                "status": "ok",
                "account_id": acc,
                "suggestion": existing.get("niche") or "",
                "source": existing.get("source") or "resolver",
                "confidence": existing.get("confidence"),
                "reason": existing.get("reason") or "",
            }

        derived = derive_niche_from_profile(
            str(row.get("name") or ""),
            text_value,
        ) or {}

        suggestion = str(
            derived.get("value") or
            derived.get("niche") or
            ""
        ).strip()

        # Existing resolver is the second source of truth.
        if not suggestion:
            existing = resolve_niche(acc, db=db) or {}
            suggestion = str(
                existing.get("niche") or ""
            ).strip()

        # Fail-safe UX fallback:
        # user may still edit/confirm the concise category manually.
        if not suggestion:
            suggestion = text_value[:160].strip()

        return {
            "status": "ok",
            "account_id": acc,
            "suggestion": suggestion,
            "source": (
                derived.get("source")
                or "business_text"
            ),
            "confidence": derived.get("confidence"),
            "reason": derived.get("reason") or "",
        }

    finally:
        db.close()


@router.post("/confirm")
def category_confirm(
    req: ConfirmRequest,
    user=Depends(get_current_user),
):
    niche = (req.niche or "").strip()

    if len(niche) < 2:
        raise HTTPException(
            status_code=422,
            detail="Укажите категорию",
        )

    if len(niche) > 255:
        raise HTTPException(
            status_code=422,
            detail="Название категории слишком длинное",
        )

    db = SessionLocal()

    try:
        acc = _resolve_account(db, user, req.account_id)

        row = db.execute(
            text("""
                SELECT
                    account_id,
                    company_niche,
                    company_description
                FROM accounts
                WHERE account_id=:a
                FOR UPDATE
            """),
            {"a": acc},
        ).mappings().first()

        if not row:
            raise HTTPException(404, "Аккаунт не найден")

        business_text = (
            req.business_text or ""
        ).strip()

        if business_text and not row["company_description"]:
            db.execute(
                text("""
                    UPDATE accounts
                    SET
                        company_niche=:n,
                        company_description=:d
                    WHERE account_id=:a
                """),
                {
                    "n": niche,
                    "d": business_text,
                    "a": acc,
                },
            )
        else:
            db.execute(
                text("""
                    UPDATE accounts
                    SET company_niche=:n
                    WHERE account_id=:a
                """),
                {
                    "n": niche,
                    "a": acc,
                },
            )

        db.commit()

        # CATEGORY_CONFIRM_READINESS_REFRESH_V1
        # Re-read through the canonical resolver immediately after commit.
        # Home/readiness then sees accounts.company_niche as source=profile.
        try:
            from app import onboarding as _category_onboarding
            _category_onboarding.stage_of(acc, db=db)
        except Exception as _suppressed_exc:
            # Saving the confirmed category must not be rolled back merely
            # because a secondary readiness refresh failed.
            observe_suppressed(__name__, _suppressed_exc, line=333)

        result = resolve_niche(acc, db=db) or {}

        return {
            "status": "ok",
            "account_id": acc,
            "company_niche": niche,
            "resolved": result,
        }

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
