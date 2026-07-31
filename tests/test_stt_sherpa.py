"""stage3_stt_sherpa (sherpa-onnx 경량 엔진) 단위 테스트.

실제 모델 다운로드·ffmpeg 실행 없이 카탈로그 조회/텍스트 정리/디코딩 루프만 검증한다.
(실제 인식 품질은 모듈 docstring 의 실측 기록 참고.)
"""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from yke.pipeline import stage3_stt_sherpa as sherpa_stt


class TestResolveModelName(unittest.TestCase):
    def test_known_language_small(self):
        name = sherpa_stt._resolve_model_name("ko", "small", log=lambda m: None)
        self.assertEqual(name, "sherpa-onnx-moonshine-tiny-ko-quantized-2026-02-27")

    def test_language_with_region_suffix_is_normalized(self):
        # "ko-KR" 처럼 지역 서픽스가 붙어도 기본 언어 코드로 매칭한다.
        name = sherpa_stt._resolve_model_name("en-US", "small", log=lambda m: None)
        self.assertEqual(name, "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27")

    def test_large_variant_when_available(self):
        name = sherpa_stt._resolve_model_name("en", "large", log=lambda m: None)
        self.assertEqual(name, "sherpa-onnx-moonshine-base-en-quantized-2026-02-27")

    def test_missing_size_falls_back_to_small_with_log(self):
        logs: list[str] = []
        # 한국어는 large 모델을 두지 않는다 -> small 로 대체하고 로그를 남긴다.
        name = sherpa_stt._resolve_model_name("ko", "large", log=logs.append)
        self.assertEqual(name, "sherpa-onnx-moonshine-tiny-ko-quantized-2026-02-27")
        self.assertTrue(any("small" in m for m in logs))

    def test_unsupported_language_raises_with_helpful_message(self):
        with self.assertRaises(ValueError) as ctx:
            sherpa_stt._resolve_model_name("xx", "small", log=lambda m: None)
        msg = str(ctx.exception)
        self.assertIn("xx", msg)
        self.assertIn("ko", msg)  # 지원 언어 목록이 메시지에 포함
        self.assertIn("faster-whisper", msg)  # 대안 안내


class TestCleanText(unittest.TestCase):
    def test_strips_sentencepiece_word_marker(self):
        # 모델이 단어 경계 기호(▁)를 붙여 내보내는 경우가 있다 — 화면/문서에 남으면 안 된다.
        self.assertEqual(sherpa_stt._clean_text("▁이 프로그램은 좋다"), "이 프로그램은 좋다")

    def test_collapses_whitespace(self):
        self.assertEqual(sherpa_stt._clean_text("  안녕\n하세요  "), "안녕 하세요")

    def test_empty_stays_empty(self):
        self.assertEqual(sherpa_stt._clean_text("  ▁ "), "")


class TestModelFiles(unittest.TestCase):
    def test_missing_files_report_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "some-model"
            model_dir.mkdir()
            (model_dir / "encoder_model.ort").write_bytes(b"x")
            with self.assertRaises(RuntimeError) as ctx:
                sherpa_stt._model_files(model_dir)
        msg = str(ctx.exception)
        self.assertIn("decoder_model_merged.ort", msg)
        self.assertIn("tokens.txt", msg)

    def test_returns_paths_when_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            model_dir = Path(tmp) / "some-model"
            model_dir.mkdir()
            for name in ("encoder_model.ort", "decoder_model_merged.ort", "tokens.txt"):
                (model_dir / name).write_bytes(b"x")
            encoder, decoder, tokens = sherpa_stt._model_files(model_dir)
        self.assertTrue(encoder.endswith("encoder_model.ort"))
        self.assertTrue(decoder.endswith("decoder_model_merged.ort"))
        self.assertTrue(tokens.endswith("tokens.txt"))


