import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path("/root/BORIS/backend/tools/service_marketplace_assistant.py")
spec = importlib.util.spec_from_file_location("svc_market", MODULE_PATH)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

def test_zero_llm_and_site_link(tmp_path):
    m.DRAFTS_FILE = tmp_path / "drafts.json"
    m.PUBLICATIONS_FILE = tmp_path / "publications.json"
    drafts = m.generate_drafts("fl", "avito", 3)
    assert len(drafts) == 3
    assert all(d["generated_without_openai"] is True for d in drafts)
    assert all(m.BORIS_SITE in d["text"] for d in drafts)
    assert all(d["status"] == "needs_owner_approval" for d in drafts)

def test_approval_gate_blocks_publication(tmp_path):
    m.DRAFTS_FILE = tmp_path / "drafts.json"
    m.PUBLICATIONS_FILE = tmp_path / "publications.json"
    drafts = m.generate_drafts("fl", "sales", 1)
    m.persist_drafts(drafts)
    try:
        m.mark_posted(drafts[0]["id"], "https://example.com/post/1")
        assert False, "must block non-approved draft"
    except SystemExit as exc:
        assert "owner approval required" in str(exc)

def test_approved_publication_stores_url(tmp_path):
    m.DRAFTS_FILE = tmp_path / "drafts.json"
    m.PUBLICATIONS_FILE = tmp_path / "publications.json"
    drafts = m.generate_drafts("workspace", "integrations", 1)
    m.persist_drafts(drafts)
    did = drafts[0]["id"]
    m.approve_draft(did)
    item = m.mark_posted(did, "https://example.com/post/42")
    assert item["url"] == "https://example.com/post/42"
    pubs = json.loads(m.PUBLICATIONS_FILE.read_text(encoding="utf-8"))
    assert pubs[-1]["draft_id"] == did
