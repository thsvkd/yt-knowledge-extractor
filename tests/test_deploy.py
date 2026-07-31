"""릴리스 스크립트(scripts/deploy.py)의 순수 함수 검증.

OS 별로 따로 빌드해 **같은 태그에 에셋을 추가**하는 흐름이 생기면서, 예전의 "이전 릴리스와
버전이 같으면 중단" 가드가 두 번째 플랫폼 실행을 정상적으로도 막게 됐다. 그래서 판정
근거를 셋으로 나눴다 — (a) 그 릴리스에 내 채널의 releases.<channel>.json 이 이미 있는가,
(b) 그 태그가 최신 릴리스인가, (c) 태그가 가리키는 커밋이 지금 HEAD 와 같은가.

(a) 하나만으로는 **아직 한 번도 릴리스된 적 없는 채널**(첫 macOS 배포)에 가드가 전혀 걸리지
않는다는 점이 핵심이다. 이 판정이 틀리면 (1) 버전 올리는 걸 잊어도 그냥 배포되거나
(2) 낡은 체크아웃이 과거 릴리스에 에셋을 붙이거나 (3) 두 번째 플랫폼이 첫 플랫폼의 릴리스
노트를 날린다. 셋 다 gh 를 실제로 호출해 보기 전에는 드러나지 않으므로, 결정 로직만 순수
함수로 떼어내 여기서 고정한다.

gh·네트워크는 건드리지 않는다(_release_assets 는 이 테스트의 대상이 아니다).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import deploy  # noqa: E402 - scripts/ 를 sys.path 에 넣은 뒤에만 import 가능
import platform_spec  # noqa: E402

_TAG = "v0.1.5"
_VERSION = "0.1.5"

# 정상적인 두 번째 OS 실행은 첫 OS 가 만든 태그와 **같은 커밋**에 서 있다.
_SHA = "0a29f0ee729d925fff946cbc598de1ecb350b90a"
_OTHER_SHA = "194746312155677793efe64a6229ecab128ddd25"

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


class TestPlanRelease(unittest.TestCase):
    """결정표. 인자 조합 → (mode, generate_notes, error) 를 그대로 고정한다."""

    def test_no_release_creates_and_generates_notes(self) -> None:
        plan = deploy.plan_release(
            tag=_TAG, prev_tag="v0.1.4", existing_assets=None,
            releases_json="releases.win.json", force=False,
            tag_commit=None, head_commit=None,
        )
        self.assertIsNone(plan.error)
        self.assertEqual(plan.mode, "create")
        self.assertTrue(plan.generate_notes)

    def test_no_release_but_same_tag_is_rejected(self) -> None:
        """방어용 — 릴리스가 없는데 최신 릴리스 태그가 같을 수는 없다(gh 조회가 어긋난 경우)."""
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=None,
            releases_json="releases.win.json", force=False,
            tag_commit=None, head_commit=None,
        )
        self.assertIsNotNone(plan.error)
        self.assertIn("[project].version", plan.error)

    def test_first_release_ever(self) -> None:
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=None, existing_assets=None,
            releases_json="releases.osx.json", force=False,
            tag_commit=None, head_commit=None,
        )
        self.assertIsNone(plan.error)
        self.assertEqual(plan.mode, "create")
        self.assertTrue(plan.generate_notes)

    def test_other_platform_release_appends_without_notes(self) -> None:
        """다른 OS 가 **같은 커밋에서** 먼저 만든 릴리스 → 노트는 손대지 않고 내 에셋만 얹는다."""
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=_WIN_FILES,
            releases_json="releases.osx.json", force=False,
            tag_commit=_SHA, head_commit=_SHA,
        )
        self.assertIsNone(plan.error)
        self.assertEqual(plan.mode, "append")
        self.assertFalse(plan.generate_notes)

    def test_own_platform_already_uploaded_is_rejected(self) -> None:
        """내 채널의 releases json 이 이미 있으면 = 버전을 안 올린 것이다."""
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=_WIN_FILES,
            releases_json="releases.win.json", force=False,
            tag_commit=_SHA, head_commit=_SHA,
        )
        self.assertIsNotNone(plan.error)
        self.assertIn("releases.win.json", plan.error)
        self.assertIn("--force", plan.error)

    def test_force_overrides_already_uploaded(self) -> None:
        """중단된 업로드를 다시 돌릴 때만 쓰는 탈출구. 그래도 노트는 재생성하지 않는다."""
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=_WIN_FILES,
            releases_json="releases.win.json", force=True,
            tag_commit=_SHA, head_commit=_SHA,
        )
        self.assertIsNone(plan.error)
        self.assertEqual(plan.mode, "append")
        self.assertFalse(plan.generate_notes)

    # -- 아직 릴리스된 적 없는 채널(첫 macOS 배포)에도 가드가 걸리는지 ------------------
    def test_never_released_channel_still_checks_commit(self) -> None:
        """핵심 회귀 방지.

        releases.osx.json 이 한 번도 올라간 적 없으면 "이미 배포함" 판정은 절대 걸리지
        않는다. 그 상태에서 버전을 안 올린 채(= 태그가 이미 있는 상태) HEAD 에서 빌드하면,
        v0.1.5 라는 이름으로 v0.1.5 가 아닌 코드가 macOS 사용자에게 배포된다.
        """
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=_WIN_FILES,
            releases_json="releases.osx.json", force=False,
            tag_commit=_OTHER_SHA, head_commit=_SHA,
        )
        self.assertIsNotNone(plan.error)
        self.assertIn("HEAD", plan.error)
        self.assertIn("--force", plan.error)

    def test_force_overrides_commit_mismatch(self) -> None:
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=_WIN_FILES,
            releases_json="releases.osx.json", force=True,
            tag_commit=_OTHER_SHA, head_commit=_SHA,
        )
        self.assertIsNone(plan.error)
        self.assertEqual(plan.mode, "append")

    def test_unknown_commit_is_rejected(self) -> None:
        """대조가 불가능하면 통과시키지 않는다 — 모르는 채 올리는 게 막으려는 사고다."""
        plan = deploy.plan_release(
            tag=_TAG, prev_tag=_TAG, existing_assets=_WIN_FILES,
            releases_json="releases.osx.json", force=False,
            tag_commit=None, head_commit=_SHA,
        )
        self.assertIsNotNone(plan.error)
        self.assertIn("--force", plan.error)

    # -- 낡은 체크아웃(과거 태그에 에셋을 붙이려는 경우) --------------------------------
    def test_stale_checkout_older_tag_is_rejected(self) -> None:
        """tag(v0.1.5) 가 최신 릴리스(v0.1.6)가 아니면 중단.

        예전에는 `gh release create <이미 있는 태그>` 가 실패해 사람이 알아챘는데, append
        흐름이 생기면서 조용히 성공하게 됐다. 그러면 낡은 에셋이 과거 릴리스에 붙고 정작
        latest 에는 그 OS 설치기가 영영 없다.
        """
        plan = deploy.plan_release(
            tag=_TAG, prev_tag="v0.1.6", existing_assets=_WIN_FILES,
            releases_json="releases.osx.json", force=False,
            tag_commit=_SHA, head_commit=_SHA,
        )
        self.assertIsNotNone(plan.error)
        self.assertIn("v0.1.6", plan.error)
        self.assertIn("--force", plan.error)

    def test_force_overrides_stale_checkout(self) -> None:
        plan = deploy.plan_release(
            tag=_TAG, prev_tag="v0.1.6", existing_assets=_WIN_FILES,
            releases_json="releases.osx.json", force=True,
            tag_commit=_SHA, head_commit=_SHA,
        )
        self.assertIsNone(plan.error)
        self.assertEqual(plan.mode, "append")


class _AssetsCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def touch(self, *names: str) -> None:
        for name in names:
            (self.out_dir / name).write_text("x", encoding="utf-8")

    def names(self, paths: list[Path]) -> list[str]:
        return [p.name for p in paths]


class TestCollectAssets(_AssetsCase):
    def test_windows_happy_path(self) -> None:
        self.touch(*_WIN_FILES)
        assets = deploy.collect_assets(
            self.out_dir, platform_spec.spec_for("windows"), _VERSION
        )
        self.assertEqual(
            self.names(assets),
            [
                "YtKnowledgeExtractor-win-Setup.exe",
                "YtKnowledgeExtractor-0.1.5-full.nupkg",
                "YtKnowledgeExtractor-0.1.5-delta.nupkg",
                "releases.win.json",
            ],
        )

    def test_macos_happy_path(self) -> None:
        self.touch(*_OSX_FILES)
        assets = deploy.collect_assets(
            self.out_dir, platform_spec.spec_for("macos"), _VERSION
        )
        self.assertEqual(
            self.names(assets),
            [
                "YtKnowledgeExtractor-osx-Setup.pkg",
                "YtKnowledgeExtractor-0.1.5-osx-full.nupkg",
                "YtKnowledgeExtractor-0.1.5-osx-delta.nupkg",
                "releases.osx.json",
            ],
        )

    def test_delta_is_optional(self) -> None:
        """첫 릴리스에는 델타가 없다 — 그걸로 실패하면 첫 배포가 막힌다."""
        self.touch(
            "YtKnowledgeExtractor-osx-Setup.pkg",
            "YtKnowledgeExtractor-0.1.5-osx-full.nupkg",
            "releases.osx.json",
        )
        assets = deploy.collect_assets(
            self.out_dir, platform_spec.spec_for("macos"), _VERSION
        )
        self.assertEqual(
            self.names(assets),
            [
                "YtKnowledgeExtractor-osx-Setup.pkg",
                "YtKnowledgeExtractor-0.1.5-osx-full.nupkg",
                "releases.osx.json",
            ],
        )

    def test_missing_setup_raises(self) -> None:
        self.touch("YtKnowledgeExtractor-0.1.5-full.nupkg", "releases.win.json")
        with self.assertRaises(ValueError):
            deploy.collect_assets(self.out_dir, platform_spec.spec_for("windows"), _VERSION)

    def test_missing_full_nupkg_raises(self) -> None:
        # 이번 버전의 full 이 없으면 업데이트가 성립하지 않는다(이전 버전 nupkg 가 폴더에
        # 남아 있어도 통과시키면 안 된다).
        self.touch(
            "YtKnowledgeExtractor-win-Setup.exe",
            "YtKnowledgeExtractor-0.1.4-full.nupkg",
            "releases.win.json",
        )
        with self.assertRaises(ValueError):
            deploy.collect_assets(self.out_dir, platform_spec.spec_for("windows"), _VERSION)

    def test_missing_releases_json_raises(self) -> None:
        # releases.<channel>.json 이 빠지면 설치는 되는데 자동 업데이트만 조용히 죽는다.
        self.touch(
            "YtKnowledgeExtractor-win-Setup.exe", "YtKnowledgeExtractor-0.1.5-full.nupkg"
        )
        with self.assertRaises(ValueError):
            deploy.collect_assets(self.out_dir, platform_spec.spec_for("windows"), _VERSION)


class TestCollectAssetsChannelIsolation(_AssetsCase):
    """dist/velopack 을 두 채널이 공유해도 서로의 파일을 올리지 않는지.

    실제 macOS/Windows 없이 상대 플랫폼 경로까지 검증하는 핵심 테스트다 — 이게 깨지면
    두 번째 플랫폼 업로드가 첫 플랫폼 에셋을 덮어쓴다.
    """

    def setUp(self) -> None:
        super().setUp()
        self.touch(*_WIN_FILES, *_OSX_FILES)

    def test_macos_ignores_windows_files(self) -> None:
        assets = self.names(
            deploy.collect_assets(self.out_dir, platform_spec.spec_for("macos"), _VERSION)
        )
        self.assertEqual(
            assets,
            [
                "YtKnowledgeExtractor-osx-Setup.pkg",
                "YtKnowledgeExtractor-0.1.5-osx-full.nupkg",
                "YtKnowledgeExtractor-0.1.5-osx-delta.nupkg",
                "releases.osx.json",
            ],
        )

    def test_windows_ignores_macos_files(self) -> None:
        assets = self.names(
            deploy.collect_assets(self.out_dir, platform_spec.spec_for("windows"), _VERSION)
        )
        self.assertEqual(
            assets,
            [
                "YtKnowledgeExtractor-win-Setup.exe",
                "YtKnowledgeExtractor-0.1.5-full.nupkg",
                "YtKnowledgeExtractor-0.1.5-delta.nupkg",
                "releases.win.json",
            ],
        )


class TestCheckWorktreeClean(unittest.TestCase):
    """미커밋 변경이 있는 채로 배포하면 '어느 커밋에도 없는 코드'가 그 버전으로 나간다.

    plan_release 의 태그↔HEAD 대조는 릴리스가 **이미 있을 때만** 도는 가드라, 첫 릴리스
    (create) 경로는 이 검사가 없으면 무방비다.
    """

    def test_clean_worktree_passes(self) -> None:
        self.assertIsNone(deploy.check_worktree_clean("", force=False))

    def test_whitespace_only_is_clean(self) -> None:
        self.assertIsNone(deploy.check_worktree_clean("\n\n", force=False))

    def test_modified_file_is_rejected(self) -> None:
        error = deploy.check_worktree_clean(" M src/yke/gui.py\n", force=False)
        self.assertIsNotNone(error)
        self.assertIn("src/yke/gui.py", error)

    def test_untracked_file_is_rejected(self) -> None:
        """추적되지 않는 파일도 flet 이 src/ 를 복사할 때 번들에 들어간다."""
        error = deploy.check_worktree_clean("?? src/yke/secret_patch.py\n", force=False)
        self.assertIsNotNone(error)

    def test_unknown_status_is_rejected(self) -> None:
        """git status 조회 실패를 '깨끗함'으로 오해하면 가드가 통째로 무력해진다."""
        self.assertIsNotNone(deploy.check_worktree_clean(None, force=False))

    def test_force_overrides(self) -> None:
        self.assertIsNone(deploy.check_worktree_clean(" M src/yke/gui.py\n", force=True))
        self.assertIsNone(deploy.check_worktree_clean(None, force=True))


class TestCheckHeadPushed(unittest.TestCase):
    """push 하지 않은 채 배포하면 태그가 방금 빌드한 커밋을 가리킬 수 없다."""

    def test_pushed_head_passes(self) -> None:
        self.assertIsNone(
            deploy.check_head_pushed("abc1234", remote_has_head=True, force=False)
        )

    def test_unpushed_head_is_rejected(self) -> None:
        error = deploy.check_head_pushed("abc1234def", remote_has_head=False, force=False)
        self.assertIsNotNone(error)
        self.assertIn("push", error)

    def test_unknown_head_is_rejected(self) -> None:
        self.assertIsNotNone(
            deploy.check_head_pushed(None, remote_has_head=False, force=False)
        )

    def test_force_overrides(self) -> None:
        self.assertIsNone(
            deploy.check_head_pushed("abc1234", remote_has_head=False, force=True)
        )


if __name__ == "__main__":
    unittest.main()
