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
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.dict("sys.modules", {"keyring": fake}),
        ):
            self.assertEqual(credentials.get_gemini_api_key(), "kr-key")
        fake.get_password.assert_called_once()

    def test_none_when_nothing_set(self) -> None:
        fake = mock.Mock()
        fake.get_password.return_value = None
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.dict("sys.modules", {"keyring": fake}),
        ):
            self.assertIsNone(credentials.get_gemini_api_key())

    def test_keyring_import_failure_is_safe(self) -> None:
        # keyring 이 아예 없거나 백엔드 오류여도 앱이 죽지 않고 None 으로 폴백한다.
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("builtins.__import__", side_effect=ImportError("no keyring")),
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


class TestBackendPinning(unittest.TestCase):
    """백엔드를 자동 탐색에 맡기지 않고 명시 지정하는지.

    자동 탐색은 번들에 dist-info 가 없으면 조용히 빈 백엔드를 고른다 — 개발에서는 통과하고
    배포본에서만 죽는 유형이라 단위 테스트로 잡아 둔다.
    """

    def setUp(self) -> None:
        # 프로세스당 한 번만 도는 플래그를 테스트마다 되돌린다.
        credentials._backend_pinned = False
        self.addCleanup(setattr, credentials, "_backend_pinned", False)

    def test_pins_macos_backend(self) -> None:
        fake = mock.MagicMock()
        with mock.patch.object(credentials.sys, "platform", "darwin"):
            credentials._pin_backend(fake)
        fake.set_keyring.assert_called_once()

    def test_pins_windows_backend(self) -> None:
        fake = mock.MagicMock()
        with mock.patch.object(credentials.sys, "platform", "win32"):
            credentials._pin_backend(fake)
        fake.set_keyring.assert_called_once()

    def test_linux_leaves_autodiscovery(self) -> None:
        """리눅스는 SecretService 자동 탐색에 맡긴다(백엔드가 배포판마다 다르다)."""
        fake = mock.MagicMock()
        with mock.patch.object(credentials.sys, "platform", "linux"):
            credentials._pin_backend(fake)
        fake.set_keyring.assert_not_called()

    def test_pins_only_once(self) -> None:
        fake = mock.MagicMock()
        with mock.patch.object(credentials.sys, "platform", "darwin"):
            credentials._pin_backend(fake)
            credentials._pin_backend(fake)
        self.assertEqual(fake.set_keyring.call_count, 1)

    def test_pin_failure_falls_back_silently(self) -> None:
        """지정에 실패해도 앱이 죽으면 안 된다(자동 탐색으로 폴백)."""
        fake = mock.MagicMock()
        fake.set_keyring.side_effect = RuntimeError("no backend")
        with mock.patch.object(credentials.sys, "platform", "darwin"):
            credentials._pin_backend(fake)  # 예외가 새어 나오면 실패


class TestWindowsSizeLimit(unittest.TestCase):
    """Windows 자격 증명 관리자 한도를 넘는 값은 저장 전에 끊는지.

    넘으면 OS 가 조용히 실패시켜 "저장했다는데 다시 열면 없는" 증상이 된다.
    """

    def test_oversized_key_rejected_on_windows(self) -> None:
        huge = "k" * (credentials._WINDOWS_CRED_MAX_BYTES // 2 + 1)
        with mock.patch.object(credentials.sys, "platform", "win32"):
            with self.assertRaises(ValueError):
                credentials.set_gemini_api_key(huge)

    def test_normal_key_passes_on_windows(self) -> None:
        fake = mock.MagicMock()
        with (
            mock.patch.object(credentials.sys, "platform", "win32"),
            mock.patch.dict("sys.modules", {"keyring": fake}),
        ):
            credentials.set_gemini_api_key("AIzaSy" + "x" * 33)
        fake.set_password.assert_called_once()

    def test_limit_not_applied_on_other_platforms(self) -> None:
        """macOS 키체인에는 이 한도가 없다 — 공연히 막지 않는다."""
        huge = "k" * (credentials._WINDOWS_CRED_MAX_BYTES // 2 + 1)
        fake = mock.MagicMock()
        with (
            mock.patch.object(credentials.sys, "platform", "darwin"),
            mock.patch.dict("sys.modules", {"keyring": fake}),
        ):
            credentials.set_gemini_api_key(huge)
        fake.set_password.assert_called_once()


if __name__ == "__main__":
    unittest.main()
