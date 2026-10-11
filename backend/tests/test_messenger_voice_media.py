import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parents[1] / "app/services/messenger_voice_media.py"
spec = importlib.util.spec_from_file_location("voice_media_test_target", SOURCE)
media = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = media
spec.loader.exec_module(media)


class VoiceMediaTest(unittest.TestCase):
    def test_ranges(self):
        for raw, expected in [(None,(0,99,False)),("bytes=0-9",(0,9,True)),("bytes=90-",(90,99,True)),("bytes=-10",(90,99,True)),("bytes=0-999",(0,99,True))]:
            self.assertEqual(media.parse_range(raw,100),expected)

    def test_range_abuse(self):
        for raw in ("bytes=0-1,5-6","bytes=100-","bytes=-0","bytes=-","bytes=20-10","bytes=1e3-","bytes="+"9"*100+"-"):
            with self.assertRaises(media.VoiceError):media.parse_range(raw,100)

    def test_executable_and_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for payload in (b"MZ"+b"0"*100,b"garbage",b"\x1a\x45\xdf\xa3"+b"broken"):
                path=Path(tmp)/"voice.webm";path.write_bytes(payload)
                with self.assertRaises(media.VoiceError):media.probe(path)

    def test_empty_size_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"empty";path.touch()
            with self.assertRaises(media.VoiceError):media.probe(path)
            with path.open("wb") as stream:stream.truncate(media.MAX_BYTES+1)
            with self.assertRaises(media.VoiceError):media.probe(path)

    def test_reject_video_and_unbounded_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"audio";path.write_bytes(b"fixture")
            for data in ({"streams":[{"codec_type":"video"}],"format":{}},
                         {"streams":[{"codec_type":"audio","codec_name":"aac"}],"format":{"format_name":"mp4","duration":"601"}},
                         {"streams":[{"codec_type":"audio","codec_name":"aac"}],"format":{"format_name":"mp4","duration":"nan"}}):
                with patch.object(media,"run_media",return_value=json.dumps(data).encode()):
                    with self.assertRaises(media.VoiceError):media.probe(path)

    def test_real_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"original";playback=Path(tmp)/"playback"
            media.run_media(["ffmpeg","-v","error","-f","lavfi","-i","sine=frequency=440:duration=1","-c:a","libopus","-f","webm",str(source)])
            result=media.normalize(source,playback)
            self.assertAlmostEqual(result["duration_ms"],1000,delta=25)
            self.assertEqual(result["codec"],"aac")
            self.assertEqual(len(result["waveform_peaks"]),64)
            self.assertEqual(media.probe(playback)["codec"],"aac")
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()),["original","playback"])

    def test_cleanup_normalization_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            original=Path(tmp)/"original";original.write_bytes(b"test")
            with patch.object(media,"probe",return_value={"codec":"opus"}),patch.object(media,"run_media",side_effect=media.VoiceError("media_timeout")):
                with self.assertRaises(media.VoiceError):media.normalize(original,Path(tmp)/"playback")
            self.assertEqual([p.name for p in Path(tmp).iterdir()],["original"])

    def test_low_confidence_auto_detection_uses_russian_fallback(self):
        engine=object.__new__(media.FasterWhisperEngine)
        auto={"text":"Kalebi dobra","language":"pl","language_probability":0.2,"duration_ms":15000,
              "segments":[{"start":0,"end":1,"text":"Kalebi dobra"}],"avg_logprob":-0.70}
        ru={"text":"Кирилл добрый день","language":"ru","language_probability":1.0,"duration_ms":15000,
            "segments":[{"start":0,"end":1,"text":"Кирилл добрый день"}],"avg_logprob":-0.66}
        with tempfile.TemporaryDirectory() as tmp,patch.object(media,"run_media",return_value=b""),patch.dict(media.os.environ,{"MESSENGER_STT_PREFERRED_LANGUAGE":"ru"}),patch.object(engine,"_decode",side_effect=[auto,ru]) as decode:
            result=engine.transcribe(Path(tmp)/"voice.m4a")
        self.assertEqual(result.text,"Кирилл добрый день")
        self.assertEqual(result.language,"ru")
        self.assertEqual(decode.call_count,2)

    def test_empty_decode_stays_no_speech_candidate(self):
        engine=object.__new__(media.FasterWhisperEngine)
        empty={"text":"","language":None,"language_probability":0.0,"duration_ms":15000,
               "segments":[],"avg_logprob":-99.0}
        with tempfile.TemporaryDirectory() as tmp,patch.object(media,"run_media",return_value=b""),patch.dict(media.os.environ,{"MESSENGER_STT_PREFERRED_LANGUAGE":"ru"}),patch.object(engine,"_decode",side_effect=[empty,empty]):
            result=engine.transcribe(Path(tmp)/"voice.m4a")
        self.assertEqual(result.text,"")
        self.assertIsNone(result.language)
        self.assertEqual(result.segments,[])


if __name__=="__main__":unittest.main()
