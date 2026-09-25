"""
BORIS CRM schema v1.

CRM is BORIS-owned even when amoCRM / Bitrix24 is connected.

External CRMs are synchronization targets/sources, NOT the internal
source of truth for BORIS automation and AI.
"""

from sqlalchemy import text
from app.crm.db import SessionLocal


DDL = 'SELECT 1 /* BORIS_SCHEMA_MIGRATION_049_OWNED */'


def install_schema():
    db = SessionLocal()
    try:
        for statement in DDL.split(";"):
            statement = statement.strip()
            if not statement:
                continue
            db.execute(text(statement))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    install_schema()
    print("CRM_SCHEMA_OK")
