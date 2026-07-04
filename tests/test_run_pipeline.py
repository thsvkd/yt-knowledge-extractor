"""run_pipeline 오케스트레이션 특성화 테스트.

네트워크/LLM 은 목으로 대체하고, 진행 이벤트·취소·실패 격리·단계 게이팅만 검증한다.
"""

from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
