from pathlib import Path

from app.services import messenger_voice_worker as worker
from app.services import messenger_voice_stt_child as child
from app.services.messenger_voice_media import MODEL_VERSION


def test_voice_worker_uses_release_local_stt_child_module():
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert '"-m", "app.services.messenger_voice_stt_child"' in source
    assert 'Path(__file__).with_name("messenger_voice_stt_child.py")' not in source


def test_voice_worker_retry_and_lease_are_bounded():
    assert worker.MAX_ATTEMPTS == len(worker.RETRY_DELAYS) + 1
    assert worker.LEASE_SECONDS > worker.JOB_TIMEOUT
    assert worker.JOB_TIMEOUT <= 900


def test_voice_stt_child_is_pinned_to_same_local_model():
    assert child.MODEL_VERSION == MODEL_VERSION
