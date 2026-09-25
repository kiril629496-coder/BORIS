import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import openai_audio_guard as A


class OpenAIAudioGuardTests(unittest.TestCase):
    def test_command_routes_do_not_call_openai_audio_directly(self):
        import inspect
        from app.api import command
        source = inspect.getsource(command)
        self.assertNotIn('requests.post("https://api.openai.com/v1/audio', source)
        self.assertIn("_transcribe", source)
        self.assertIn("_LOCAL_STT_MODEL_NAME", source)
        self.assertNotIn("_guarded_transcribe", source)
        self.assertIn("_guarded_speech", source)

    def test_speech_matching_cache_is_zero_new_purchase(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OPENAI_API_KEY":"test", "BORIS_AUDIO_PAID_CACHE_DIR":td, "BORIS_ALLOW_EXTERNAL_RAW_AUDIO":"1"}):
            digest, _ = A._intent("acc", "голосовой ответ BORIS",
                                  {"kind":"speech","model":"tts-1","voice":"onyx","text":"Привет"})
            audio, meta, _ = A._paths(digest, ".mp3")
            audio.parent.mkdir(parents=True, exist_ok=True)
            audio.write_bytes(b"paid-audio")
            meta.write_text(json.dumps({"intent_hash":digest,"request_id":"req-old"}),encoding="utf-8")
            with patch.object(A, "_lock", side_effect=AssertionError("lock/provider path called")):
                out=A.speech(account_id="acc",text_value="Привет",voice="onyx",model="tts-1",
                             operation="голосовой ответ BORIS",cost_rub=1.0)
            self.assertTrue(out["cache_hit"])
            self.assertEqual(out["cost_rub"],0.0)
            self.assertEqual(out["audio"],b"paid-audio")

    def test_transcription_ambiguous_transport_is_quarantined_before_rebuy(self):
        class Dummy: pass
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OPENAI_API_KEY":"test", "BORIS_AUDIO_PAID_CACHE_DIR":td, "BORIS_ALLOW_EXTERNAL_RAW_AUDIO":"1"}):
            with patch.object(A,"_lock",return_value=(Dummy(),1)), \
                 patch.object(A,"_unlock",return_value=None), \
                 patch.object(A,"_reserve",return_value="run-1"), \
                 patch.object(A.ai_budget,"release",return_value={}), \
                 patch.object(A,"log_guard_event",return_value=True), \
                 patch("app.services.reliability.dependency_call",side_effect=ConnectionError("lost after send")) as provider:
                with self.assertRaisesRegex(RuntimeError,"AUDIO_PAID_OUTCOME_UNCERTAIN"):
                    A.transcribe(account_id="acc",audio=b"same-audio",filename="x.webm",content_type="audio/webm",
                                 cost_rub=1.0)
                with self.assertRaisesRegex(RuntimeError,"AUDIO_PAID_OUTCOME_UNCERTAIN"):
                    A.transcribe(account_id="acc",audio=b"same-audio",filename="x.webm",content_type="audio/webm",
                                 cost_rub=1.0)
            self.assertEqual(provider.call_count,1)
            self.assertEqual(len(list(Path(td).glob("*.uncertain.json"))),1)


if __name__ == "__main__":
    unittest.main()


    def test_external_raw_audio_is_blocked_by_default(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY":"test"}, clear=False):
            os.environ.pop("BORIS_ALLOW_EXTERNAL_RAW_AUDIO", None)
            with self.assertRaisesRegex(RuntimeError, "AUDIO_EXTERNAL_PII_BLOCKED_USE_LOCAL_STT"):
                A.transcribe(
                    account_id="acc", audio=b"voice", filename="x.webm",
                    content_type="audio/webm", cost_rub=1.0,
                )

class CalltrackingAudioGuardIntegrationTests(unittest.TestCase):
    def test_calltracking_transcription_is_local_only_and_fail_closed(self):
        import inspect
        from app.api import calltracking
        source = inspect.getsource(calltracking._transcribe)
        loader = inspect.getsource(calltracking._local_stt_model)
        self.assertIn("local faster-whisper only", source)
        self.assertIn("LocalSTTLowConfidence", source)
        self.assertIn("local_files_only=True", loader)
        self.assertNotIn("_guarded_transcribe", source)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("api.openai.com", source)
        self.assertEqual(calltracking._WHISPER_PER_MIN_USD, 0.0)
        self.assertEqual(calltracking._WHISPER_MIN_USD, 0.0)

    def test_calltracking_local_stt_accepts_only_high_confidence(self):
        from types import SimpleNamespace
        from app.api import calltracking

        class FakeModel:
            def transcribe(self, *args, **kwargs):
                words = [
                    SimpleNamespace(probability=0.93),
                    SimpleNamespace(probability=0.96),
                ]
                segment = SimpleNamespace(
                    text=" уверенная локальная расшифровка ",
                    avg_logprob=-0.2,
                    no_speech_prob=0.01,
                    words=words,
                )
                return iter([segment]), SimpleNamespace(language="ru")

        meta = {}
        with patch.object(calltracking, "_LOCAL_STT_MODEL", FakeModel()):
            text_value = calltracking._transcribe(
                b"fake-audio", "call.wav", "", account_id="qa-local-stt", provenance=meta
            )
        self.assertEqual(text_value, "уверенная локальная расшифровка")
        self.assertEqual(meta["stt_provider"], "local_faster_whisper")
        self.assertFalse(meta["paid_stt"])
        self.assertGreater(meta["stt_confidence"], 0.9)

    def test_calltracking_local_stt_low_confidence_never_becomes_success(self):
        from types import SimpleNamespace
        from app.api import calltracking

        class FakeModel:
            def transcribe(self, *args, **kwargs):
                return iter([]), SimpleNamespace(language="ru")

        with patch.object(calltracking, "_LOCAL_STT_MODEL", FakeModel()):
            with self.assertRaises(calltracking.LocalSTTLowConfidence):
                calltracking._transcribe(
                    b"fake-audio", "call.wav", "", account_id="qa-local-stt", provenance={}
                )

    def test_whisper_verbose_json_logs_provider_duration_before_cache(self):
        class DummyLock: pass
        class Resp:
            status_code = 200
            headers = {"x-request-id": "req-whisper-1"}
            def json(self):
                return {"text": "расшифровка", "duration": 42.5}
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"OPENAI_API_KEY":"test", "BORIS_AUDIO_PAID_CACHE_DIR":td, "BORIS_ALLOW_EXTERNAL_RAW_AUDIO":"1"}):
            with patch.object(A,"_lock",return_value=(DummyLock(),1)), \
                 patch.object(A,"_unlock",return_value=None), \
                 patch.object(A,"_reserve",return_value="run-1"), \
                 patch.object(A.ai_budget,"commit",return_value={"status":"committed"}) as commit, \
                 patch.object(A.ai_budget,"release",return_value={}), \
                 patch.object(A,"log_usage",return_value=0.42) as usage, \
                 patch("app.services.reliability.dependency_call",return_value=Resp()) as provider:
                out=A.transcribe(account_id="acc",audio=b"call-audio",filename="call.mp3",content_type="audio/mpeg",
                                 model="whisper-1",operation="расшифровка звонка",cost_rub=None,
                                 prompt="бытовки",response_format="verbose_json",module="calltracking_transcription")
            self.assertEqual(out["text"],"расшифровка")
            self.assertEqual(out["duration"],42.5)
            self.assertFalse(out["cache_hit"])
            self.assertEqual(provider.call_count,1)
            self.assertEqual(usage.call_count,1)
            self.assertEqual(usage.call_args.kwargs.get("audio_seconds"),42.5)
            commit.assert_called_once_with("run-1","req-whisper-1")



