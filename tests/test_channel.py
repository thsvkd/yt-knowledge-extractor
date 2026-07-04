"""채널/재생목록 URL 분류·정규화 특성화 테스트(순수 로직)."""

from __future__ import annotations

import unittest

from yke.pipeline.stage1_ingest import _normalize_channel_url
from yke.utils import is_channel_or_playlist_url


class TestIsChannelOrPlaylist(unittest.TestCase):
    def test_individual_videos_are_false(self):
        for url in (
            "https://www.youtube.com/watch?v=8fYXYk42LxU",
            "https://youtu.be/8fYXYk42LxU?t=10",
            "https://youtube.com/shorts/8fYXYk42LxU",
            "https://www.youtube.com/embed/8fYXYk42LxU",
        ):
            self.assertFalse(is_channel_or_playlist_url(url), url)

    def test_channels_are_true(self):
        for url in (
            "https://www.youtube.com/@veritasium",
            "https://www.youtube.com/@veritasium/videos",
            "https://www.youtube.com/channel/UCabc123",
            "https://www.youtube.com/c/Veritasium",
            "https://www.youtube.com/user/1veritasium",
        ):
            self.assertTrue(is_channel_or_playlist_url(url), url)

    def test_playlists_are_true(self):
        self.assertTrue(
            is_channel_or_playlist_url("https://www.youtube.com/playlist?list=PLabc")
        )

    def test_watch_with_list_is_individual(self):
        # 영상+재생목록이 섞이면 개별 영상으로 본다(watch 우선).
        self.assertFalse(
            is_channel_or_playlist_url("https://www.youtube.com/watch?v=abc&list=PLxyz")
        )

    def test_empty_is_false(self):
        self.assertFalse(is_channel_or_playlist_url(""))
        self.assertFalse(is_channel_or_playlist_url("   "))


class TestNormalizeChannelUrl(unittest.TestCase):
    def test_channel_root_gets_videos_tab(self):
        self.assertEqual(
            _normalize_channel_url("https://www.youtube.com/@veritasium"),
            "https://www.youtube.com/@veritasium/videos",
        )
        self.assertEqual(
            _normalize_channel_url("https://www.youtube.com/channel/UCabc/"),
            "https://www.youtube.com/channel/UCabc/videos",
        )

    def test_existing_tab_is_kept(self):
        for url in (
            "https://www.youtube.com/@veritasium/videos",
            "https://www.youtube.com/@veritasium/streams",
            "https://www.youtube.com/@veritasium/shorts",
        ):
            self.assertEqual(_normalize_channel_url(url), url)

    def test_playlist_and_video_are_untouched(self):
        for url in (
            "https://www.youtube.com/playlist?list=PLabc",
            "https://www.youtube.com/watch?v=abc",
        ):
            self.assertEqual(_normalize_channel_url(url), url)

    def test_query_string_is_preserved_and_videos_goes_on_path(self):
        # /videos 는 쿼리 뒤가 아니라 경로에 붙어야 한다.
        self.assertEqual(
            _normalize_channel_url("https://www.youtube.com/@veritasium?sub_confirmation=1"),
            "https://www.youtube.com/@veritasium/videos?sub_confirmation=1",
        )
        self.assertEqual(
            _normalize_channel_url("https://www.youtube.com/channel/UCabc?si=xyz"),
            "https://www.youtube.com/channel/UCabc/videos?si=xyz",
        )

    def test_existing_tab_with_query_is_kept(self):
        self.assertEqual(
            _normalize_channel_url("https://www.youtube.com/@veritasium/videos?foo=bar"),
            "https://www.youtube.com/@veritasium/videos?foo=bar",
        )


if __name__ == "__main__":
    unittest.main()
