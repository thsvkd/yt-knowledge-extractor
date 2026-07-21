"""gemini_client(GeminiClient, google-genai 래퍼) 특성화 테스트.

실제 네트워크/키 없이 동작을 검증한다 — google.genai.Client 를 목으로 대체하고,
자격증명/설치 검사는 모듈 함수를 패치한다.
"""

from __future__ import annotations

import unittest
from unittest import mock

from yke.llm import gemini_client


class TestResolveModel(unittest.TestCase):
    def test_claude_id_falls_back_to_default(self) -> None:
        self.assertEqual(gemini_client._resolve_model("claude-opus-4-8"), "gemini-flash-latest")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(gemini_client._resolve_model(""), "gemini-flash-latest")

    def test_gemini_id_kept(self) -> None:
        self.assertEqual(gemini_client._resolve_model("gemini-2.5-pro"), "gemini-2.5-pro")


class TestIsAvailable(unittest.TestCase):
    def test_needs_both_sdk_and_key(self) -> None:
        cases = [(True, True, True), (True, False, False), (False, True, False)]
        for sdk, key, expected in cases:
            with mock.patch.object(gemini_client, "genai_available", return_value=sdk), \
                 mock.patch.object(gemini_client, "has_gemini_api_key", return_value=key):
                self.assertEqual(gemini_client.is_available(), expected, (sdk, key))


class TestGeminiClient(unittest.TestCase):
    def setUp(self) -> None:
        # SDK 있음 + 키 있음으로 가장.
        p1 = mock.patch.object(gemini_client, "genai_available", return_value=True)
        p2 = mock.patch.object(gemini_client, "get_gemini_api_key", return_value="AIza-test")
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _client_with(self, resp) -> tuple[gemini_client.GeminiClient, mock.Mock]:
        fake_client = mock.Mock()
        fake_client.models.generate_content.return_value = resp
        with mock.patch("google.genai.Client", return_value=fake_client):
            client = gemini_client.GeminiClient()
        return client, fake_client

    def test_init_requires_key(self) -> None:
        with mock.patch.object(gemini_client, "get_gemini_api_key", return_value=None):
            with self.assertRaises(RuntimeError):
                gemini_client.GeminiClient()

    def test_init_requires_sdk(self) -> None:
        with mock.patch.object(gemini_client, "genai_available", return_value=False):
            with self.assertRaises(RuntimeError):
                gemini_client.GeminiClient()

    def test_complete_returns_text(self) -> None:
        client, fake = self._client_with(mock.Mock(text="hello", candidates=[]))
        out = client.complete("sys", "user text", model="gemini-2.5-flash")
        self.assertEqual(out, "hello")
        _args, kwargs = fake.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-2.5-flash")
        self.assertEqual(kwargs["contents"], "user text")
        # system 은 system_instruction 으로 전달된다.
        self.assertEqual(kwargs["config"].system_instruction, "sys")

    def test_complete_resolves_non_gemini_model(self) -> None:
        client, fake = self._client_with(mock.Mock(text="ok", candidates=[]))
        client.complete("s", "u", model="claude-opus-4-8")
        _args, kwargs = fake.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-flash-latest")

    def test_empty_response_raises(self) -> None:
        client, _ = self._client_with(mock.Mock(text="", candidates=[]))
        with self.assertRaises(RuntimeError):
            client.complete("s", "u", model="gemini-2.5-flash")

    def test_generate_error_wrapped(self) -> None:
        fake_client = mock.Mock()
        fake_client.models.generate_content.side_effect = ValueError("boom")
        with mock.patch("google.genai.Client", return_value=fake_client):
            client = gemini_client.GeminiClient()
        with self.assertRaisesRegex(RuntimeError, "Gemini 호출 실패"):
            client.complete("s", "u", model="gemini-2.5-flash")


