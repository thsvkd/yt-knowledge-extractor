"""macOS 빌드 사전 점검(scripts/build.py:ensure_macos_toolchain) 검증.

이 점검이 물러지면 **빌드가 불가능한 머신이 초록불을 받는다**. 실제로 겪은 함정이다:
Command Line Tools 만 깔린 맥에서도 `xcode-select -p` 는 성공하고 pkgbuild·ditto·codesign
같은 vpk 가 쓰는 명령도 전부 CLT 에 들어 있어 존재 확인을 통과한다. 그래서 예전 점검은
통과시켰고, Flutter SDK 를 다 내려받은 뒤 몇 분 지나서야 `flet build macos` 가
"Xcode installation is incomplete" 로 죽었다 — 원인이 환경 문제라는 게 한참 뒤에 드러났다.

전체 Xcode 유무를 가르는 것은 xcodebuild 의 성공 여부다(CLT 전용 환경에서는 실행이
실패한다). 그 판정이 유지되는지를 고정한다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import build  # noqa: E402 - scripts/ 를 sys.path 에 넣은 뒤에만 import 가능

# CLT 전용 맥에서 실제로 나온 출력(2026-08 실측).
_CLT_DIR = "/Library/Developer/CommandLineTools"
_XCODEBUILD_CLT_ERROR = (
    "xcode-select: error: tool 'xcodebuild' requires Xcode, but active developer "
    f"directory '{_CLT_DIR}' is a command line tools instance"
)
_XCODE_DIR = "/Applications/Xcode.app/Contents/Developer"
_XCODEBUILD_OK = "Xcode 16.2\nBuild version 16C5032a"


class EnsureMacosToolchainTest(unittest.TestCase):
    """``ensure_macos_toolchain`` 이 실패해야 할 때 실패하는지."""

    def setUp(self) -> None:
        self._orig_run = build.subprocess.run
        self._orig_which = build.shutil.which
        self._orig_info = build.info
        self.addCleanup(self._restore)
        # 안내 문구가 테스트 출력에 섞이지 않게 한다.
        build.info = lambda message: None

    def _restore(self) -> None:
        build.subprocess.run = self._orig_run
        build.shutil.which = self._orig_which
        build.info = self._orig_info

    def _install(self, *, developer_dir: str, xcodebuild_ok: bool, tools: set[str]) -> None:
        """지정한 환경을 흉내 내도록 subprocess.run / shutil.which 를 갈아 끼운다."""

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "xcode-select":
                return subprocess.CompletedProcess(cmd, 0, developer_dir + "\n", "")
            if cmd[0] == "xcodebuild":
                if xcodebuild_ok:
                    return subprocess.CompletedProcess(cmd, 0, _XCODEBUILD_OK, "")
                return subprocess.CompletedProcess(cmd, 1, "", _XCODEBUILD_CLT_ERROR)
            raise AssertionError(f"예상치 못한 명령: {cmd}")

        build.subprocess.run = fake_run
        build.shutil.which = lambda tool: f"/usr/bin/{tool}" if tool in tools else None

    def test_command_line_tools_only_is_rejected(self) -> None:
        """CLT 전용: xcode-select 도 vpk 용 명령도 다 통과하지만 빌드는 불가능하다."""
        self._install(
            developer_dir=_CLT_DIR,
            xcodebuild_ok=False,
            tools={*build._MACOS_TOOLS, "pod"},
        )
        with self.assertRaises(SystemExit):
            build.ensure_macos_toolchain()

    def test_missing_cocoapods_is_rejected(self) -> None:
        """전체 Xcode 가 있어도 CocoaPods 가 없으면 Flutter 플러그인 단계에서 죽는다."""
        self._install(
            developer_dir=_XCODE_DIR,
            xcodebuild_ok=True,
            tools=set(build._MACOS_TOOLS),  # pod 없음
        )
        with self.assertRaises(SystemExit):
            build.ensure_macos_toolchain()

    def test_missing_velopack_tool_is_rejected(self) -> None:
        """vpk 가 .pkg 를 만들며 직접 부르는 명령이 하나라도 없으면 중단한다."""
        self._install(
            developer_dir=_XCODE_DIR,
            xcodebuild_ok=True,
            tools={*(t for t in build._MACOS_TOOLS if t != "pkgbuild"), "pod"},
        )
        with self.assertRaises(SystemExit):
            build.ensure_macos_toolchain()

    def test_full_xcode_with_cocoapods_passes(self) -> None:
        """정상 환경은 통과해야 한다 — 점검이 과하게 조여 빌드를 막으면 안 된다."""
        self._install(
            developer_dir=_XCODE_DIR,
            xcodebuild_ok=True,
            tools={*build._MACOS_TOOLS, "pod"},
        )
        build.ensure_macos_toolchain()  # 예외 없이 끝나야 한다.


class PruneExternalSymlinksTest(unittest.TestCase):
    """``.app`` 밖을 가리키는 링크만 지우고 안쪽 링크는 반드시 보존해야 한다.

    실제로 겪은 두 가지 실패가 이 테스트의 존재 이유다.
    1. 링크를 안 지우면: flet 이 남긴 ``site-packages/.pod`` 가 빌드 머신의 pub 캐시를
       가리키고 그 안에서 자기 자신으로 되돌아와, ``vpk pack`` 이 순환을 따라가다
       PathTooLongException 으로 죽는다.
    2. 판정을 잘못하면: 프레임워크의 정상 상대 링크(``Versions/Current`` 등)까지 지워
       번들이 통째로 깨진다(상대 경로로 walk 해서 60개 중 60개를 지운 적이 있다).
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.outside = self.root / "outside"
        (self.outside / "cache").mkdir(parents=True)
        self.bundle = self.root / "app.app"
        (self.bundle / "Contents" / "Frameworks" / "X.framework" / "Versions" / "A").mkdir(
            parents=True
        )
        (self.bundle / "Contents" / "MacOS").mkdir(parents=True)
        (self.bundle / "Contents" / "MacOS" / "app").write_text("bin")

    def _links(self) -> set[str]:
        """남아 있는 심링크의 번들 기준 상대 경로(POSIX 표기).

        구분자를 ``str()`` 로 OS 에 맡기면 Windows 에서 ``a\\b`` 가 되어, 아래 어서션이
        쓰는 ``a/b`` 와 절대 일치하지 않는다. ``assertIn`` 은 그래서 실패하고, 더 나쁜 건
        ``assertNotIn`` 이 **링크가 남아 있어도 항상 통과**해 검증이 조용히 사라진다는
        것이다(실제로 겪음). 대상 코드는 macOS 전용이지만 테스트는 모든 OS 에서 돈다.
        """
        return {
            p.relative_to(self.bundle).as_posix() for p in self.bundle.rglob("*") if p.is_symlink()
        }

    def test_internal_relative_link_is_kept(self) -> None:
        versions = self.bundle / "Contents" / "Frameworks" / "X.framework" / "Versions"
        (versions / "Current").symlink_to("A")
        removed = build.prune_external_symlinks(self.bundle)
        self.assertEqual(removed, [])
        self.assertIn("Contents/Frameworks/X.framework/Versions/Current", self._links())

    def test_internal_relative_link_is_kept_when_called_with_relative_path(self) -> None:
        """상대 경로로 불러도 결과가 같아야 한다 — 이 구분을 놓쳐 번들을 통째로 지운 적이 있다."""
        versions = self.bundle / "Contents" / "Frameworks" / "X.framework" / "Versions"
        (versions / "Current").symlink_to("A")
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        removed = build.prune_external_symlinks(Path("app.app"))
        self.assertEqual(removed, [])
        self.assertIn("Contents/Frameworks/X.framework/Versions/Current", self._links())

    def test_external_absolute_link_is_removed(self) -> None:
        """flet 의 site-packages/.pod 가 정확히 이 모양이다(절대 경로로 번들 밖을 가리킴)."""
        link = self.bundle / "Contents" / "MacOS" / ".pod"
        link.symlink_to(self.outside / "cache")
        removed = build.prune_external_symlinks(self.bundle)
        self.assertEqual([p.name for p in removed], [".pod"])
        self.assertNotIn("Contents/MacOS/.pod", self._links())

    def test_external_relative_link_is_removed(self) -> None:
        """.. 로 번들을 빠져나가는 링크도 같은 문제다(경로 문자열만으로 판정해야 잡힌다)."""
        link = self.bundle / "Contents" / "escape"
        link.symlink_to(Path("..") / ".." / "outside" / "cache")
        removed = build.prune_external_symlinks(self.bundle)
        self.assertEqual([p.name for p in removed], ["escape"])

    def test_recursive_link_does_not_hang(self) -> None:
        """순환 링크에서 realpath 를 쓰면 여기서 멈춘다 — normpath 로만 판정하는 이유."""
        loop = self.outside / "loop"
        loop.symlink_to(self.outside)  # outside/loop -> outside (자기 자신)
        (self.bundle / "Contents" / "MacOS" / ".pod").symlink_to(loop)
        removed = build.prune_external_symlinks(self.bundle)
        self.assertEqual([p.name for p in removed], [".pod"])

    def test_non_link_files_are_untouched(self) -> None:
        build.prune_external_symlinks(self.bundle)
        self.assertTrue((self.bundle / "Contents" / "MacOS" / "app").is_file())


class ResignAdhocTest(unittest.TestCase):
    """링크를 지운 번들을 ad-hoc 으로 다시 봉인하는지.

    지운 ``.pod`` 는 프레임워크의 ``_CodeSignature/CodeResources`` 에 봉인된 리소스로
    등재돼 있어서, 파일만 지우면 번들이 ``a sealed resource is missing or invalid`` 가
    된다(실측: v0.1.5 를 만든 빌드가 그 상태였다). 개발 머신에서는 quarantine 이 없어
    그냥 실행되므로 **로컬 실행만으로는 드러나지 않는다** — 그래서 테스트로 고정한다.

    실제 서명 여부는 여기서 검증할 수 없다(codesign 은 macOS 전용이고 CI 는 다른 OS 에서도
    돈다). 검증 대상은 "커맨드가 맞는가"와 "부르는 조건이 맞는가" 두 가지다.
    """

    def test_command_is_force_deep_adhoc(self) -> None:
        calls: list[list[str]] = []
        build.resign_adhoc(Path("/tmp/app.app"), runner=calls.append)
        self.assertEqual(calls, [["codesign", "--force", "--deep", "--sign", "-", "/tmp/app.app"]])

    def test_failure_is_not_swallowed(self) -> None:
        """재서명 실패를 삼키면 깨진 봉인이 그대로 배포된다.

        기본 runner 는 ``_common.check`` 라 종료 코드가 0 이 아니면 SystemExit 로 죽는다.
        여기서는 그 계약(예외가 호출자에게 전파된다)만 고정한다.
        """

        def boom(_cmd: list[str]) -> None:
            raise SystemExit(1)

        with self.assertRaises(SystemExit):
            build.resign_adhoc(Path("/tmp/app.app"), runner=boom)


if __name__ == "__main__":
    unittest.main()
