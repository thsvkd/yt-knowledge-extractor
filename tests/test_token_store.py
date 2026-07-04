"""token_store 저장/로드/전환 정리 특성화 테스트.

flet shared_preferences(async)를 dict 백엔드 가짜 객체로 대체한다.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from yke import token_store


class FakePrefs:
    """shared_preferences 의 async 인터페이스를 흉내내는 dict 백엔드."""

    def __init__(self) -> None:
        self.d: dict[str, object] = {}

    async def get(self, key):
        return self.d.get(key)

    async def set(self, key, value):
        self.d[key] = value
        return True

    async def remove(self, key):
        self.d.pop(key, None)
        return True


class TestTokenStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.prefs = FakePrefs()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _await(coro):
        return asyncio.run(coro)

    def test_save_to_file_then_load(self) -> None:
        p = Path(self.tmp.name) / "t.json"
        loc = self._await(token_store.save(self.prefs, "sk-ant-oat01-X", str(p)))
        self.assertIn("파일", loc)
        self.assertTrue(p.exists())
        token, _location = self._await(token_store.load(self.prefs))
        self.assertEqual(token, "sk-ant-oat01-X")

    def test_save_to_app_storage_then_load(self) -> None:
        loc = self._await(token_store.save(self.prefs, "TOK", None))
        self.assertEqual(loc, "앱 저장소")
        token, _location = self._await(token_store.load(self.prefs))
        self.assertEqual(token, "TOK")

    def test_switch_file_to_appstorage_removes_orphan(self) -> None:
        p = Path(self.tmp.name) / "t.json"
        self._await(token_store.save(self.prefs, "TOK1", str(p)))
        self.assertTrue(p.exists())
        # 앱 저장소로 전환 → 이전 평문 파일이 정리되어야 한다(고아 방지).
        self._await(token_store.save(self.prefs, "TOK2", None))
        self.assertFalse(p.exists())
        token, _location = self._await(token_store.load(self.prefs))
        self.assertEqual(token, "TOK2")

    def test_save_same_file_twice_keeps_file(self) -> None:
        p = Path(self.tmp.name) / "t.json"
        self._await(token_store.save(self.prefs, "A", str(p)))
        self._await(token_store.save(self.prefs, "B", str(p)))  # 같은 파일 → 삭제 안 함
        self.assertTrue(p.exists())
        token, _location = self._await(token_store.load(self.prefs))
        self.assertEqual(token, "B")

    def test_clear_removes_file_and_keys(self) -> None:
        p = Path(self.tmp.name) / "t.json"
        self._await(token_store.save(self.prefs, "TOK", str(p)))
        self._await(token_store.clear(self.prefs))
        self.assertFalse(p.exists())
        token, _location = self._await(token_store.load(self.prefs))
        self.assertEqual(token, "")


if __name__ == "__main__":
    unittest.main()
