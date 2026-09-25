import unittest
from unittest.mock import patch

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as SQLAlchemySession

from app.db import session as db_session


class DbSessionDisconnectCloseTests(unittest.TestCase):
    @staticmethod
    def _operational_error(message: str, invalidated: bool = False):
        return OperationalError(
            "ROLLBACK",
            {},
            RuntimeError(message),
            connection_invalidated=invalidated,
        )

    def test_sessionlocal_uses_resilient_session(self):
        db = db_session.SessionLocal()
        try:
            self.assertIsInstance(db, db_session.ResilientSession)
        finally:
            db.close()

    def test_physical_disconnect_during_close_is_recovered(self):
        err = self._operational_error("SSL connection has been closed unexpectedly")
        db = db_session.ResilientSession(bind=db_session.engine)
        with patch.object(SQLAlchemySession, "close", side_effect=err), \
             patch.object(db_session.engine, "dispose") as dispose, \
             self.assertLogs("app.db.session", level="WARNING") as logs:
            self.assertIsNone(db.close())
            dispose.assert_called_once()
            self.assertTrue(any("DB_SESSION_CLOSE_DISCONNECT_RECOVERED" in line for line in logs.output))

    def test_connection_invalidated_flag_is_recovered(self):
        err = self._operational_error("driver-specific disconnect", invalidated=True)
        db = db_session.ResilientSession(bind=db_session.engine)
        with patch.object(SQLAlchemySession, "close", side_effect=err), \
             patch.object(db_session.engine, "dispose") as dispose:
            self.assertIsNone(db.close())
            dispose.assert_called_once()

    def test_non_disconnect_dbapi_error_is_not_hidden(self):
        err = self._operational_error("syntax error near FROM", invalidated=False)
        db = db_session.ResilientSession(bind=db_session.engine)
        with patch.object(SQLAlchemySession, "close", side_effect=err), \
             patch.object(db_session.engine, "dispose") as dispose:
            with self.assertRaises(OperationalError):
                db.close()
            dispose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
