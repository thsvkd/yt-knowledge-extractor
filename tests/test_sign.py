"""코드 서명 인자 조립(scripts/sign.py) 고정.

여기서 막으려는 두 가지 사고:

1. **macOS 봉인이 깨진 채 배포되는 것.** vpk 는 pack 중에 UpdateMac 과 sq.version 을
   ``Contents/MacOS`` 에 끼워 넣으므로, build.py 가 prune 직후 재서명해 두어도 그 시점에
   앱 봉인이 다시 깨진다. ``--signAppIdentity`` 를 생략하면 Velopack 은 codesign 을 아예
   돌리지 않아 그 깨진 봉인이 그대로 설치기에 들어간다. 그래서 이 인자는 **환경 변수가
   하나도 없어도 반드시** 붙어야 한다(기본 ad-hoc).
2. **.pfx 비밀번호가 로그에 남는 것.** 서명이 실패하면 커맨드를 그대로 출력하는데, 그
   문자열에 ``/p <비밀번호>`` 가 들어 있다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import sign  # noqa: E402 - scripts/ 를 sys.path 에 넣은 뒤에만 import 가능

_MACOS_ENV_VARS = (
    "YKE_SIGN_APP_IDENTITY",
    "YKE_SIGN_INSTALL_IDENTITY",
    "YKE_SIGN_NOTARY_PROFILE",
)


class VelopackSignArgsMacosTest(unittest.TestCase):
    def test_adhoc_identity_is_the_default(self) -> None:
        """환경 변수가 하나도 없어도 --signAppIdentity 는 붙는다.

        이게 빠지면 Velopack 이 codesign 을 돌리지 않아, vpk 가 방금 깨뜨린 봉인이 그대로
        설치기에 들어간다(모듈 docstring 참고).
        """
        with mock.patch.dict("os.environ", dict.fromkeys(_MACOS_ENV_VARS, "")):
            args = sign.velopack_sign_args_macos()

        self.assertEqual(args, ["--signAppIdentity", "-"])

    def test_configured_identity_replaces_the_adhoc_default(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                **dict.fromkeys(_MACOS_ENV_VARS, ""),
                "YKE_SIGN_APP_IDENTITY": "Developer ID Application: thsvkd (TEAM123)",
            },
        ):
            args = sign.velopack_sign_args_macos()

        self.assertEqual(args, ["--signAppIdentity", "Developer ID Application: thsvkd (TEAM123)"])
        self.assertEqual(args.count("--signAppIdentity"), 1, "ad-hoc 기본값이 함께 남으면 안 된다")

    def test_installer_and_notary_are_independent(self) -> None:
        """세 항목은 서로 독립이다 — 설치기 신원만 준 경우도 그대로 성립해야 한다."""
        with mock.patch.dict(
            "os.environ",
            {
                **dict.fromkeys(_MACOS_ENV_VARS, ""),
                "YKE_SIGN_INSTALL_IDENTITY": "Developer ID Installer: thsvkd",
                "YKE_SIGN_NOTARY_PROFILE": "yke-notary",
            },
        ):
            args = sign.velopack_sign_args_macos()

        self.assertEqual(args[:2], ["--signAppIdentity", "-"])
        self.assertEqual(
            args[args.index("--signInstallIdentity") + 1], "Developer ID Installer: thsvkd"
        )
        self.assertEqual(args[args.index("--notaryProfile") + 1], "yke-notary")

    def test_whitespace_only_value_is_treated_as_unset(self) -> None:
        """빈 신원을 그대로 넘기면 codesign 이 알 수 없는 오류로 죽는다."""
        with mock.patch.dict(
            "os.environ",
            {**dict.fromkeys(_MACOS_ENV_VARS, ""), "YKE_SIGN_APP_IDENTITY": "   "},
        ):
            args = sign.velopack_sign_args_macos()

        self.assertEqual(args, ["--signAppIdentity", "-"])


class MaskSignParamsTest(unittest.TestCase):
    def test_pfx_password_is_hidden(self) -> None:
        masked = sign.mask_sign_params("/f cert.pfx /p s3cret /fd SHA256")

        self.assertNotIn("s3cret", masked)
        self.assertEqual(masked, "/f cert.pfx /p *** /fd SHA256")

    def test_everything_else_is_kept(self) -> None:
        """인증서 경로·타임스탬프는 실패 원인을 짚는 데 필요하므로 가리지 않는다."""
        params = "/sha1 ABCDEF /fd SHA256 /tr http://timestamp.digicert.com /td SHA256"

        self.assertEqual(sign.mask_sign_params(params), params)

    def test_trailing_p_without_value_does_not_crash(self) -> None:
        """마지막 토큰이 /p 인 경우에도 죽지 않아야 한다(짝이 없는 인자)."""
        self.assertEqual(sign.mask_sign_params("/f cert.pfx /p"), "/f cert.pfx /p")


if __name__ == "__main__":
    unittest.main()
