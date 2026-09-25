from pathlib import Path

import scripts.guardian_source_write_scan as guard_scan


def test_live_guardian_contours_have_zero_python_source_writers():
    rows = []
    for path in guard_scan.guardian_files():
        if path.suffix == ".py":
            rows.extend(guard_scan.scan_file(path))
    assert rows == []


def test_guardian_source_writer_is_detected(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    path = root / "scripts" / "fake_guardian.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from pathlib import Path\n"
        "SOURCE = Path('/root/BORIS/backend/app/api/example.py')\n"
        "SOURCE.write_text('broken')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(guard_scan, "ROOT", root)
    rows = guard_scan.scan_file(path)
    assert any(row.get("code") == "guardian_python_source_write" for row in rows)
