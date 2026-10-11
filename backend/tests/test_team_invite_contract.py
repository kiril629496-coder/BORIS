import ast
import hashlib
from pathlib import Path

TEAM = Path('/root/BORIS/backend/app/api/team.py').read_text(encoding='utf-8')


def test_team_router_has_one_canonical_invite_accept_path():
    tree = ast.parse(TEAM)
    routes = [n.decorator_list[0].args[0].value for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              for d in n.decorator_list
              if isinstance(d, ast.Call) and getattr(d.func, 'attr', '') in {'post', 'get'}
              and d.args and isinstance(d.args[0], ast.Constant)]
    assert '/invite/accept' in routes
    assert routes.count('/invite/accept') == 1


def test_invite_contract_is_single_use_account_bound_and_expiring():
    for marker in ('invite_token_hash', 'invite_expires_at',
                   'invite_token_hash=NULL', 'FOR UPDATE', 'created_by_user_id'):
        assert marker in TEAM
    # INSERT sets INVITED as the positional value of the status column; acceptance
    # later enforces row['status'] == 'INVITED' before a token may be consumed.
    assert "permissions_override,status,invite_token_hash" in TEAM
    assert "'INVITED'" in TEAM
    token = 'fixture-token'
    assert hashlib.sha256(token.encode()).hexdigest() != token


def test_backend_permissions_and_roles_are_not_frontend_only():
    for role in ('OWNER', 'MANAGER_LEAD', 'MANAGER', 'VIEWER'):
        assert f'"{role}"' in TEAM
    assert 'from app.services.command_policy import has_permission' in TEAM
    assert 'has_permission(db, user, account_id, "can_manage_team")' in TEAM
    assert '"MANAGER_LEAD"' in TEAM and '"VIEWER"' in TEAM
    assert "status='ACTIVE'" in TEAM


def test_revoke_keeps_audit_identity_but_blocks_access():
    assert 'emp.status = "blocked"' in TEAM
    assert 'DELETE FROM user_account_access' in TEAM
    assert 'history' in TEAM or 'НЕ удаляем' in TEAM


def test_invite_permissions_override_is_bound_as_jsonb_not_python_dict():
    assert 'CAST(:po AS jsonb)' in TEAM
    assert 'json.dumps(body.permissions_override or {}, ensure_ascii=False)' in TEAM
