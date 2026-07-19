"""stage3_stt_vosk (Vosk 경량 엔진, non-AI 옵션) 단위 테스트.

실제 모델 다운로드·ffmpeg 실행 없이 카탈로그 조회/세그먼트 파싱 로직만 검증한다.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from yke.pipeline import stage3_stt_vosk as vosk_stt


class TestResolveModelName(unittest.TestCase):
    def test_known_language_small(self):
        name = vosk_stt._resolve_model_name("ko", "small", log=lambda m: None)
        self.assertEqual(name, "vosk-model-small-ko-0.22")

    def test_language_with_region_suffix_is_normalized(self):
        # "ko-KR" 처럼 지역 서픽스가 붙어도 기본 언어 코드로 매칭한다.
        name = vosk_stt._resolve_model_name("en-US", "small", log=lambda m: None)
        self.assertEqual(name, "vosk-model-small-en-us-0.15")

    def test_large_variant_when_available(self):
        name = vosk_stt._resolve_model_name("en", "large", log=lambda m: None)
        self.assertEqual(name, "vosk-model-en-us-0.22")

    def test_missing_size_falls_back_to_small_with_log(self):
        logs: list[str] = []
        # 한국어는 large 모델이 없다 -> small 로 대체하고 로그를 남긴다.
        name = vosk_stt._resolve_model_name("ko", "large", log=logs.append)
        self.assertEqual(name, "vosk-model-small-ko-0.22")
        self.assertTrue(any("small" in m for m in logs))

    def test_unsupported_language_raises_with_helpful_message(self):
        with self.assertRaises(ValueError) as ctx:
            vosk_stt._resolve_model_name("xx", "small", log=lambda m: None)
        self.assertIn("xx", str(ctx.exception))
        self.assertIn("ko", str(ctx.exception))  # 지원 언어 목록이 메시지에 포함


class TestSegmentFromResult(unittest.TestCase):
    def test_uses_word_timestamps_when_present(self):
        result = json.dumps(
            {
                "text": "hello world",
                "result": [
                    {"word": "hello", "start": 0.1, "end": 0.5, "conf": 1.0},
                    {"word": "world", "start": 0.6, "end": 1.2, "conf": 1.0},
                ],
            }
        )
        seg = vosk_stt._segment_from_result(result, fallback_start=0.0, fallback_end=99.0)
        self.assertEqual(seg.start, 0.1)
        self.assertEqual(seg.end, 1.2)
        self.assertEqual(seg.text, "hello world")

    def test_empty_text_returns_none(self):
        result = json.dumps({"text": ""})
        self.assertIsNone(vosk_stt._segment_from_result(result, fallback_start=0.0, fallback_end=1.0))

    def test_missing_word_list_uses_fallback_times(self):
        result = json.dumps({"text": "음"})
        seg = vosk_stt._segment_from_result(result, fallback_start=2.0, fallback_end=3.5)
        self.assertEqual(seg.start, 2.0)
        self.assertEqual(seg.end, 3.5)


class TestConvertToWav(unittest.TestCase):
    def test_builds_16k_mono_ffmpeg_command(self):
        completed = mock.Mock(returncode=0, stderr="")
        with (
            mock.patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg.exe"),
            mock.patch.object(vosk_stt.subprocess, "run", return_value=completed) as run,
        ):
            vosk_stt._convert_to_wav(Path("in.webm"), Path("out.wav"))
        cmd = run.call_args.args[0]
        self.assertIn("-ar", cmd)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")
        self.assertIn("-ac", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")

    def test_nonzero_returncode_raises(self):
        completed = mock.Mock(returncode=1, stderr="boom")
        with (
            mock.patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg.exe"),
            mock.patch.object(vosk_stt.subprocess, "run", return_value=completed),
        ):
            with self.assertRaises(RuntimeError):
                vosk_stt._convert_to_wav(Path("in.webm"), Path("out.wav"))


class TestDecodeShouldStop(unittest.TestCase):
    """중단 요청이 오디오 전체를 다 읽을 때까지 기다리지 않고 청크 경계에서 즉시 먹히는지."""

    def _write_silent_wav(self, path: Path, n_frames: int) -> None:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * n_frames)

    def test_raises_without_reading_whole_file(self):
        instances: list["_FakeRecognizer"] = []

        class _FakeRecognizer:
            def __init__(self, *_a, **_kw):
                self.accept_calls = 0
                instances.append(self)

            def SetWords(self, _v):
                pass

            def AcceptWaveform(self, _data):
                self.accept_calls += 1
                return False

            def Result(self):
                return json.dumps({"text": ""})

            def FinalResult(self):
                return json.dumps({"text": ""})

        fake_vosk = SimpleNamespace(KaldiRecognizer=_FakeRecognizer)
        # 첫 청크 확인 때는 False(처리), 두 번째 확인 때 True(중단) — 청크 3개 분량을
        # 준비해 실제로 파일 전체를 다 읽지 않고 멈추는지 검증한다.
        should_stop = mock.Mock(side_effect=[False, True])

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "a.wav"
            self._write_silent_wav(wav_path, vosk_stt._DECODE_CHUNK_FRAMES * 3)
            with mock.patch.dict("sys.modules", {"vosk": fake_vosk}):
                with self.assertRaises(vosk_stt.StoppedError):
                    vosk_stt._decode(wav_path, model=object(), on_progress=None, should_stop=should_stop)

        self.assertEqual(instances[0].accept_calls, 1)  # 첫 청크만 처리하고 멈췄다


if __name__ == "__main__":
    unittest.main()
