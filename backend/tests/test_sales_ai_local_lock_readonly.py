import os

from app.services.sales_ai_router import _local_inference_guard


def test_local_mop_lock_works_when_existing_lock_is_read_only(tmp_path, monkeypatch):
    lock = tmp_path / "sales_ai.lock"
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o444)
    monkeypatch.setenv("BORIS_SALES_LOCAL_LOCK_PATH", str(lock))

    with _local_inference_guard("mop_test_readonly_lock"):
        assert lock.exists()
