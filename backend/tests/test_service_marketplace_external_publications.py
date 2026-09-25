import json
from unittest.mock import patch

from app.services import service_marketplace as marketplace


def test_external_verified_publication_upsert_is_idempotent(tmp_path):
    publications = tmp_path / "publications.json"
    with patch.object(marketplace, "PUBLICATIONS_FILE", publications):
        first = marketplace.upsert_verified_external_publication(
            platform="sdelkino.com",
            url="https://example.test/post/1",
            target_url="https://boris-ai.pro/software-dev/",
            title="BORIS Development",
            verified_at="2026-09-11T20:44:57+00:00",
            source="confirmed_guest_post",
        )
        second = marketplace.upsert_verified_external_publication(
            platform="sdelkino.com",
            url="https://example.test/post/1/",
            target_url="https://boris-ai.pro/software-dev/",
            title="BORIS Development",
            verified_at="2026-09-11T20:45:57+00:00",
            source="confirmed_guest_post",
        )

    rows = json.loads(publications.read_text(encoding="utf-8"))
    assert first["created"] is True
    assert second["created"] is False
    assert len(rows) == 1
    assert rows[0]["verified"] is True
    assert rows[0]["site_url"] == "https://boris-ai.pro/software-dev/"
    assert rows[0]["source"] == "confirmed_guest_post"


def test_external_verified_publication_requires_absolute_urls(tmp_path):
    with patch.object(marketplace, "PUBLICATIONS_FILE", tmp_path / "publications.json"):
        try:
            marketplace.upsert_verified_external_publication(
                platform="guest", url="/post/1", target_url="https://boris-ai.pro/software-dev/"
            )
            assert False, "relative publication URL must be rejected"
        except ValueError as exc:
            assert "absolute publication url" in str(exc)


def test_external_verification_can_revoke_current_credit(tmp_path):
    publications = tmp_path / "publications.json"
    publications.write_text(json.dumps([{
        "url": "https://example.test/post/1",
        "platform": "guest",
        "verified": True,
    }]), encoding="utf-8")
    with patch.object(marketplace, "PUBLICATIONS_FILE", publications):
        row = marketplace.update_external_publication_verification(
            "https://example.test/post/1",
            verified=False,
            link_present=False,
            error="target_link_missing",
            checked_at="2026-09-11T21:00:00+00:00",
        )
    assert row["verified"] is False
    assert row["link_present"] is False
    assert row["verification_error"] == "target_link_missing"
