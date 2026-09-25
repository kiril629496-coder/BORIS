import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.video_factory import voiceover as V
from app.video_factory import visual_qa as Q


def _data(project_id="p1", account_id="acc-video"):
    return {
        "id": project_id,
        "account_id": account_id,
        "plan": {"mode": "boris_tutorial", "duration": 20,
                 "scenes": [{"index": 1, "narration": "Короткая тестовая озвучка"}]},
    }


def _intent(data):
    text = V.narration_text(data)
    instructions = (
        "Говори по-русски уверенно, естественно и тепло, как профессиональный ведущий "
        "деловой презентации. Не спеши. Делай короткие смысловые паузы. "
        "Не используй рекламную театральность."
    )
    basis = json.dumps({
        "policy": "video_tts_shared_audio_guard_v2",
        "account_id": str(data.get("account_id") or ""),
        "project_id": str(data.get("id") or ""),
        "model": V.MODEL,
        "voice": V.VOICE,
        "text": text,
        "instructions": instructions,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class VideoPaidSafetyTests(unittest.TestCase):
    def test_visual_qa_routes_through_shared_exactly_once_vision_guard(self):
        import inspect
        source = inspect.getsource(Q.audit)
        self.assertIn("_vision_chat_json", source)
        self.assertNotIn("requests.post", source)
        self.assertIn("account_required_for_paid_vision", source)

    def test_tts_requires_account_before_provider(self):
        data = _data(account_id="")
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with self.assertRaisesRegex(RuntimeError, "voiceover_account_required"):
                V.generate_boris_voice(data, td, td)

    def test_tts_durable_cache_prevents_second_purchase(self):
        data = _data()
        intent = _intent(data)
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            outdir = Path(td) / data["id"]
            outdir.mkdir(parents=True)
            (outdir / "boris_voice.mp3").write_bytes(b"already-paid-audio")
            (outdir / "boris_voice.paid-meta.json").write_text(
                json.dumps({"intent_hash": intent, "speed_factor": 1.0}), encoding="utf-8")
            with patch.object(V, "_audio_duration", return_value=12.0), \
                 patch("app.services.reliability.dependency_call", side_effect=AssertionError("provider called")):
                path, cost = V.generate_boris_voice(data, td, td)
            self.assertTrue(path.endswith("boris_voice.mp3"))
            self.assertEqual(cost, 0.0)
            self.assertTrue(data["voiceover_meta"]["cache_hit"])

    def test_tts_ambiguous_transport_is_quarantined_and_not_rebought(self):
        data = _data()
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "BORIS_AUDIO_PAID_CACHE_DIR": str(Path(td)/"guard")}):
            with patch("app.services.ai_budget.reserve", return_value={"status": "observe"}), \
                 patch("app.services.ai_budget.allow_next", return_value={"allowed": True}), \
                 patch("app.services.ai_budget.release", return_value={"status": "released"}), \
                 patch("app.services.ai_guard_audit.log_guard_event", return_value=True), \
                 patch("app.services.reliability.dependency_call", side_effect=ConnectionError("lost after send")) as provider:
                with self.assertRaisesRegex(RuntimeError, "AUDIO_PAID_OUTCOME_UNCERTAIN"):
                    V.generate_boris_voice(data, td, td)
                with self.assertRaisesRegex(RuntimeError, "AUDIO_PAID_OUTCOME_UNCERTAIN"):
                    V.generate_boris_voice(data, td, td)
            self.assertEqual(provider.call_count, 1)
            markers=list((Path(td)/"guard").glob("*.uncertain.json"))
            self.assertEqual(len(markers),1)
            saved = json.loads(markers[0].read_text(encoding="utf-8"))
            self.assertEqual(saved.get("error_code"), "audio_transport_outcome_unknown")

    def test_tts_paid_success_is_recoverable_after_local_postprocessing_failure(self):
        data = _data(project_id="p2")
        data["plan"]["mode"] = "commercial"
        response = type("Resp", (), {"status_code": 200, "content": b"paid-audio",
                                      "headers": {"x-request-id": "req-tts-1"}})()
        with tempfile.TemporaryDirectory() as td:
            audio_cache = str(Path(td) / "audio-cache")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "BORIS_AUDIO_PAID_CACHE_DIR": audio_cache}), \
                 patch("app.services.openai_audio_guard._lock", return_value=(unittest.mock.MagicMock(), 1)), \
                 patch("app.services.openai_audio_guard._unlock", return_value=None), \
                 patch("app.services.ai_budget.reserve", return_value={"status": "observe"}), \
                 patch("app.services.ai_budget.allow_next", return_value={"allowed": True}), \
                 patch("app.services.ai_budget.commit", return_value={"status": "committed"}) as commit, \
                 patch("app.services.ai_budget.release", return_value={"status": "released"}), \
                 patch("app.services.ai_guard_audit.log_guard_event", return_value=True), \
                 patch("app.services.reliability.dependency_call", return_value=response) as provider, \
                 patch("app.services.openai_audio_guard.log_usage", return_value=0.01) as usage, \
                 patch.object(V, "_fit_audio", side_effect=[RuntimeError("local ffmpeg failed"), (12.0, 1.0)]):
                with self.assertRaisesRegex(RuntimeError, "local ffmpeg failed"):
                    V.generate_boris_voice(data, td, td)
                path, retry_cost = V.generate_boris_voice(data, td, td)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(usage.call_count, 1)
            commit.assert_called_once_with(unittest.mock.ANY, "req-tts-1")
            self.assertEqual(retry_cost, 0.0)
            self.assertTrue(Path(path).is_file())
            self.assertTrue(data["voiceover_meta"]["cache_hit"])


if __name__ == "__main__":
    unittest.main()