class OpenAIAudioInputGuardTests(unittest.TestCase):
    def test_transcription_empty_audio_stops_before_provider(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test","BORIS_ALLOW_EXTERNAL_RAW_AUDIO":"1"}), \
             patch.object(A,"log_guard_event",return_value=True), \
             patch("app.services.reliability.dependency_call",side_effect=AssertionError("provider called")) as provider:
            with self.assertRaisesRegex(RuntimeError,"AUDIO_INPUT_EMPTY"):
                A.transcribe(account_id="acc",audio=b"",filename="x.webm",content_type="audio/webm",cost_rub=1.0)
        self.assertEqual(provider.call_count,0)

    def test_transcription_oversize_stops_before_provider(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test","BORIS_AUDIO_MAX_UPLOAD_BYTES":"4","BORIS_ALLOW_EXTERNAL_RAW_AUDIO":"1"}), \
             patch.object(A,"log_guard_event",return_value=True), \
             patch("app.services.reliability.dependency_call",side_effect=AssertionError("provider called")) as provider:
            with self.assertRaisesRegex(RuntimeError,"AUDIO_INPUT_TOO_LARGE"):
                A.transcribe(account_id="acc",audio=b"12345",filename="x.webm",content_type="audio/webm",cost_rub=1.0)
        self.assertEqual(provider.call_count,0)

    def test_tts_oversize_stops_before_provider(self):
        with patch.dict(os.environ,{"OPENAI_API_KEY":"test","BORIS_AUDIO_TTS_MAX_CHARS":"4"}), \
             patch.object(A,"log_guard_event",return_value=True), \
             patch("app.services.reliability.dependency_call",side_effect=AssertionError("provider called")) as provider:
            with self.assertRaisesRegex(RuntimeError,"AUDIO_TTS_TEXT_TOO_LARGE"):
                A.speech(account_id="acc",text_value="12345",voice="onyx",cost_rub=1.0)
        self.assertEqual(provider.call_count,0)
