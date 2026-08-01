# FastAPI Routes for SaaS БОРИС Landing Page Editor
# Save to: backend/app/api/landing.py

import os
import json
import psycopg2
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

# Реальные зависимости проекта — авторизация через существующий auth.py (SQLAlchemy + JWT)
from app.api.auth import get_current_user, require_owner
from app.models.user import User

router = APIRouter(prefix="/api", tags=["landing"])


def get_db_connection():
    """Отдельное сырое psycopg2-подключение для простой таблицы landing_content —
    не смешиваем с основной SQLAlchemy-сессией проекта (SessionLocal), она не нужна
    для этой изолированной таблицы."""
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        yield conn
    finally:
        conn.close()


# Pydantic Schemas
class LandingContentUpdate(BaseModel):
    block_key: str = Field(..., description="Unique key for the block")
    value: str = Field(..., description="Value to store (can be stringified JSON or plain text)")

class RollbackRequest(BaseModel):
    block_key: str = Field(..., description="Key of the block to rollback")

class ContentResponse(BaseModel):
    success: bool
    content: Dict[str, Any]


@router.get("/landing/content", response_model=ContentResponse)
async def get_landing_content(conn=Depends(get_db_connection)):
    """
    Public route to fetch landing page content (unauthorized, cached)
    """
    try:
        # Using psycopg2 connection
        with conn.cursor() as cursor:
            cursor.execute("SELECT block_key, content_type, value FROM landing_content")
            rows = cursor.fetchall()
            
        content_dict = {}
        for row in rows:
            key, content_type, value = row
            if content_type == 'number':
                try:
                    content_dict[key] = float(value) if '.' in value else int(value)
                except ValueError:
                    content_dict[key] = value
            elif content_type == 'json':
                try:
                    content_dict[key] = json.loads(value)
                except json.JSONDecodeError:
                    content_dict[key] = value
            else:
                content_dict[key] = value
                
        return {"success": True, "content": content_dict}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error: {str(e)}"
        )


@router.put("/admin/landing/content", status_code=status.HTTP_200_OK)
async def update_landing_content(
    payload: LandingContentUpdate,
    conn=Depends(get_db_connection),
    current_user=Depends(get_current_user) # Only 'owner' role can edit
):
    """
    Protected route to update a specific content block.
    Saves the previous state to landing_content_history.
    """
    # Guard role check
    if getattr(current_user, 'role', 'client') != 'owner':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только владелец (owner) может редактировать контент лендинга"
        )
        
    block_key = payload.block_key
    value = payload.value
    
    try:
        with conn.cursor() as cursor:
            # 1. Fetch current value for history
            cursor.execute("SELECT value FROM landing_content WHERE block_key = %s", (block_key,))
            row = cursor.fetchone()
            
            # If the value is the same, no need to update or write history
            if row and row[0] == value:
                return {"success": True, "message": "Значение не изменилось"}
                
            prev_value = row[0] if row else ""
            
            # 2. Write to history if previous record existed
            if row:
                cursor.execute(
                    "INSERT INTO landing_content_history (block_key, previous_value, changed_at) VALUES (%s, %s, %s)",
                    (block_key, prev_value, datetime.utcnow())
                )
                
            # 3. Upsert content
            cursor.execute(
                """
                INSERT INTO landing_content (block_key, value, updated_at) 
                VALUES (%s, %s, %s)
                ON CONFLICT (block_key) 
                DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                """,
                (block_key, value, datetime.utcnow())
            )
            
            conn.commit()
            
        return {"success": True, "block_key": block_key, "updated_to": value}
    except Exception as e:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update database: {str(e)}"
        )


@router.get("/admin/landing/history/{block_key}")
async def get_landing_content_history(
    block_key: str,
    conn=Depends(get_db_connection),
    current_user=Depends(get_current_user)
):
    """
    Protected route — returns change history for a specific block.
    Admin panel calls this via GET /api/admin/landing/history/{key}
    (missing from Gemini's generated route file both times — added manually,
    verify it doesn't get dropped again on next regeneration).
    """
    if getattr(current_user, 'role', 'client') != 'owner':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только владелец (owner) может просматривать историю изменений"
        )

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, previous_value, changed_at FROM landing_content_history
                WHERE block_key = %s
                ORDER BY changed_at DESC LIMIT 20
                """,
                (block_key,)
            )
            rows = cursor.fetchall()

        history = [
            {"id": r[0], "previous_value": r[1], "changed_at": r[2].isoformat()}
            for r in rows
        ]
        return {"success": True, "history": history}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch history: {str(e)}"
        )


@router.post("/admin/landing/rollback")
async def rollback_landing_content(
    payload: RollbackRequest,
    conn=Depends(get_db_connection),
    current_user=Depends(get_current_user)
):
    """
    Rollback a specific block key to its previous version
    """
    if getattr(current_user, 'role', 'client') != 'owner':
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для выполнения отката изменений"
        )
        
    block_key = payload.block_key
    
    try:
        with conn.cursor() as cursor:
            # 1. Get latest historical value
            cursor.execute(
                """
                SELECT id, previous_value FROM landing_content_history 
                WHERE block_key = %s 
                ORDER BY changed_at DESC LIMIT 1
                """,
                (block_key,)
            )
            history_row = cursor.fetchone()
            
            if not history_row:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="История изменений для данного блока отсутствует"
                )
                
            history_id, previous_value = history_row
            
            # 2. Update landing_content back to previous value
            cursor.execute(
                "UPDATE landing_content SET value = %s, updated_at = %s WHERE block_key = %s",
                (previous_value, datetime.utcnow(), block_key)
            )
            
            # 3. Delete this history point so we can roll back further next time
            cursor.execute("DELETE FROM landing_content_history WHERE id = %s", (history_id,))
            
            conn.commit()
            
        return {"success": True, "block_key": block_key, "rolled_back_to": previous_value}
    except HTTPException as he:
        raise he
    except Exception as e:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rollback failed: {str(e)}"
        )
