"""llm.make_client / provider_available(프로바이더 추상화) 특성화 테스트."""

from __future__ import annotations

import unittest
from unittest import mock

from yke import llm
from yke.config import LLMConfig


class TestMakeClient(unittest.TestCase):
    def test_claude_provider(self) -> None:
        with mock.patch("yke.llm.claude_client.ClaudeClient") as C:
            llm.make_client(LLMConfig(provider="claude"))
        C.assert_called_once()

    def test_gemini_provider(self) -> None:
        with mock.patch("yke.llm.gemini_client.GeminiClient") as G:
            llm.make_client(LLMConfig(provider="gemini"))
        G.assert_called_once()

    def test_provider_is_case_insensitive(self) -> None:
        with mock.patch("yke.llm.gemini_client.GeminiClient") as G:
            llm.make_client(LLMConfig(provider="GEMINI"))
        G.assert_called_once()

    def test_unknown_provider_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            llm.make_client(LLMConfig(provider="bogus"))


class TestProviderAvailable(unittest.TestCase):
    def test_gemini_dispatch(self) -> None:
        with mock.patch("yke.llm.gemini_client.is_available", return_value=True):
            self.assertTrue(llm.provider_available("gemini"))

    def test_claude_dispatch(self) -> None:
        with mock.patch("yke.llm.claude_client.is_available", return_value=False):
            self.assertFalse(llm.provider_available("claude"))

    def test_unknown_provider_false(self) -> None:
        self.assertFalse(llm.provider_available("bogus"))


if __name__ == "__main__":
    unittest.main()
