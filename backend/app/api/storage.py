from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any
import json
from sqlalchemy.orm import Session
from fastapi import Depends
from app.db.session import get_db, engine
from app.db.base import Base
from app.models.storage import Storage

router = APIRouter(prefix="/api/storage", tags=["storage"])

# создаём таблицу, если её ещё нет (безопасно — не трогает существующие)
Base.metadata.create_all(bind=engine, tables=[Storage.__table__])

class SaveRequest(BaseModel):
    account_id: str = "default"
    key: str
    # Any, а не dict|list: storage — универсальное key-value хранилище,
    # сюда кладут и скаляры (например use_banner = true). Раньше такие
    # сохранения молча падали с 422 и настройка терялась.
    value: Any

@router.post("/save")
def save(req: SaveRequest, db: Session = Depends(get_db)):
    row = db.query(Storage).filter(Storage.account_id == req.account_id, Storage.key == req.key).first()
    value_json = json.dumps(req.value, ensure_ascii=False)
    if row:
        row.value = value_json
    else:
        row = Storage(account_id=req.account_id, key=req.key, value=value_json)
        db.add(row)
    db.commit()
    return {"status": "ok"}

@router.get("/load")
def load(account_id: str = "default", key: str = ""):
    from app.db.session import SessionLocal
    db = SessionLocal()
    try:
        row = db.query(Storage).filter(Storage.account_id == account_id, Storage.key == key).first()
        if row:
            return {"status": "ok", "value": json.loads(row.value)}
        return {"status": "ok", "value": None}
    finally:
        db.close()