class TestConvertToWav(unittest.TestCase):
    def test_builds_16k_mono_command(self):
        completed = SimpleNamespace(returncode=0, stderr="")
        with (
            mock.patch.object(sherpa_stt, "subprocess") as sp,
            mock.patch.dict(
                "sys.modules",
                {"imageio_ffmpeg": SimpleNamespace(get_ffmpeg_exe=lambda: "/bin/ffmpeg")},
            ),
        ):
            sp.run.return_value = completed
            sherpa_stt._convert_to_wav(Path("in.webm"), Path("out.wav"))
            cmd = sp.run.call_args[0][0]
        self.assertIn("-ac", cmd)
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")  # mono
        self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")  # 16kHz

    def test_failure_raises_with_stderr_tail(self):
        completed = SimpleNamespace(returncode=1, stderr="boom: bad codec")
        with (
            mock.patch.object(sherpa_stt, "subprocess") as sp,
            mock.patch.dict(
                "sys.modules",
                {"imageio_ffmpeg": SimpleNamespace(get_ffmpeg_exe=lambda: "/bin/ffmpeg")},
            ),
        ):
            sp.run.return_value = completed
            with self.assertRaises(RuntimeError) as ctx:
                sherpa_stt._convert_to_wav(Path("in.webm"), Path("out.wav"))
        self.assertIn("bad codec", str(ctx.exception))


# --- 디코딩 루프(VAD → 인식) 검증용 가짜 sherpa-onnx 객체 ---------------------


class _FakeSpeech:
    def __init__(self, start: int, n_samples: int):
        self.start = start
        self.samples = [0.0] * n_samples


class _FakeVad:
    """window 크기 단위 입력을 받아, 정해진 시점마다 발화 구간을 하나씩 뱉는 VAD 흉내."""

    def __init__(self, emit_at: dict[int, _FakeSpeech] | None = None):
        self.config = SimpleNamespace(silero_vad=SimpleNamespace(window_size=512))
        self.accepted = 0
        self.flushed = False
        self._emit_at = emit_at or {}
        self._queue: list[_FakeSpeech] = []

    def accept_waveform(self, samples):
        assert len(samples) == 512, "VAD 는 window 크기 단위로 먹여야 한다"
        self.accepted += 1
        if self.accepted in self._emit_at:
            self._queue.append(self._emit_at[self.accepted])

    def flush(self):
        self.flushed = True

    def empty(self):
        return not self._queue

    @property
    def front(self):
        return self._queue[0]

    def pop(self):
        self._queue.pop(0)


class _FakeRecognizer:
    def __init__(self, texts: list[str]):
        self._texts = list(texts)
        self.decoded = 0

    def create_stream(self):
        return SimpleNamespace(
            accept_waveform=lambda rate, samples: None,
            result=SimpleNamespace(text=self._texts[self.decoded] if self._texts else ""),
        )

    def decode_stream(self, stream):
        self.decoded += 1


def _write_silent_wav(path: Path, n_frames: int) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * n_frames)