class TestListGenerateModels(unittest.TestCase):
    @staticmethod
    def _model(name: str, actions, display=None):
        m = mock.Mock(supported_actions=actions, display_name=display)
        m.name = name  # mock.Mock(name=...) 는 특수 처리되므로 반드시 사후 대입.
        return m

    def test_empty_when_unavailable(self) -> None:
        with mock.patch.object(gemini_client, "is_available", return_value=False):
            self.assertEqual(gemini_client.list_generate_models(), [])

    def test_filters_generatecontent_and_returns_id_display_sorted(self) -> None:
        fake_client = mock.Mock()
        fake_client.models.list.return_value = [
            self._model("models/gemini-2.5-flash", ["generateContent"], display=None),
            self._model("models/gemini-3-pro", ["generateContent"], display="Gemini 3 Pro"),
            self._model("models/text-embedding-004", ["embedContent"]),  # 비 gemini
            self._model("models/gemini-embedding-001", ["embedContent"]),  # 임베딩 제외
            self._model("models/gemini-1.5-pro", None, display="Gemini 1.5 Pro"),  # actions 없음 → 포함
            self._model("models/gemini-2.0-flash", ["countTokens"]),  # generateContent 미지원 제외
        ]
        with mock.patch.object(gemini_client, "is_available", return_value=True), \
             mock.patch.object(gemini_client, "get_gemini_api_key", return_value="k"), \
             mock.patch("google.genai.Client", return_value=fake_client):
            out = gemini_client.list_generate_models()
        self.assertEqual(
            out,
            [
                ("gemini-3-pro", "Gemini 3 Pro"),
                ("gemini-2.5-flash", "gemini-2.5-flash"),  # display 없으면 ID 로 폴백
                ("gemini-1.5-pro", "Gemini 1.5 Pro"),
            ],
        )

    def test_exception_returns_empty(self) -> None:
        with mock.patch.object(gemini_client, "is_available", return_value=True), \
             mock.patch.object(gemini_client, "get_gemini_api_key", return_value="k"), \
             mock.patch("google.genai.Client", side_effect=RuntimeError("net down")):
            self.assertEqual(gemini_client.list_generate_models(), [])


class TestResolveAliases(unittest.TestCase):
    def test_empty_when_unavailable(self) -> None:
        with mock.patch.object(gemini_client, "is_available", return_value=False):
            self.assertEqual(gemini_client.resolve_aliases(), {})

    def test_resolves_concrete_name(self) -> None:
        def _get(model):
            m = mock.Mock(version=None)
            m.name = "models/gemini-3.6-flash" if model == "gemini-flash-latest" else "models/" + model
            return m

        fake = mock.Mock()
        fake.models.get.side_effect = _get
        with mock.patch.object(gemini_client, "is_available", return_value=True), \
             mock.patch.object(gemini_client, "get_gemini_api_key", return_value="k"), \
             mock.patch("google.genai.Client", return_value=fake):
            out = gemini_client.resolve_aliases(("gemini-flash-latest",))
        self.assertEqual(out, {"gemini-flash-latest": "gemini-3.6-flash"})

    def test_skips_when_name_echoes_alias(self) -> None:
        # API 가 별칭 이름을 그대로 돌려주면 구체 ID 로 해석 못 한 것 → 생략(호출부가 라이브
        # 목록에서 티어별 최신을 추정한다).
        m = mock.Mock(version="3.6")
        m.name = "models/gemini-flash-latest"
        fake = mock.Mock()
        fake.models.get.return_value = m
        with mock.patch.object(gemini_client, "is_available", return_value=True), \
             mock.patch.object(gemini_client, "get_gemini_api_key", return_value="k"), \
             mock.patch("google.genai.Client", return_value=fake):
            out = gemini_client.resolve_aliases(("gemini-flash-latest",))
        self.assertEqual(out, {})

    def test_skips_unresolvable_alias(self) -> None:
        fake = mock.Mock()
        fake.models.get.side_effect = RuntimeError("404")
        with mock.patch.object(gemini_client, "is_available", return_value=True), \
             mock.patch.object(gemini_client, "get_gemini_api_key", return_value="k"), \
             mock.patch("google.genai.Client", return_value=fake):
            out = gemini_client.resolve_aliases(("gemini-flash-latest",))
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()
