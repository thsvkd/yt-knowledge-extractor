"""사용자 데이터 경로(src/yke/appdirs.py) 검증.

실측으로 확인한 버그에서 출발한다: Windows 에서 우리 사용자 데이터 경로가 Velopack 의
설치 루트(``%LOCALAPPDATA%\\YtKnowledgeExtractor``)와 **정확히 같았다**. v0.1.3 설치기를
--silent 로 돌리자 그 폴더의 ``gui_settings.json`` 이 사라졌다. 같은 자리에 있던
``gpu-runtime/``(cuBLAS ~900MB)도 업데이트마다 함께 지워지므로, 사용자는 저장 폴더 설정이
초기화되고 GPU 런타임을 매번 다시 받아야 했다.

그래서 vendor 폴더를 한 단계 끼워 Velopack 경로와 갈라놓고, 예전 경로에 남은 데이터를 한
번 옮긴다. 이 테스트가 고정하는 성질은 둘이다.
  1. 새 경로는 Velopack 설치 루트 **안이 아니다**.
  2. 이전은 우리 소유 항목만 옮긴다 — 예전 경로는 설치 루트이기도 해서 current/·packages/·
     Update.exe 를 건드리면 설치본이 깨진다.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yke import appdirs


class TestWindowsPathSeparation(unittest.TestCase):
    def test_new_path_is_not_inside_velopack_root(self) -> None:
        local = r"C:\Users\u\AppData\Local"
        new = appdirs._windows_data_dir("YtKnowledgeExtractor", local)
        legacy = appdirs._windows_legacy_dir("YtKnowledgeExtractor", local)
        self.assertNotEqual(new, legacy)
        # legacy 가 Velopack 설치 루트다. 새 경로가 그 아래면 같은 문제가 반복된다.
        self.assertNotIn(str(legacy), str(new))

    def test_legacy_path_matches_velopack_layout(self) -> None:
        """예전 경로 계산이 실제 Velopack 설치 루트와 같아야 이전 대상이 맞는다."""
        local = r"C:\Users\u\AppData\Local"
        legacy = appdirs._windows_legacy_dir("YtKnowledgeExtractor", local)
        self.assertEqual(legacy, Path(local) / "YtKnowledgeExtractor")

    def test_empty_localappdata_falls_back_to_home(self) -> None:
        """폴백을 바꾸면 기존 사용자의 설정이 사라진 것처럼 보인다 — 동작을 고정한다."""
        self.assertTrue(str(appdirs._windows_data_dir("App", None)))
        self.assertTrue(str(appdirs._windows_data_dir("App", "")))


class TestMigration(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.legacy = root / "YtKnowledgeExtractor"
        self.target = root / "thsvkd" / "YtKnowledgeExtractor"
        # Velopack 설치 루트를 흉내낸다.
        (self.legacy / "current").mkdir(parents=True)
        (self.legacy / "current" / "app.exe").write_text("app")
        (self.legacy / "packages").mkdir()
        (self.legacy / "Update.exe").write_text("updater")
        # 우리 소유 데이터.
        (self.legacy / "gui_settings.json").write_text('{"output_dir": "D:/out"}')
        (self.legacy / "gpu-runtime").mkdir()
        (self.legacy / "gpu-runtime" / "cublas64_12.dll").write_text("dll")

    def test_moves_only_owned_items(self) -> None:
        moved = appdirs.migrate_owned_items(self.legacy, self.target)
        self.assertEqual(sorted(moved), ["gpu-runtime", "gui_settings.json"])
        self.assertEqual(
            (self.target / "gui_settings.json").read_text(), '{"output_dir": "D:/out"}'
        )
        self.assertTrue((self.target / "gpu-runtime" / "cublas64_12.dll").is_file())

    def test_never_touches_velopack_files(self) -> None:
        """이것이 깨지면 이전 코드가 설치본을 부순다 — 가장 중요한 성질이다."""
        appdirs.migrate_owned_items(self.legacy, self.target)
        self.assertTrue((self.legacy / "current" / "app.exe").is_file())
        self.assertTrue((self.legacy / "packages").is_dir())
        self.assertTrue((self.legacy / "Update.exe").is_file())

    def test_does_not_overwrite_newer_data(self) -> None:
        """새 경로에 이미 있으면 그쪽이 최신이다(이전은 한 번이지만 방어한다)."""
        self.target.mkdir(parents=True)
        (self.target / "gui_settings.json").write_text('{"output_dir": "NEW"}')
        moved = appdirs.migrate_owned_items(self.legacy, self.target)
        self.assertNotIn("gui_settings.json", moved)
        self.assertEqual((self.target / "gui_settings.json").read_text(), '{"output_dir": "NEW"}')

    def test_missing_legacy_dir_is_noop(self) -> None:
        missing = self.legacy.parent / "없는폴더"
        self.assertEqual(appdirs.migrate_owned_items(missing, self.target), [])

    def test_same_dir_is_noop(self) -> None:
        """예전 경로 == 새 경로인 플랫폼(macOS 등)에서 자기 자신을 옮기려 하면 안 된다."""
        self.assertEqual(appdirs.migrate_owned_items(self.legacy, self.legacy), [])
        self.assertTrue((self.legacy / "gui_settings.json").is_file())

    def test_nothing_to_move_is_clean(self) -> None:
        (self.legacy / "gui_settings.json").unlink()
        import shutil

        shutil.rmtree(self.legacy / "gpu-runtime")
        self.assertEqual(appdirs.migrate_owned_items(self.legacy, self.target), [])


class TestUserDataDir(unittest.TestCase):
    def test_returns_absolute_path(self) -> None:
        self.assertTrue(appdirs.user_data_dir("YtKnowledgeExtractor").is_absolute())

    def test_app_id_is_last_component(self) -> None:
        self.assertEqual(appdirs.user_data_dir("SomeApp").name, "SomeApp")


if __name__ == "__main__":
    unittest.main()
