"""Invite conflict handling preserves authorization and database failure semantics."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import datetime
import secrets
import hashlib
import json
import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

@pytest.mark.parametrize("constraint,expected", [
    ("uq_team_memberships_active_phone", 409),
    ("uq_team_memberships_invite_hash", None),
    (None, None),
])
def test_invite_conflict_rolls_back_and_closes(constraint, expected):
    tree = ast.parse(Path(__file__).parents[1].joinpath("app/api/team.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "create_invite")
    node.decorator_list = []
    node.args.defaults = []
    for arg in node.args.args:
        arg.annotation = None
    db = Mock()
    original = Exception("private SQL detail must not reach client")
    original.diag = SimpleNamespace(constraint_name=constraint)
    error = IntegrityError("INSERT", {}, original)
    db.execute.side_effect = error
    require = Mock()
    namespace = dict(
        enforce_messenger_rate=Mock(), TEAM_ROLES={"MANAGER"}, _phone=lambda s:s,
        HTTPException=HTTPException, SessionLocal=lambda:db, _require_team_manage=require,
        secrets=secrets, datetime=datetime, text=text, json=json, hashlib=hashlib,
        IntegrityError=IntegrityError,
    )
    exec(compile(ast.Module(body=[node],type_ignores=[]),"<invite>","exec"),namespace)
    body=SimpleNamespace(role="MANAGER",phone="70000000000",name="QA",expires_hours=24,
                         account_id="qa",email="qa@example.com",permissions_override={})
    with pytest.raises(HTTPException if expected else IntegrityError) as caught:
        namespace["create_invite"](body,SimpleNamespace(id=1))
    if expected:
        assert caught.value.status_code == expected
        assert "телефоном" in caught.value.detail
        assert "private SQL" not in caught.value.detail
    else:
        assert caught.value is error
    require.assert_called_once()
    db.rollback.assert_called_once()
    db.close.assert_called_once()
    db.commit.assert_not_called()
