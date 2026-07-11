"""utils 특성화 테스트 (결정론적 순수 로직)."""

from __future__ import annotations

import unittest

from yke.utils import fmt_ts, parse_json_array, ts_to_seconds


class TestFmtTs(unittest.TestCase):
    def test_seconds_to_mmss(self):
        self.assertEqual(fmt_ts(0), "00:00")
        self.assertEqual(fmt_ts(75), "01:15")

    def test_over_an_hour(self):
        self.assertEqual(fmt_ts(3661), "1:01:01")

    def test_none_is_zero(self):
        self.assertEqual(fmt_ts(None), "00:00")


class TestTsToSeconds(unittest.TestCase):
    def test_mmss(self):
        self.assertEqual(ts_to_seconds("01:15"), 75)

    def test_hmmss(self):
        self.assertEqual(ts_to_seconds("1:01:01"), 3661)

    def test_roundtrip_with_fmt(self):
        self.assertEqual(ts_to_seconds(fmt_ts(529)), 529)

    def test_malformed_is_zero(self):
        self.assertEqual(ts_to_seconds("abc"), 0)


class TestParseJsonArray(unittest.TestCase):
    def test_plain_array(self):
        self.assertEqual(parse_json_array('[{"a": 1}]'), [{"a": 1}])

    def test_prose_and_code_fence(self):
        self.assertEqual(parse_json_array("sure:\n```json\n[1, 2, 3]\n```"), [1, 2, 3])

    def test_object_not_array_returns_empty(self):
        self.assertEqual(parse_json_array('{"a": 1}'), [])

    def test_garbage_returns_empty(self):
        self.assertEqual(parse_json_array("not json at all"), [])

    def test_empty_returns_empty(self):
        self.assertEqual(parse_json_array(""), [])


if __name__ == "__main__":
    unittest.main()
