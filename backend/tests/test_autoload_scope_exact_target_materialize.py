from pathlib import Path


def _snapshot_block() -> str:
    src = Path("app/api/avito.py").read_text(encoding="utf-8")
    start = src.index("def _autoload_upload_snapshot")
    end = src.index("\ndef ", start + len("def _autoload_upload_snapshot"))
    return src[start:end]


def test_scoped_autoload_materializes_only_exact_authoritative_feed_target():
    block = _snapshot_block()
    assert "AUTOLOAD_SCOPE_EXACT_TARGET_MATERIALIZE_V1" in block
    assert "materialize_exact_feed_target as _materialize_scope_target" in block
    assert '_IdentityStorage.key=="feed_items"' in block
    assert "_feed_by_id_scope.setdefault(_feed_fid_scope,[]).append(_feed_item_scope)" in block
    assert "if len(_feed_candidates_scope)==1:" in block
    assert '_local_target in {"materialized","exists"}' in block
    assert block.count("_br=_bind_autoload(") >= 2
    assert "resolve_or_import_live" not in block


def test_scoped_autoload_keeps_provider_rejection_truth_when_materializing():
    block = _snapshot_block()
    assert '_bind_section="error_rejected" if _fid in _failed_scope_fids else _raw_section' in block
    assert '_bind_status="rejected" if _fid in _failed_scope_fids else _raw_status' in block
    second_bind = block.index("_br=_bind_autoload(", block.index("_br=_bind_autoload(") + 1)
    tail = block[second_bind:second_bind + 700]
    assert "report_section=_bind_section" in tail
    assert "avito_status=_bind_status" in tail
