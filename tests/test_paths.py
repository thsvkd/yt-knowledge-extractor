"""영상 폴더 이름(제목 기반) 레이아웃 검증.

폴더는 ``<영상 제목> [<영상ID>]`` 로 만들되, 제목을 모를 때나 예전 버전이 만든
``<영상ID>`` 폴더가 있을 때도 같은 영상의 산출물을 잃지 않아야 한다.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yke.paths import VideoPaths, find_video_dir, sanitize_folder_name, video_dir_name


class TestSanitizeFolderName(unittest.TestCase):
    def test_keeps_korean_and_spaces(self) -> None:
        self.assertEqual(
            sanitize_folder_name("유튜브 알고리즘 완전정복"), "유튜브 알고리즘 완전정복"
        )

    def test_replaces_invalid_chars(self) -> None:
        # 윈도우 금지 문자(\/:*?"<>|)는 공백으로 바뀌고 연속 공백은 하나로 접힌다.
        self.assertEqual(sanitize_folder_name('a/b:c*d?e"f<g>h|i'), "a b c d e f g h i")

    def test_strips_trailing_dots_and_spaces(self) -> None:
        # 윈도우는 이름 끝의 점·공백을 저장하지 못한다.
        self.assertEqual(sanitize_folder_name("제목입니다... "), "제목입니다")

    def test_truncates_long_titles(self) -> None:
        self.assertEqual(len(sanitize_folder_name("가" * 200)), 80)

    def test_reserved_device_names_are_escaped(self) -> None:
        self.assertEqual(sanitize_folder_name("CON"), "_CON")
        self.assertEqual(sanitize_folder_name("nul.txt"), "_nul.txt")

    def test_empty_when_nothing_usable(self) -> None:
        self.assertEqual(sanitize_folder_name(None), "")
        self.assertEqual(sanitize_folder_name("   "), "")
        self.assertEqual(sanitize_folder_name("///"), "")


class TestVideoDirName(unittest.TestCase):
    def test_title_with_id_suffix(self) -> None:
        self.assertEqual(video_dir_name("dQw4w9WgXcQ", "제목"), "제목 [dQw4w9WgXcQ]")

    def test_falls_back_to_id_without_title(self) -> None:
        self.assertEqual(video_dir_name("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(video_dir_name("dQw4w9WgXcQ", "///"), "dQw4w9WgXcQ")


class TestVideoPaths(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_title_dir(self) -> None:
        vp = VideoPaths(self.data, "abc12345678", "유튜브 알고리즘")
        self.assertEqual(vp.root.name, "유튜브 알고리즘 [abc12345678]")
        self.assertTrue(vp.root.is_dir())

    def test_same_title_different_video_gets_own_dir(self) -> None:
        a = VideoPaths(self.data, "aaaaaaaaaaa", "같은 제목")
        b = VideoPaths(self.data, "bbbbbbbbbbb", "같은 제목")
        self.assertNotEqual(a.root, b.root)
        self.assertTrue(a.root.is_dir() and b.root.is_dir())

    def test_finds_existing_dir_without_title(self) -> None:
        # 제목을 모르는 캐시 조회에서도 ID 접미사로 기존 폴더를 찾는다.
        made = VideoPaths(self.data, "abc12345678", "제목")
        (made.root / "meta.json").write_text("{}", encoding="utf-8")
        again = VideoPaths(self.data, "abc12345678", create=False)
        self.assertEqual(again.root, made.root)
        self.assertTrue(again.meta.exists())

    def test_create_false_does_not_make_dir(self) -> None:
        vp = VideoPaths(self.data, "abc12345678", create=False)
        self.assertFalse(vp.root.exists())

    def test_legacy_id_dir_is_reused_and_renamed(self) -> None:
        # 예전 버전이 만든 <영상ID> 폴더는 그대로 인식하고, 제목을 알게 되면 옮긴다.
        legacy = self.data / "abc12345678"
        legacy.mkdir(parents=True)
        (legacy / "transcript.raw.json").write_text("[]", encoding="utf-8")

        found = VideoPaths(self.data, "abc12345678", create=False)
        self.assertEqual(found.root, legacy)

        moved = VideoPaths(self.data, "abc12345678", "제목")
        self.assertEqual(moved.root.name, "제목 [abc12345678]")
        self.assertTrue(moved.transcript_raw.exists())  # 캐시가 따라 옮겨졌다
        self.assertFalse(legacy.exists())

    def test_renames_when_title_changes(self) -> None:
        old = VideoPaths(self.data, "abc12345678", "예전 제목")
        (old.root / "meta.json").write_text("{}", encoding="utf-8")
        new = VideoPaths(self.data, "abc12345678", "새 제목")
        self.assertEqual(new.root.name, "새 제목 [abc12345678]")
        self.assertTrue(new.meta.exists())
        self.assertFalse(old.root.exists())

    def test_prefers_dir_that_holds_outputs(self) -> None:
        # 같은 영상의 폴더가 여럿이면(제목 변경 후 이동 실패 등) 산출물이 있는 쪽을 쓴다.
        with_data = self.data / "예전 제목 [abc12345678]"
        with_data.mkdir(parents=True)
        (with_data / "meta.json").write_text("{}", encoding="utf-8")
        (self.data / "가나다 [abc12345678]").mkdir()  # 이름은 앞서지만 비어 있다

        self.assertEqual(find_video_dir(self.data, "abc12345678"), with_data)
        # 옮길 이름이 이미 (빈 폴더로) 있으면 이동하지 않고 산출물이 있는 폴더를 그대로 쓴다.
        vp = VideoPaths(self.data, "abc12345678", "가나다")
        self.assertEqual(vp.root, with_data)

    def test_find_video_dir_returns_none_for_missing(self) -> None:
        self.assertIsNone(find_video_dir(self.data, "abc12345678"))
        self.data.mkdir(parents=True)
        self.assertIsNone(find_video_dir(self.data, "abc12345678"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
