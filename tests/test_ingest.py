"""stage1 자막 언어 선택(_pick_lang) 및 다운로드 재시도(_download) 특성화 테스트."""

from __future__ import annotations

import unittest
from unittest import mock

import yt_dlp

from yke.pipeline import stage1_ingest
from yke.pipeline.stage1_ingest import _download, _pick_lang


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


class TestDownloadRetry(unittest.TestCase):
    """유튜브의 간헐적 403/429/5xx 를 짧게 재시도하는 _download() 검증."""

    def _patched_ydl(self, side_effect):
        ydl = mock.MagicMock()
        ydl.extract_info.side_effect = side_effect
        ydl.__enter__.return_value = ydl
        return mock.patch.object(stage1_ingest.yt_dlp, "YoutubeDL", return_value=ydl)

    def test_succeeds_first_try_without_retry(self):
        with (
            self._patched_ydl([{"id": "v1"}]),
            mock.patch.object(stage1_ingest.time, "sleep") as sleep,
        ):
            result = _download({}, "https://x", log=lambda m: None)
        self.assertEqual(result, {"id": "v1"})
        sleep.assert_not_called()

    def test_retries_on_403_then_succeeds(self):
        err = yt_dlp.utils.DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        logs: list[str] = []
        with (
            self._patched_ydl([err, {"id": "v1"}]),
            mock.patch.object(stage1_ingest.time, "sleep") as sleep,
        ):
            result = _download({}, "https://x", log=logs.append)
        self.assertEqual(result, {"id": "v1"})
        sleep.assert_called_once()
        self.assertTrue(any("재시도" in m for m in logs))

    def test_gives_up_after_max_attempts(self):
        err = yt_dlp.utils.DownloadError("HTTP Error 403: Forbidden")
        with (
            self._patched_ydl([err, err, err]),
            mock.patch.object(stage1_ingest.time, "sleep"),
        ):
            with self.assertRaises(yt_dlp.utils.DownloadError):
                _download({}, "https://x", log=lambda m: None)

    def test_non_retryable_error_raises_immediately(self):
        err = yt_dlp.utils.DownloadError("ERROR: Private video. Sign in if you've been granted access")
        with (
            self._patched_ydl([err, {"id": "should-not-reach"}]),
            mock.patch.object(stage1_ingest.time, "sleep") as sleep,
        ):
            with self.assertRaises(yt_dlp.utils.DownloadError):
                _download({}, "https://x", log=lambda m: None)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
