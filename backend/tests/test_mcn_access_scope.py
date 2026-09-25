# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api import mcn_phone as API


class _Result:
    def __init__(self, row=None):
        self._row=row
    def first(self):
        return self._row


class _DB:
    def __init__(self, owner_id=200, allowed=False, exists=True):
        self.owner_id=owner_id
        self.allowed=allowed
        self.exists=exists
    def execute(self, stmt, params=None):
        sql=str(stmt)
        if "SELECT owner_user_id FROM accounts" in sql:
            return _Result((self.owner_id,) if self.exists else None)
        if "FROM user_account_access" in sql:
            return _Result((1,) if self.allowed else None)
        raise AssertionError("unexpected SQL: "+sql)
    def close(self):
        pass


class MCNAccessScopeTests(unittest.TestCase):
    def _check(self, user, *, owner_id=200, allowed=False, exists=True, owner_only=False):
        db=_DB(owner_id=owner_id,allowed=allowed,exists=exists)
        with patch("app.db.session.SessionLocal",return_value=db):
            return API._assert_account("client-account",user,owner_only=owner_only)

    def test_platform_owner_can_manage_visible_client_portfolio(self):
        self.assertIsNone(self._check({"id":100,"role":"owner"},owner_id=200,owner_only=True))

    def test_real_account_owner_can_manage_own_mcn(self):
        self.assertIsNone(self._check({"id":200,"role":"client"},owner_id=200,owner_only=True))

    def test_employee_with_can_view_can_read_but_cannot_change_operator(self):
        self.assertIsNone(self._check({"id":300,"role":"employee"},owner_id=200,allowed=True,owner_only=False))
        with self.assertRaises(HTTPException) as ctx:
            self._check({"id":300,"role":"employee"},owner_id=200,allowed=True,owner_only=True)
        self.assertEqual(ctx.exception.status_code,403)

    def test_unlisted_user_is_denied(self):
        with self.assertRaises(HTTPException) as ctx:
            self._check({"id":301,"role":"employee"},owner_id=200,allowed=False,owner_only=False)
        self.assertEqual(ctx.exception.status_code,403)

    def test_missing_account_stays_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self._check({"id":100,"role":"owner"},exists=False,owner_only=False)
        self.assertEqual(ctx.exception.status_code,404)


if __name__=="__main__":
    unittest.main()