class TestDecode(unittest.TestCase):
    def test_builds_segments_with_times_and_reports_progress(self):
        # 16000 샘플(1초)마다 발화 구간 하나씩 — 시작 시각·길이가 그대로 세그먼트가 된다.
        vad = _FakeVad(
            emit_at={
                10: _FakeSpeech(start=16000, n_samples=16000),  # 1.0초 ~ 2.0초
                40: _FakeSpeech(start=48000, n_samples=8000),  # 3.0초 ~ 3.5초
            }
        )
        rec = _FakeRecognizer(["▁첫 번째 발화", "두 번째 발화"])
        progress: list[tuple[float, float]] = []

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            _write_silent_wav(wav, 16000 * 5)  # 5초
            segs = sherpa_stt._decode(
                wav, rec, vad, lambda done, total: progress.append((done, total)), None
            )

        self.assertEqual([s.text for s in segs], ["첫 번째 발화", "두 번째 발화"])
        self.assertAlmostEqual(segs[0].start, 1.0)
        self.assertAlmostEqual(segs[0].end, 2.0)
        self.assertAlmostEqual(segs[1].start, 3.0)
        self.assertAlmostEqual(segs[1].end, 3.5)
        self.assertTrue(vad.flushed)  # 마지막 구간까지 밀어냈다
        self.assertEqual(progress[-1], (5.0, 5.0))  # 진행률은 100% 로 끝난다

    def test_empty_text_is_dropped(self):
        vad = _FakeVad(emit_at={5: _FakeSpeech(start=0, n_samples=1600)})
        rec = _FakeRecognizer(["   "])
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            _write_silent_wav(wav, 16000)
            segs = sherpa_stt._decode(wav, rec, vad, None, None)
        self.assertEqual(segs, [])

    def test_should_stop_raises_without_reading_whole_file(self):
        # 첫 청크는 처리하고, 두 번째 확인에서 중단 — 파일 전체를 읽지 않고 멈춰야 한다.
        vad = _FakeVad()
        rec = _FakeRecognizer([])
        should_stop = mock.Mock(side_effect=[False, True])

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            _write_silent_wav(wav, int(16000 * sherpa_stt._READ_CHUNK_SECONDS * 3))
            with self.assertRaises(sherpa_stt.StoppedError):
                sherpa_stt._decode(wav, rec, vad, None, should_stop)

        # 한 청크(2초 = 16000*2 샘플)만 VAD 에 들어갔다: 32000/512 = 62.5 → 62 window.
        self.assertEqual(vad.accepted, 62)
        self.assertFalse(vad.flushed)


class TestImportGuidance(unittest.TestCase):
    def test_missing_package_explains_how_to_install(self):
        # optional extra 라 미설치일 수 있다 — 네이티브 라이브러리 경로 나열 대신 안내를 준다.
        with mock.patch.dict("sys.modules", {"sherpa_onnx": None}):
            with self.assertRaises(RuntimeError) as ctx:
                sherpa_stt._import_sherpa()
        msg = str(ctx.exception)
        self.assertIn("--extra sherpa", msg)
        self.assertIn("faster-whisper", msg)


class TestEngineDispatch(unittest.TestCase):
    """stage3_stt 가 engine 값에 따라 경량 엔진으로 위임하는지(예전 이름 vosk 포함)."""

    def _cfg(self, engine: str):
        return SimpleNamespace(engine=engine, sherpa_model_size="small", cpu_threads=0)

    def test_delegates_for_sherpa_and_legacy_vosk(self):
        from yke.pipeline import stage3_stt

        for engine in ("sherpa", "sherpa-onnx", "vosk"):
            with mock.patch.object(
                stage3_stt_module(), "transcribe", return_value=["ok"]
            ) as delegated:
                out = stage3_stt.transcribe(Path("a.m4a"), "ko", self._cfg(engine))
            self.assertEqual(out, ["ok"], engine)
            self.assertTrue(delegated.called, engine)

    def test_faster_whisper_does_not_delegate(self):
        from yke.pipeline import stage3_stt

        with (
            mock.patch.object(stage3_stt_module(), "transcribe") as delegated,
            mock.patch.object(stage3_stt, "_get_model", side_effect=RuntimeError("stop here")),
        ):
            with self.assertRaises(RuntimeError):
                stage3_stt.transcribe(
                    Path("a.m4a"),
                    "ko",
                    SimpleNamespace(
                        engine="faster-whisper",
                        model="small",
                        device="cpu",
                        compute_type="int8",
                        word_timestamps=False,
                        batched=False,
                        batch_size=4,
                        cpu_threads=0,
                    ),
                )
        self.assertFalse(delegated.called)


def stage3_stt_module():
    """위임 대상 모듈(지연 import 되므로 테스트에서 매번 새로 가져온다)."""
    from yke.pipeline import stage3_stt_sherpa

    return stage3_stt_sherpa


if __name__ == "__main__":
    unittest.main()
