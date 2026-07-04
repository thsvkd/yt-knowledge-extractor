"""stage2 자막 파싱/롤업 축소 특성화 테스트."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from yke.models import Segment
from yke.pipeline.stage2_subtitles import (
    _collapse_rollup,
    find_subtitle_file,
    parse_vtt,
)

_VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
안녕하세요 <c>여러분</c>

00:00:04.000 --> 00:00:07.500
오늘은 커피 원두에 대해
이야기해 보겠습니다
"""


class TestParseVtt(unittest.TestCase):
    def _write(self, text: str) -> Path:
        p = Path(tempfile.mkdtemp()) / "audio.ko.vtt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_basic_cues(self):
        segs = parse_vtt(self._write(_VTT))
        self.assertEqual(len(segs), 2)

    def test_tags_stripped(self):
        segs = parse_vtt(self._write(_VTT))
        self.assertEqual(segs[0].text, "안녕하세요 여러분")

    def test_multiline_joined_and_timestamps(self):
        segs = parse_vtt(self._write(_VTT))
        self.assertEqual(segs[1].text, "오늘은 커피 원두에 대해 이야기해 보겠습니다")
        self.assertEqual(segs[1].start, 4.0)
        self.assertAlmostEqual(segs[1].end, 7.5)


class TestCollapseRollup(unittest.TestCase):
    def test_sliding_window_dedup(self):
        segs = [
            Segment(start=0, end=1, text="a b c"),
            Segment(start=1, end=2, text="b c d"),
            Segment(start=2, end=3, text="c d e"),
        ]
        out = _collapse_rollup(segs, group_words=100)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].text, "a b c d e")

    def test_regrouping_by_word_count(self):
        segs = [Segment(start=float(i), end=float(i), text=f"w{i}") for i in range(10)]
        out = _collapse_rollup(segs, group_words=4)
        self.assertEqual(len(out), 3)  # 10단어 -> 4+4+2
        self.assertEqual(out[0].text, "w0 w1 w2 w3")
        self.assertEqual(out[0].start, 0.0)

    def test_parse_vtt_collapse_flag(self):
        vtt = (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:01.000\na b c\n\n"
            "00:00:01.000 --> 00:00:02.000\nb c d\n"
        )
        p = Path(tempfile.mkdtemp()) / "audio.ko.vtt"
        p.write_text(vtt, encoding="utf-8")
        raw = parse_vtt(p, collapse_rollup=False)
        col = parse_vtt(p, collapse_rollup=True)
        self.assertEqual(len(raw), 2)
        self.assertEqual(" ".join(s.text for s in col), "a b c d")


class TestFindSubtitleFile(unittest.TestCase):
    class _VP:
        def __init__(self, root):
            self.root = root

    def test_returns_single_vtt(self):
        d = Path(tempfile.mkdtemp())
        (d / "audio.ko.vtt").write_text("x", encoding="utf-8")
        self.assertEqual(find_subtitle_file(self._VP(d)).name, "audio.ko.vtt")

    def test_none_when_absent(self):
        self.assertIsNone(find_subtitle_file(self._VP(Path(tempfile.mkdtemp()))))


if __name__ == "__main__":
    unittest.main()
