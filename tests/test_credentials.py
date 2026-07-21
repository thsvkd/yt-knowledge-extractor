"""llm.credentials(Gemini API 키 보관) 특성화 테스트.

keyring 백엔드는 sys.modules 로 가짜를 주입해 실제 OS 자격증명 저장소를 건드리지 않는다.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from yke.llm import credentials


class TestGetKey(unittest.TestCase):
    def test_env_gemini_key_takes_precedence(self) -> None:
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": " env-key "}, clear=False):
            self.assertEqual(credentials.get_gemini_api_key(), "env-key")

    def test_google_api_key_env_also_read(self) -> None:
        env = {"GOOGLE_API_KEY": "g-key"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(credentials.get_gemini_api_key(), "g-key")

    def test_falls_back_to_keyring(self) -> None:
        fake = mock.Mock()
        fake.get_password.return_value = " kr-key "
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.dict(
            "sys.modules", {"keyring": fake}
        ):
            self.assertEqual(credentials.get_gemini_api_key(), "kr-key")
        fake.get_password.assert_called_once()

    def test_none_when_nothing_set(self) -> None:
        fake = mock.Mock()
        fake.get_password.return_value = None
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.dict(
            "sys.modules", {"keyring": fake}
        ):
            self.assertIsNone(credentials.get_gemini_api_key())

    def test_keyring_import_failure_is_safe(self) -> None:
        # keyring 이 아예 없거나 백엔드 오류여도 앱이 죽지 않고 None 으로 폴백한다.
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "builtins.__import__", side_effect=ImportError("no keyring")
        ):
            self.assertIsNone(credentials.get_gemini_api_key())


class TestSetKey(unittest.TestCase):
    def test_set_trims_and_stores(self) -> None:
        fake = mock.Mock()
        with mock.patch.dict("sys.modules", {"keyring": fake}):
            credentials.set_gemini_api_key("  my-key  ")
        args = fake.set_password.call_args[0]
        self.assertEqual(args[2], "my-key")

    def test_set_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            credentials.set_gemini_api_key("   ")

    def test_set_backend_failure_raises_runtime(self) -> None:
        fake = mock.Mock()
        fake.set_password.side_effect = RuntimeError("no backend")
        with mock.patch.dict("sys.modules", {"keyring": fake}):
            with self.assertRaises(RuntimeError):
                credentials.set_gemini_api_key("k")


class TestHasKey(unittest.TestCase):
    def test_has_key_true_false(self) -> None:
        with mock.patch.object(credentials, "get_gemini_api_key", return_value="k"):
            self.assertTrue(credentials.has_gemini_api_key())
        with mock.patch.object(credentials, "get_gemini_api_key", return_value=None):
            self.assertFalse(credentials.has_gemini_api_key())


if __name__ == "__main__":
    unittest.main()
