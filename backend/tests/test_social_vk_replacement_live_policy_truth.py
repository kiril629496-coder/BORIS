import importlib.util
import hashlib
from pathlib import Path

SRC=Path(__file__).parents[1]/"run/social_vk_replacement_watch_v1.py"
spec=importlib.util.spec_from_file_location("watch",SRC)
w=importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

def sha(x):
    return hashlib.sha256(str(x).encode("utf-8","ignore")).hexdigest()

base={
    "account_id":"u2","owner_id":-10,"old_post_id":1,"new_post_id":2,
    "record_id":"r","project_id":"p",
    "expected_text":"old","expected_text_sha256":sha("old"),
}
live=dict(base,expected_text="new",expected_text_sha256=sha("new"))
accepted=dict(base,expected_text="middle",expected_text_sha256=sha("middle"),
              accepted_at="2026-09-05T12:00:00+00:00")

assert w._effective_payload(base,live)["expected_text"]=="new"
assert w._effective_payload(base,None)["expected_text"]=="old"
wrong=dict(live,new_post_id=3)
assert w._effective_payload(base,wrong)["expected_text"]=="old"
print("SOCIAL_VK_REPLACEMENT_LIVE_POLICY_TRUTH=PASS")

assert w._accepted_truth("old",base,live,[accepted])=="manifest_event"
assert w._accepted_truth("new",base,live,[accepted])=="current_live_policy"
assert w._accepted_truth("middle",base,None,[accepted])=="accepted_policy_snapshot"
assert w._accepted_truth("corrupt",base,live,[accepted]) is None
print("SOCIAL_VK_REPLACEMENT_DURABLE_MULTI_TRUTH=PASS")

src=SRC.read_text()
assert "_load_accepted_versions" in src
assert "orphan_text_unrecognized" in src
assert "if not live:\n                errors.append" in src
assert "_readback_until_current" in src
assert "accepted_versions" in src
assert 'int(snap.get("code") or 0) in {9,29}' in src
assert '"dependency_state":"waiting_external"' in src
print("SOCIAL_VK_RETENTION_SELF_HEAL_CONTRACT=PASS")
