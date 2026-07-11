"""run_pipeline 오케스트레이션 특성화 테스트.

네트워크/LLM 은 목으로 대체하고, 진행 이벤트·취소·실패 격리·단계 게이팅만 검증한다.
"""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yke import run
from yke.config import Config
from yke.models import KnowledgeUnit, Segment


def _seg() -> list[Segment]:
    return [Segment(start=0.0, end=1.0, text="hi")]


def _fake_bt(url, cfg, data_dir, force, *, log=print):
    vid = "v_" + url
    return vid, {"id": vid, "title": url}, _seg()


class TestRunPipeline(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(
            videos=[],
            data_dir=f"{self.tmp.name}/data",
            output_dir=f"{self.tmp.name}/out",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_transcript_stage_collects_and_reports(self) -> None:
        events: list[run.Progress] = []
        with mock.patch.object(run, "build_transcript", side_effect=_fake_bt):
            res = run.run_pipeline(
                ["u1", "u2"], self.cfg, stage="transcript", on_progress=events.append
            )
        self.assertEqual(res.video_count, 2)
        self.assertFalse(res.stopped)
        self.assertEqual(res.failures, [])
        self.assertIsNone(res.wiki_path)
        msgs = [e.message for e in events]
        self.assertTrue(any("트랜스크립트 1 세그먼트" in m for m in msgs))
        # 스피너용 transient 이벤트가 하나 이상 존재한다.
        self.assertTrue(any(e.transient for e in events))
        # 영상별 성공 이벤트(phase=transcript)가 영상 수만큼 나온다.
        transcript_success = [
            e for e in events if e.level == "success" and e.phase == "transcript"
        ]
        self.assertEqual(len(transcript_success), 2)

    def test_failure_is_isolated(self) -> None:
        def bt(url, cfg, data_dir, force, *, log=print):
            if url == "bad":
                raise RuntimeError("boom")
            return "v_ok", {"id": "v_ok"}, _seg()

        events: list[run.Progress] = []
        with mock.patch.object(run, "build_transcript", side_effect=bt):
            res = run.run_pipeline(
                ["bad", "good"], self.cfg, stage="transcript", on_progress=events.append
            )
        self.assertEqual(res.failures, ["bad"])
        self.assertEqual(res.video_count, 1)
        self.assertTrue(any(e.level == "error" for e in events))

    def test_all_failures_raises(self) -> None:
        with mock.patch.object(run, "build_transcript", side_effect=RuntimeError("x")):
            with self.assertRaises(RuntimeError):
                run.run_pipeline(["a"], self.cfg, stage="transcript")

    def test_stop_between_videos(self) -> None:
        processed = {"n": 0}

        def bt(url, cfg, data_dir, force, *, log=print):
            processed["n"] += 1
            return "v_" + url, {"id": "v_" + url}, _seg()

        # 첫 영상 처리 후부터 중단 신호를 준다(루프 상단에서 검사).
        with mock.patch.object(run, "build_transcript", side_effect=bt):
            res = run.run_pipeline(
                ["a", "b", "c"],
                self.cfg,
                stage="transcript",
                should_stop=lambda: processed["n"] >= 1,
            )
        self.assertTrue(res.stopped)
        self.assertEqual(res.video_count, 1)

    def test_channel_expands_and_dedups(self) -> None:
        def fake_expand(url, limit, *, log=print):
            return ["https://youtu.be/a", "https://youtu.be/b"]

        with (
            mock.patch.object(run.stage1_ingest, "expand_source", side_effect=fake_expand),
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
        ):
            # 채널은 a,b 로 확장 + 개별 a → 중복 제거 → a,b (2개)
            res = run.run_pipeline(
                ["https://www.youtube.com/@chan", "https://youtu.be/a"],
                self.cfg,
                stage="transcript",
                channel_limit=2,
            )
        self.assertEqual(res.video_count, 2)
        self.assertFalse(res.stopped)

    def test_channel_expansion_failure_isolated(self) -> None:
        def fake_expand(url, limit, *, log=print):
            raise RuntimeError("채널 없음")

        with (
            mock.patch.object(run.stage1_ingest, "expand_source", side_effect=fake_expand),
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
        ):
            res = run.run_pipeline(
                ["https://www.youtube.com/@bad", "https://youtu.be/ok"],
                self.cfg,
                stage="transcript",
            )
        self.assertIn("https://www.youtube.com/@bad", res.failures)
        self.assertEqual(res.video_count, 1)

    def test_extract_stage_uses_llm_units(self) -> None:
        units = [
            KnowledgeUnit(
                concept="c",
                statement="s",
                type="fact",
                source_video_id="vid",
                timestamp="00:01",
                quote_evidence="q",
            )
        ]
        with (
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
            mock.patch("yke.llm.claude_client.ClaudeClient"),
            mock.patch.object(run.stage5_extract, "extract_units", return_value=units),
        ):
            res = run.run_pipeline(["u"], self.cfg, stage="extract")
        self.assertEqual(res.video_count, 1)
        self.assertEqual(res.unit_count, 1)
        self.assertEqual(res.concept_count, 0)
        self.assertIsNone(res.wiki_path)


class TestCaptionValidation(unittest.TestCase):
    """자막 완전성 검증(_caption_coverage / _caption_is_usable) 단위 테스트."""

    def test_coverage_ratio(self):
        segs = [Segment(start=0, end=590, text="x")]
        self.assertAlmostEqual(run._caption_coverage(segs, 600), 590 / 600)

    def test_coverage_zero_when_empty(self):
        self.assertEqual(run._caption_coverage([], 600), 0.0)

    def test_coverage_unknown_duration_passes(self):
        # duration 을 모르면 검증 불가 → 1.0(통과)로 본다.
        segs = [Segment(start=0, end=5, text="x")]
        self.assertEqual(run._caption_coverage(segs, 0), 1.0)

    def test_one_liner_rejected(self):
        # 사용자 실제 실패 케이스: 긴 영상에 한 줄짜리 자막.
        segs = [Segment(start=0, end=5, text="한 줄뿐인 깨진 자막")]
        self.assertFalse(
            run._caption_is_usable(segs, 600, min_coverage=0.5, min_segments=2)
        )

    def test_low_coverage_rejected(self):
        # 세그먼트는 여러 개지만 앞부분만 덮고 끊긴 자막.
        segs = [Segment(start=0, end=10, text="앞부분"), Segment(start=10, end=20, text="조금")]
        self.assertFalse(
            run._caption_is_usable(segs, 600, min_coverage=0.5, min_segments=2)
        )

    def test_complete_caption_accepted(self):
        segs = [Segment(start=0, end=300, text="앞부분"), Segment(start=300, end=590, text="뒷부분")]
        self.assertTrue(
            run._caption_is_usable(segs, 600, min_coverage=0.5, min_segments=2)
        )

    def test_coverage_check_disabled_when_ratio_zero(self):
        segs = [Segment(start=0, end=10, text="앞부분"), Segment(start=10, end=20, text="조금")]
        self.assertTrue(
            run._caption_is_usable(segs, 600, min_coverage=0.0, min_segments=2)
        )


class TestBuildTranscriptPriority(unittest.TestCase):
    """build_transcript 의 소스 우선순위(STT 우선)와 깨진 자막 폴백 검증."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.vid = "testvid"
        root = self.data / self.vid
        root.mkdir(parents=True)
        (root / "audio.webm").write_bytes(b"x")  # vp.audio() 가 찾도록
        (root / "audio.ko.vtt").write_text("WEBVTT\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _cfg(self, **subs) -> Config:
        c = Config(videos=[], data_dir=str(self.data), output_dir=str(self.data / "out"))
        for k, v in subs.items():
            setattr(c.subtitles, k, v)
        return c

    def _meta(self, **kw) -> dict:
        m = {"id": self.vid, "title": "t", "duration": 600,
             "manual_sub_lang": None, "auto_sub_lang": None}
        m.update(kw)
        return m

    @contextlib.contextmanager
    def _patched(self, meta, stt, parse):
        with (
            mock.patch.object(run.stage1_ingest, "probe", return_value={"id": self.vid}),
            mock.patch.object(run.stage1_ingest, "ingest", return_value=meta),
            mock.patch.object(run.stage3_stt, "transcribe", side_effect=stt),
            mock.patch.object(run.stage2_subtitles, "parse_vtt", parse),
        ):
            yield

    def test_stt_first_uses_stt_and_skips_captions(self):
        meta = self._meta(manual_sub_lang="ko", auto_sub_lang="ko")

        def stt(audio, lang, cfg, log=print):
            return [Segment(start=0, end=580, text="STT 받아쓰기 결과")]

        parse = mock.Mock(return_value=[Segment(start=0, end=600, text="수동 자막")])
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self.assertEqual([s.text for s in segs], ["STT 받아쓰기 결과"])
        parse.assert_not_called()  # STT 성공 → 자막은 건드리지 않음

    def test_broken_manual_caption_falls_through(self):
        # stt_first=False 로 수동 자막을 먼저 시도하지만, 한 줄짜리라 STT 로 폴백.
        meta = self._meta(manual_sub_lang="ko")

        def stt(audio, lang, cfg, log=print):
            return [Segment(start=0, end=580, text="STT 받아쓰기 결과")]

        parse = mock.Mock(return_value=[Segment(start=0, end=5, text="한 줄짜리 깨진 자막")])
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript(
                "testvid", self._cfg(stt_first=False), self.data, False
            )
        parse.assert_called()  # 수동 자막을 시도는 했고
        self.assertEqual([s.text for s in segs], ["STT 받아쓰기 결과"])  # 깨져서 STT 채택

    def test_stt_failure_falls_back_to_valid_manual(self):
        meta = self._meta(manual_sub_lang="ko")

        def stt(audio, lang, cfg, log=print):
            raise RuntimeError("stt boom")

        parse = mock.Mock(
            return_value=[
                Segment(start=0, end=300, text="앞부분 내용"),
                Segment(start=300, end=590, text="뒷부분 내용"),
            ]
        )
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self.assertEqual([s.text for s in segs], ["앞부분 내용", "뒷부분 내용"])


if __name__ == "__main__":
    unittest.main()
