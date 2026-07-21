"""config 로딩/기본값 특성화 테스트."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yke.config import load_config


class TestLoadConfig(unittest.TestCase):
    def _write(self, text: str) -> Path:
        p = Path(tempfile.mkdtemp()) / "c.yaml"
        p.write_text(text, encoding="utf-8")
        return p

    def test_defaults_applied(self):
        cfg = load_config(self._write('videos: ["https://youtu.be/x"]\n'))
        self.assertEqual(cfg.videos, ["https://youtu.be/x"])
        self.assertEqual(cfg.language, "ko")
        self.assertEqual(cfg.stt.engine, "faster-whisper")  # AI 엔진이 기본
        self.assertEqual(cfg.stt.model, "auto")
        self.assertEqual(cfg.stt.device, "auto")
        self.assertFalse(cfg.stt.word_timestamps)  # 세그먼트 시각만 쓰므로 기본 off
        self.assertTrue(cfg.stt.batched)  # 배치 추론 기본 on (GPU·CPU 모두)
        self.assertEqual(cfg.stt.cpu_threads, 0)  # 0 = 물리 코어 수 자동 감지
        self.assertEqual(cfg.stt.vosk_model_size, "small")
        self.assertTrue(cfg.subtitles.use_manual)
        self.assertTrue(cfg.subtitles.use_auto_fallback)
        self.assertFalse(cfg.subtitles.stt_first)  # 수동 자막(업로더 제공) 우선이 기본
        self.assertEqual(cfg.llm.provider, "claude")  # 기본 프로바이더는 Claude CLI
        self.assertEqual(cfg.llm.model, "claude-opus-4-8")
        self.assertFalse(cfg.llm.repair_transcript)  # 자막 LLM 보정은 기본 off

    def test_llm_provider_and_repair_override(self):
        cfg = load_config(
            self._write(
                "videos: []\nllm:\n  provider: gemini\n  model: gemini-2.5-pro\n"
                "  repair_transcript: true\n"
            )
        )
        self.assertEqual(cfg.llm.provider, "gemini")
        self.assertEqual(cfg.llm.model, "gemini-2.5-pro")
        self.assertTrue(cfg.llm.repair_transcript)
        self.assertEqual(cfg.llm.max_chars_per_chunk, 8000)  # 미지정 기본 유지

    def test_partial_override_keeps_other_defaults(self):
        cfg = load_config(
            self._write(
                "videos: []\nlanguage: en\nstt:\n  model: small\n  device: cuda\n"
            )
        )
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.stt.model, "small")
        self.assertEqual(cfg.stt.device, "cuda")
        self.assertEqual(cfg.stt.compute_type, "auto")  # 미지정 기본 유지
        self.assertEqual(cfg.stt.engine, "faster-whisper")  # 미지정 기본 유지

    def test_vosk_engine_override(self):
        cfg = load_config(
            self._write(
                "videos: []\nstt:\n  engine: vosk\n  vosk_model_size: large\n"
            )
        )
        self.assertEqual(cfg.stt.engine, "vosk")
        self.assertEqual(cfg.stt.vosk_model_size, "large")
        self.assertEqual(cfg.stt.model, "auto")  # 미지정 기본 유지(엔진과 무관)


if __name__ == "__main__":
    unittest.main()
