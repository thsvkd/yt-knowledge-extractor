"""stage5 지식 원자 단위 정규화(_to_unit) 회귀 테스트."""

from __future__ import annotations

import unittest

from yke.pipeline.stage5_extract import _to_unit


class TestToUnit(unittest.TestCase):
    def test_korean_type_mapped(self):
        u = _to_unit(
            {"concept": "c", "statement": "s", "type": "사실", "timestamp": "01:00", "quote_evidence": "q"},
            "v",
        )
        self.assertEqual(u.type, "fact")

    def test_uppercase_type_mapped(self):
        u = _to_unit({"concept": "c", "statement": "s", "type": "Opinion"}, "v")
        self.assertEqual(u.type, "opinion")

    def test_unknown_type_defaults_to_fact(self):
        u = _to_unit({"concept": "c", "statement": "s", "type": "weird"}, "v")
        self.assertEqual(u.type, "fact")

    def test_optional_fields_defaulted_and_source_set(self):
        u = _to_unit({"concept": "c", "statement": "s", "type": "tip"}, "vid9")
        self.assertEqual(u.timestamp, "00:00")
        self.assertEqual(u.quote_evidence, "")
        self.assertEqual(u.source_video_id, "vid9")


if __name__ == "__main__":
    unittest.main()
