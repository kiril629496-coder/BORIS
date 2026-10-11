from pathlib import Path

TEAM = Path("/root/BORIS/backend/app/api/team.py").read_text(encoding="utf-8")


def test_team_membership_role_update_is_server_side_and_account_bound():
    assert '@router.patch("/memberships/{membership_id}")' in TEAM
    assert "_require_team_manage(db, user, row[\"account_id\"])" in TEAM
    assert 'role not in TEAM_ROLES or role == "OWNER"' in TEAM
    assert "UPDATE team_memberships SET role=:r" in TEAM
    assert "UPDATE user_account_access SET role=:r" in TEAM


def test_team_role_update_recomputes_canonical_permissions():
    for permission in (
        'perms["can_view"]',
        'perms["can_reply"]',
        'perms["can_manage_ads"]',
        'perms["can_view_reports"]',
        'perms["can_manage_team"]',
    ):
        assert permission in TEAM
