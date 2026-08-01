from sqlalchemy.orm import Session
from app.models.campaign import Campaign


def get_all(db: Session):
    return db.query(Campaign).all()


def create(db: Session, name: str):
    obj = Campaign(name=name)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj