"""OS 별 Velopack 규약(scripts/platform_spec.py) 고정.

build.py(패킹)와 deploy.py(업로드)가 파일명 규칙을 각자 하드코딩하면 한쪽만 바뀌었을 때
"빌드는 성공했는데 자동 업데이트가 안 되는" 조용한 실패가 난다(Velopack 은 릴리스에서
releases.<channel>.json 을 이름 완전 일치로만 찾는다). 그래서 이 테스트는 채널·실행파일
이름·글롭 값을 **하드코딩으로** 단언한다 — 계약 표에서 한 글자라도 어긋나면 즉시 깨지는
게 목적이므로, 여기서 platform_spec 의 상수를 다시 참조해 자기 자신을 검증하면 안 된다.

가장 중요한 것은 win/osx 글롭의 상호 배타성이다. dist/velopack 폴더를 두 채널이 공유하는
전제가 여기에 걸려 있다.
"""

from __future__ import annotations

import fnmatch
import sys
import tomllib
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import platform_spec  # noqa: E402 - scripts/ 를 sys.path 에 넣은 뒤에만 import 가능

# vpk 가 실제로 뱉는 산출물 이름(velopack 의 이름 규칙 기준). Portable.zip / assets.*.json /
# RELEASES-* 는 GithubSource 가 쓰지 않으므로 업로드 대상이 아니다 — 그런데도 같은 폴더에
# 놓여 있으니, 글롭이 이것들까지 주워 담지 않는지 확인해야 한다.
_WIN_FILES = [
    "YtKnowledgeExtractor-win-Setup.exe",
    "YtKnowledgeExtractor-0.1.5-full.nupkg",
    "YtKnowledgeExtractor-0.1.5-delta.nupkg",
    "releases.win.json",
    "YtKnowledgeExtractor-win-Portable.zip",
    "assets.win.json",
    "RELEASES-win",
]
_OSX_FILES = [
    "YtKnowledgeExtractor-osx-Setup.pkg",
    "YtKnowledgeExtractor-0.1.5-osx-full.nupkg",
    "YtKnowledgeExtractor-0.1.5-osx-delta.nupkg",
    "releases.osx.json",
    "YtKnowledgeExtractor-osx-Portable.zip",
    "assets.osx.json",
    "RELEASES-osx",
]


def _matched(globs: tuple[str, ...], names: list[str]) -> list[str]:
    """``globs`` 중 하나라도 매치하는 이름들(입력 순서 유지).

    fnmatchcase 를 쓰는 이유: fnmatch.fnmatch 는 Windows 에서 대소문자를 무시해
    같은 테스트가 OS 마다 다른 것을 검증하게 된다. 규약은 OS 와 무관해야 한다.
    """
    return [n for n in names if any(fnmatch.fnmatchcase(n, g) for g in globs)]


class TestSpecFields(unittest.TestCase):
    """계약 표의 5개 필드를 그대로 고정한다."""

    def test_windows(self) -> None:
        spec = platform_spec.spec_for("windows")
        self.assertEqual(spec.target, "windows")
        self.assertEqual(spec.channel, "win")
        self.assertEqual(spec.main_exe, "yt-knowledge-extractor.exe")
        self.assertEqual(spec.setup_glob, "*-Setup.exe")
        self.assertEqual(spec.releases_json, "releases.win.json")

    def test_macos(self) -> None:
        spec = platform_spec.spec_for("macos")
        self.assertEqual(spec.target, "macos")
        self.assertEqual(spec.channel, "osx")
        self.assertEqual(spec.main_exe, "yt-knowledge-extractor")
        self.assertEqual(spec.setup_glob, "*-Setup.pkg")
        self.assertEqual(spec.releases_json, "releases.osx.json")

    def test_unsupported_target_raises(self) -> None:
        # Linux 는 Velopack 배포 대상이 아니다. 조용히 win 규약으로 떨어지면 안 된다.
        with self.assertRaises(ValueError):
            platform_spec.spec_for("linux")


class TestArtifactName(unittest.TestCase):
    def test_comes_from_pyproject(self) -> None:
        """실행 파일 이름은 pyproject [project].name 이 SSOT다.

        하드코딩해 두면 이름이 바뀌었을 때 vpk 가 <app>/Contents/MacOS/<mainExe> 를 못 찾아
        빌드가 죽는다(Windows 는 <name>.exe).
        """
        data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(platform_spec.artifact_name(), "yt-knowledge-extractor")
        self.assertEqual(platform_spec.artifact_name(), data["project"]["name"])

    def test_main_exe_follows_artifact(self) -> None:
        # artifact 주입은 pyproject 를 읽지 않는 테스트용 경로다.
        self.assertEqual(platform_spec.spec_for("windows", artifact="foo").main_exe, "foo.exe")
        self.assertEqual(platform_spec.spec_for("macos", artifact="foo").main_exe, "foo")


class TestGlobsAreMutuallyExclusive(unittest.TestCase):
    """dist/velopack 을 win/osx 가 공유해도 안전하다는 전제를 고정한다.

    Velopack 은 Windows + 기본 채널(win)일 때만 nupkg 이름에서 채널 접미사를 생략하므로
    (-full/-delta vs -osx-full/-osx-delta), 두 채널 글롭은 서로의 파일을 절대 매치하지
    않는다. 이게 깨지면 두 번째 플랫폼 업로드가 상대 플랫폼 에셋을 덮어쓴다.
    """

    def setUp(self) -> None:
        self.win = platform_spec.spec_for("windows")
        self.osx = platform_spec.spec_for("macos")

    def test_win_globs_match_only_win_uploadables(self) -> None:
        globs = self.win.upload_globs("0.1.5")
        self.assertEqual(
            _matched(globs, _WIN_FILES),
            [
                "YtKnowledgeExtractor-win-Setup.exe",
                "YtKnowledgeExtractor-0.1.5-full.nupkg",
                "YtKnowledgeExtractor-0.1.5-delta.nupkg",
                "releases.win.json",
            ],
        )
        self.assertEqual(_matched(globs, _OSX_FILES), [])

    def test_osx_globs_match_only_osx_uploadables(self) -> None:
        globs = self.osx.upload_globs("0.1.5")
        self.assertEqual(
            _matched(globs, _OSX_FILES),
            [
                "YtKnowledgeExtractor-osx-Setup.pkg",
                "YtKnowledgeExtractor-0.1.5-osx-full.nupkg",
                "YtKnowledgeExtractor-0.1.5-osx-delta.nupkg",
                "releases.osx.json",
            ],
        )
        self.assertEqual(_matched(globs, _WIN_FILES), [])

    def test_upload_globs_order(self) -> None:
        # 업로드 순서(setup → full → delta → releases json)는 collect_assets 의 반환 순서와
        # 같아야 사람이 로그를 보고 빠진 파일을 알아챌 수 있다.
        self.assertEqual(
            self.win.upload_globs("0.1.5"),
            ("*-Setup.exe", "*-0.1.5-full.nupkg", "*-0.1.5-delta.nupkg", "releases.win.json"),
        )
        self.assertEqual(
            self.osx.upload_globs("0.1.5"),
            (
                "*-Setup.pkg",
                "*-0.1.5-osx-full.nupkg",
                "*-0.1.5-osx-delta.nupkg",
                "releases.osx.json",
            ),
        )

    def test_nupkg_globs(self) -> None:
        self.assertEqual(
            self.win.nupkg_globs("0.1.5"), ("*-0.1.5-full.nupkg", "*-0.1.5-delta.nupkg")
        )
        self.assertEqual(
            self.osx.nupkg_globs("0.1.5"),
            ("*-0.1.5-osx-full.nupkg", "*-0.1.5-osx-delta.nupkg"),
        )

    def test_win_and_osx_nupkg_names_can_never_collide(self) -> None:
        """이름이 같아지는 순간 두 번째 OS 의 `gh release upload --clobber` 가 첫 OS 의
        nupkg 를 덮어써 한쪽 플랫폼 업데이트가 깨진 채 릴리스가 나간다.

        위 테스트들은 "이 글롭이 이 파일명을 매치한다"를 고정하는데, 그것만으로는 두 채널이
        **같은 이름**을 쓰게 되는 변경을 잡지 못한다(둘 다 같은 이름을 매치하면 각자의
        단언은 그대로 통과한다). 그래서 글롭 자체가 서로 다른지를 따로 못 박는다.
        """
        for version in ("0.1.5", "1.0.0", "10.20.30"):
            win = self.win.nupkg_globs(version)
            osx = self.osx.nupkg_globs(version)
            self.assertEqual(len(set(win) | set(osx)), 4, f"{version}: 글롭이 겹칩니다")
            # setup 은 확장자(.exe/.pkg)가, 피드는 파일명이 채널로 갈린다.
            self.assertNotEqual(self.win.setup_glob, self.osx.setup_glob)
            self.assertNotEqual(self.win.releases_json, self.osx.releases_json)

    def test_globs_are_version_scoped(self) -> None:
        """vpk 가 델타 기준으로 내려받아 둔 이전 버전 nupkg 는 잡히면 안 된다."""
        stale = [
            "YtKnowledgeExtractor-0.1.4-full.nupkg",
            "YtKnowledgeExtractor-0.1.4-delta.nupkg",
            "YtKnowledgeExtractor-0.1.4-osx-full.nupkg",
            "YtKnowledgeExtractor-0.1.4-osx-delta.nupkg",
        ]
        self.assertEqual(_matched(self.win.upload_globs("0.1.5"), stale), [])
        self.assertEqual(_matched(self.osx.upload_globs("0.1.5"), stale), [])


if __name__ == "__main__":
    unittest.main()
