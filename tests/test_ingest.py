"""stage1 자막 언어 선택(_pick_lang) 특성화 테스트."""

from __future__ import annotations

import unittest

from yke.pipeline.stage1_ingest import _pick_lang


class TestPickLang(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(_pick_lang({"ko": [], "en": []}, "ko"), "ko")

    def test_prefix_variant(self):
        self.assertEqual(_pick_lang({"ko-KR": [], "en": []}, "ko"), "ko-KR")

    def test_prefers_shortest_variant(self):
        # 'ko' 와 'ko-orig' 가 모두 있으면 정확 일치('ko')를 택한다.
        self.assertEqual(_pick_lang({"ko-orig": [], "ko": []}, "ko"), "ko")

    def test_none_when_language_absent(self):
        self.assertIsNone(_pick_lang({"en": [], "ja": []}, "ko"))

    def test_none_for_empty_table(self):
        self.assertIsNone(_pick_lang(None, "ko"))
        self.assertIsNone(_pick_lang({}, "ko"))


if __name__ == "__main__":
    unittest.main()
