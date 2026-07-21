"""GUI LLM 프로바이더/모델 선택 로직 특성화 테스트.

flet Page 없이 PipelineGUI 를 __init__ 우회로 만들어(test_gui_progress 와 같은 패턴)
순수 로직만 검증한다: 프로바이더별 모델 옵션, 선택 모델 해석, 프로바이더 준비 여부.
"""

from __future__ import annotations

import unittest
from unittest import mock

from yke.gui import PipelineGUI


class _DD:
    """value/text 만 흉내내는 Dropdown 스텁."""

    def __init__(self, value=None, text=None):
        self.value = value
        self.text = text


class TestLlmOptions(unittest.TestCase):
    def test_claude_presets(self) -> None:
        keys = [o.key for o in PipelineGUI._llm_options("claude-opus-4-8", "claude")]
        self.assertIn("claude-opus-4-8", keys)
        self.assertIn("claude-sonnet-5", keys)
        self.assertNotIn("gemini-2.5-flash", keys)

    def test_gemini_presets(self) -> None:
        keys = [o.key for o in PipelineGUI._llm_options("gemini-2.5-flash", "gemini")]
        self.assertIn("gemini-2.5-flash", keys)
        self.assertIn("gemini-2.5-pro", keys)
        self.assertNotIn("claude-opus-4-8", keys)

    def test_custom_model_prepended(self) -> None:
        opts = PipelineGUI._llm_options("gemini-3.0-custom", "gemini")
        self.assertEqual(opts[0].key, "gemini-3.0-custom")


class TestSelectedModel(unittest.TestCase):
    def _gui(self, provider, value, text) -> PipelineGUI:
        g = object.__new__(PipelineGUI)
        g.llm_provider_dd = _DD(value=provider)
        g.llm_model_dd = _DD(value=value, text=text)
        return g

    def test_preset_label_maps_to_id(self) -> None:
        g = self._gui("gemini", "gemini-2.5-flash", "Gemini 2.5 Flash (안정)")
        self.assertEqual(g._selected_llm_model(), "gemini-2.5-flash")

    def test_custom_text_kept(self) -> None:
        g = self._gui("gemini", None, "gemini-3.0-x")
        self.assertEqual(g._selected_llm_model(), "gemini-3.0-x")

    def test_empty_falls_back_to_provider_default(self) -> None:
        g = self._gui("gemini", None, "")
        self.assertEqual(g._selected_llm_model(), "gemini-flash-latest")

    def test_value_used_when_no_text(self) -> None:
        g = self._gui("claude", "claude-sonnet-5", "")
        self.assertEqual(g._selected_llm_model(), "claude-sonnet-5")


class TestGeminiLiveOptions(unittest.TestCase):
    def test_alias_annotated_and_live_appended(self) -> None:
        live = [
            ("gemini-3.6-flash", "Gemini 3.6 Flash"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),  # 프리셋(안정)과 중복 → 라이브에서 생략
        ]
        targets = {"gemini-flash-latest": "gemini-3.6-flash"}
        opts = PipelineGUI._gemini_options(live, targets)
        by_key = {o.key: o.text for o in opts}
        # 별칭은 해석된 구체 모델명을 병기한다.
        self.assertIn("→ gemini-3.6-flash", by_key["gemini-flash-latest"])
        # 프리셋에 없는 구체 모델은 표시명+ID 로 추가된다.
        self.assertEqual(by_key["gemini-3.6-flash"], "Gemini 3.6 Flash (gemini-3.6-flash)")
        # 프리셋에 이미 있는 안정 ID 는 중복되지 않고 프리셋 라벨을 유지한다.
        self.assertEqual(by_key["gemini-2.5-flash"], "Gemini 2.5 Flash (안정)")

    def test_alias_without_target_stays_plain(self) -> None:
        opts = PipelineGUI._gemini_options([], {})
        by_key = {o.key: o.text for o in opts}
        self.assertEqual(by_key["gemini-flash-latest"], "Gemini Flash (최신)")


class TestProviderReady(unittest.TestCase):
    def test_gemini_needs_sdk_and_key(self) -> None:
        with mock.patch("yke.gui._genai_available", return_value=True), \
             mock.patch("yke.gui.has_gemini_api_key", return_value=True):
            self.assertTrue(PipelineGUI._provider_ready("gemini"))
        with mock.patch("yke.gui._genai_available", return_value=True), \
             mock.patch("yke.gui.has_gemini_api_key", return_value=False):
            self.assertFalse(PipelineGUI._provider_ready("gemini"))

    def test_claude_uses_cli(self) -> None:
        with mock.patch("yke.gui._claude_cli_available", return_value=True):
            self.assertTrue(PipelineGUI._provider_ready("claude"))
        with mock.patch("yke.gui._claude_cli_available", return_value=False):
            self.assertFalse(PipelineGUI._provider_ready("claude"))


if __name__ == "__main__":
    unittest.main()
