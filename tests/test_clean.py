"""stage4 텍스트 정제 특성화 테스트."""

from __future__ import annotations

import unittest

from yke.models import Segment
from yke.pipeline.stage4_clean import clean_segments


class TestCleanSegments(unittest.TestCase):
    def test_whitespace_normalized(self):
        out = clean_segments([Segment(start=0, end=1, text="  a   b  ")])
        self.assertEqual(out[0].text, "a b")

    def test_drops_empty_and_single_char(self):
        out = clean_segments(
            [
                Segment(start=0, end=1, text="   "),
                Segment(start=1, end=2, text="x"),
                Segment(start=2, end=3, text="ok"),
            ]
        )
        self.assertEqual([s.text for s in out], ["ok"])

    def test_preserves_timestamps(self):
        out = clean_segments([Segment(start=5.5, end=6.5, text="hello world")])
        self.assertEqual((out[0].start, out[0].end), (5.5, 6.5))


if __name__ == "__main__":
    unittest.main()
