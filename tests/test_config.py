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
        self.assertEqual(cfg.stt.model, "large-v3")
        self.assertEqual(cfg.stt.device, "auto")
        self.assertTrue(cfg.subtitles.use_manual)
        self.assertTrue(cfg.subtitles.use_auto_fallback)
        self.assertEqual(cfg.llm.model, "claude-opus-4-8")

    def test_partial_override_keeps_other_defaults(self):
        cfg = load_config(
            self._write(
                "videos: []\nlanguage: en\nstt:\n  model: small\n  device: cuda\n"
            )
        )
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.stt.model, "small")
        self.assertEqual(cfg.stt.device, "cuda")
        self.assertEqual(cfg.stt.compute_type, "int8")  # 미지정 기본 유지


if __name__ == "__main__":
    unittest.main()
