"""run.py URL -> video_id 추출 회귀 테스트."""

from __future__ import annotations

import unittest

from yke.run import _video_id_from_url


class TestVideoIdFromUrl(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(
            _video_id_from_url("https://www.youtube.com/watch?v=8fYXYk42LxU"), "8fYXYk42LxU"
        )

    def test_short_url_with_query(self):
        self.assertEqual(_video_id_from_url("https://youtu.be/8fYXYk42LxU?t=10"), "8fYXYk42LxU")

    def test_shorts_url(self):
        self.assertEqual(
            _video_id_from_url("https://youtube.com/shorts/8fYXYk42LxU"), "8fYXYk42LxU"
        )

    def test_none_for_non_youtube(self):
        self.assertIsNone(_video_id_from_url("not a url"))


if __name__ == "__main__":
    unittest.main()
