from pathlib import Path

def test_release_builder_enforces_architecture_gate_before_and_after_snapshot():
 t=Path("scripts/build_backend_release.py").read_text()
 assert "architecture_release_gate.py" in t
 assert t.count("_assert_architecture_release_gate()")>=3
 assert "--verify" in t

def test_gate_receipt_is_bound_to_exact_source_fingerprint():
 t=Path("scripts/architecture_release_gate.py").read_text()
 assert "architecture-release-test-receipt.json" in t
 assert "rec.get('source_fingerprint')==fp" in t
 assert "source_changed_during_tests" in t
 assert "full_backend_tests" in t and "mandatory_dependency_tests" in t
