"""updater 순수 로직 특성화 테스트(네트워크 없이).

버전 비교·에셋 선택·응답 파싱·SHA256·변형 판별·사이드카 생성만 검증한다.
end-to-end(실제 GitHub 다운로드/폴더 스왑)는 public 레포 + 릴리스가 있어야 가능하다.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from yke import updater


class TestVersion(unittest.TestCase):
    def test_parse_version_strips_v(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2.3"), (1, 2, 3))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("v0.2.0", "0.1.0"))
        self.assertTrue(updater.is_newer("v0.1.1", "0.1.0"))
        self.assertFalse(updater.is_newer("v0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer("v0.1.0", "0.2.0"))

    def test_asset_name(self):
        self.assertEqual(updater.asset_name("cpu", "windows"), "yke-cpu-windows.zip")
        self.assertEqual(updater.asset_name("gpu", "linux"), "yke-gpu-linux.zip")

    def test_double_digit_minor_compares_numerically(self):
        self.assertTrue(updater.is_newer("1.10.0", "1.9.0"))
        self.assertFalse(updater.is_newer("1.9.0", "1.10.0"))

    def test_prerelease_is_not_greater_than_final(self):
        # '1.2.3-rc1' 이 최종 '1.2.3' 보다 크게 정렬되면 안 된다.
        self.assertEqual(updater.parse_version("v1.2.3-rc1"), (1, 2, 3))
        self.assertEqual(updater.parse_version("1.2.3+build5"), (1, 2, 3))
        self.assertFalse(updater.is_newer("1.2.3-rc1", "1.2.3"))


class TestDetectVariantTarget(unittest.TestCase):
    def test_parses_folder_name(self):
        self.assertEqual(
            updater.detect_variant_target(Path("C:/apps/yke-gpu-windows")), ("gpu", "windows")
        )
        self.assertEqual(
            updater.detect_variant_target(Path("/opt/yke-cpu-linux")), ("cpu", "linux")
        )

    def test_falls_back_when_unrecognized(self):
        variant, target = updater.detect_variant_target(Path("/some/dev/checkout"))
        self.assertEqual(variant, "cpu")
        self.assertIn(target, ("windows", "macos", "linux"))


class TestParseLatest(unittest.TestCase):
    def _data(self, tag="v0.2.0"):
        return {
            "tag_name": tag,
            "body": "릴리스 노트",
            "assets": [
                {
                    "name": "yke-cpu-windows.zip",
                    "browser_download_url": "https://example.com/yke-cpu-windows.zip",
                    "digest": "sha256:deadbeef",
                },
                {
                    "name": "yke-gpu-windows.zip",
                    "browser_download_url": "https://example.com/yke-gpu-windows.zip",
                    "digest": "sha256:cafef00d",
                },
            ],
        }

    def test_returns_release_when_newer_and_asset_matches(self):
        rel = updater.parse_latest(self._data(), "0.1.0", "cpu", "windows")
        self.assertIsNotNone(rel)
        self.assertEqual(rel.version, "0.2.0")
        self.assertEqual(rel.asset_name, "yke-cpu-windows.zip")
        self.assertEqual(rel.sha256, "deadbeef")
        self.assertTrue(rel.asset_url.endswith("yke-cpu-windows.zip"))

    def test_picks_gpu_asset_for_gpu_variant(self):
        rel = updater.parse_latest(self._data(), "0.1.0", "gpu", "windows")
        self.assertEqual(rel.asset_name, "yke-gpu-windows.zip")
        self.assertEqual(rel.sha256, "cafef00d")

    def test_none_when_not_newer(self):
        self.assertIsNone(updater.parse_latest(self._data("v0.1.0"), "0.1.0", "cpu", "windows"))

    def test_none_when_no_matching_asset(self):
        # 리눅스 에셋이 없음
        self.assertIsNone(updater.parse_latest(self._data(), "0.1.0", "cpu", "linux"))

    def test_sha256_absent_when_no_digest(self):
        data = self._data()
        del data["assets"][0]["digest"]
        rel = updater.parse_latest(data, "0.1.0", "cpu", "windows")
        self.assertIsNone(rel.sha256)


class TestSha256(unittest.TestCase):
    def test_matches_hashlib(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.bin"
            p.write_bytes(b"hello yke")
            self.assertEqual(updater.sha256_of(p), hashlib.sha256(b"hello yke").hexdigest())


class TestSidecar(unittest.TestCase):
    def test_writes_script_with_pid_and_paths(self):
        with tempfile.TemporaryDirectory() as d:
            path = updater.write_sidecar(
                d,
                pid=4321,
                install_dir=Path(d) / "yke-cpu-windows",
                new_bundle_dir=Path(d) / "staging" / "yke-cpu-windows",
                app_exe="yt-knowledge-extractor.exe",
            )
            self.assertTrue(path.exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("4321", text)
            self.assertIn("yt-knowledge-extractor.exe", text)
            # 백업 경로(롤백용)가 포함된다.
            self.assertIn("yke-cpu-windows_backup", text)
            expected_suffix = ".bat" if sys.platform == "win32" else ".sh"
            self.assertTrue(path.name.endswith(expected_suffix))

    def test_win_sidecar_hardening(self):
        if sys.platform != "win32":
            self.skipTest("Windows 전용 사이드카")
        with tempfile.TemporaryDirectory() as d:
            path = updater.write_sidecar(
                d,
                pid=1,
                install_dir=Path(d) / "yke-cpu-windows",
                new_bundle_dir=Path(d) / "staging" / "yke-cpu-windows",
                app_exe="yt-knowledge-extractor.exe",
            )
            text = path.read_text(encoding="utf-8")
            # 콘솔 없이도 슬립되는 ping 사용(timeout 은 busy-loop 위험).
            self.assertIn("ping", text)
            self.assertNotIn("timeout /t", text)
            # 롤백 전 부분 install 을 지운다.
            self.assertIn("rmdir", text)


if __name__ == "__main__":
    unittest.main()
