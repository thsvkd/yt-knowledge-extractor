"""run_pipeline 오케스트레이션 특성화 테스트.

네트워크/LLM 은 목으로 대체하고, 진행 이벤트·취소·실패 격리·단계 게이팅만 검증한다.
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yke import run
from yke.config import Config, LLMConfig
from yke.models import KnowledgeUnit, Segment
from yke.utils import StoppedError


def _seg() -> list[Segment]:
    return [Segment(start=0.0, end=1.0, text="hi")]


def _fake_bt(url, cfg, data_dir, force, *, log=print, on_progress=None, on_stt_progress=None, should_stop=None):
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

    def test_stt_sub_progress_reported_and_transient(self) -> None:
        # STT 세부 진행(초 단위)이 combined sub_progress 로 변환되어 흘러가는지,
        # 그리고 스피너/바 전용(transient)이라 영속 로그에는 안 남는지 확인한다.
        def bt(url, cfg, data_dir, force, *, log=print, on_progress=None, on_stt_progress=None, should_stop=None):
            if on_stt_progress:
                on_stt_progress(30.0, 100.0)
                on_stt_progress(100.0, 100.0)
            return "v_" + url, {"id": "v_" + url}, _seg()

        events: list[run.Progress] = []
        with mock.patch.object(run, "build_transcript", side_effect=bt):
            run.run_pipeline(["a"], self.cfg, stage="transcript", on_progress=events.append)

        sub_events = [e for e in events if e.sub_progress is not None]
        self.assertTrue(any(abs(e.sub_progress - 0.3) < 1e-9 for e in sub_events))
        self.assertTrue(any(e.sub_progress == 1.0 for e in sub_events))
        self.assertTrue(all(e.transient for e in sub_events))

    def test_failure_is_isolated(self) -> None:
        def bt(url, cfg, data_dir, force, *, log=print, on_progress=None, on_stt_progress=None, should_stop=None):
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

    def test_stopped_error_mid_video_stt_is_not_a_failure(self) -> None:
        # 진행 중이던 영상의 STT 자체가 should_stop 을 감지해 StoppedError 를 던진 경우 —
        # (다음 영상 경계를 기다리지 않고) 즉시 멈추되, failures 목록에는 넣지 않는다.
        def bt(url, cfg, data_dir, force, *, log=print, on_progress=None, on_stt_progress=None, should_stop=None):
            if url == "slow":
                raise StoppedError("stt stopped mid video")
            return "v_" + url, {"id": "v_" + url}, _seg()

        events: list[run.Progress] = []
        with mock.patch.object(run, "build_transcript", side_effect=bt):
            res = run.run_pipeline(
                ["slow", "never-reached"],
                self.cfg,
                stage="transcript",
                on_progress=events.append,
                should_stop=lambda: False,  # STT 내부에서만 감지된 상황을 흉내
            )
        self.assertTrue(res.stopped)
        self.assertEqual(res.failures, [])
        self.assertEqual(res.video_count, 0)
        self.assertFalse(any(e.level == "error" for e in events))

    def test_stop_between_videos(self) -> None:
        processed = {"n": 0}

        def bt(url, cfg, data_dir, force, *, log=print, on_progress=None, on_stt_progress=None, should_stop=None):
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
            return [
                run.stage1_ingest.VideoEntry(url="https://youtu.be/a"),
                run.stage1_ingest.VideoEntry(url="https://youtu.be/b"),
            ]

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

    def test_channel_preskips_unplayable_without_probe(self) -> None:
        # 채널 분석에서 availability=subscriber_only 로 이미 확인된 영상은 build_transcript
        # 를 아예 호출하지 않고 즉시 스킵한다(영상별 재조회 없음 = 빠른 스킵).
        def fake_expand(url, limit, *, log=print):
            return [
                run.stage1_ingest.VideoEntry(
                    url="https://youtu.be/mem", video_id="mem", availability="subscriber_only"
                ),
                run.stage1_ingest.VideoEntry(url="https://youtu.be/ok", video_id="ok"),
            ]

        with (
            mock.patch.object(run.stage1_ingest, "expand_source", side_effect=fake_expand),
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt) as bt,
        ):
            res = run.run_pipeline(
                ["https://www.youtube.com/@chan"], self.cfg, stage="transcript"
            )
        # 재생 가능한 영상(ok)만 실제로 처리 → build_transcript 는 딱 1회 호출.
        self.assertEqual(bt.call_count, 1)
        self.assertEqual(res.video_count, 1)
        self.assertIn("https://youtu.be/mem", res.failures)
        skipped = [r for r in res.results if r.status == "skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].error_reason, "멤버 전용 영상")

    def test_result_records_elapsed_and_per_video(self) -> None:
        with mock.patch.object(run, "build_transcript", side_effect=_fake_bt):
            res = run.run_pipeline(["u1", "u2"], self.cfg, stage="transcript")
        self.assertGreaterEqual(res.elapsed_seconds, 0.0)
        self.assertEqual(len(res.results), 2)
        self.assertTrue(all(r.status == "done" for r in res.results))

    def test_run_report_written(self) -> None:
        with mock.patch.object(run, "build_transcript", side_effect=_fake_bt):
            res = run.run_pipeline(["u1"], self.cfg, stage="transcript")
        report = Path(res.out_dir) / "run_report.json"
        self.assertTrue(report.exists())
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["done"], 1)
        self.assertIn("elapsed_seconds", data)
        self.assertEqual(len(data["videos"]), 1)


class TestRepairPass(unittest.TestCase):
    """트랜스크립트 확보 직후 LLM 자막 보정(cfg.llm.repair_transcript) 오케스트레이션."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(
            videos=[],
            data_dir=f"{self.tmp.name}/data",
            output_dir=f"{self.tmp.name}/out",
            llm=LLMConfig(provider="claude", repair_transcript=True),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _fake_repair(self, segs, llm_cfg, client, *, log=print, should_stop=lambda: False):
        return [s.model_copy(update={"text": s.text + "!"}) for s in segs]

    def test_repair_runs_and_persists_when_enabled(self) -> None:
        with (
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
            mock.patch("yke.llm.make_client", return_value=mock.Mock()),
            mock.patch.object(
                run.stage_repair, "repair_segments", side_effect=self._fake_repair
            ) as mrepair,
        ):
            res = run.run_pipeline(["u1"], self.cfg, stage="transcript")
        self.assertTrue(mrepair.called)
        self.assertFalse(res.stopped)
        # 보정본이 transcript.json + transcript.txt 로 저장된다(원본 raw 는 보존).
        from yke.paths import VideoPaths

        vp = VideoPaths(Path(self.cfg.data_dir), "v_u1")
        data = json.loads(vp.transcript.read_text(encoding="utf-8"))
        self.assertEqual(data[0]["text"], "hi!")
        self.assertTrue(vp.transcript_txt.exists())
        self.assertIn("hi!", vp.transcript_txt.read_text(encoding="utf-8"))

    def test_repair_skipped_gracefully_when_llm_unavailable(self) -> None:
        events: list[run.Progress] = []
        with (
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
            mock.patch("yke.llm.make_client", side_effect=RuntimeError("no creds")),
            mock.patch.object(run.stage_repair, "repair_segments") as mrepair,
        ):
            res = run.run_pipeline(["u1"], self.cfg, stage="transcript", on_progress=events.append)
        # 자격증명이 없으면 보정만 건너뛰고 파이프라인은 계속된다(트랜스크립트는 확보됨).
        self.assertFalse(mrepair.called)
        self.assertEqual(res.video_count, 1)
        self.assertTrue(any("자막 보정 건너뜀" in e.message for e in events))

    def test_repair_not_run_when_disabled(self) -> None:
        self.cfg.llm.repair_transcript = False
        with (
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
            mock.patch.object(run.stage_repair, "repair_segments") as mrepair,
        ):
            run.run_pipeline(["u1"], self.cfg, stage="transcript")
        self.assertFalse(mrepair.called)

    def test_repair_stop_returns_stopped(self) -> None:
        def _stop_repair(segs, llm_cfg, client, *, log=print, should_stop=lambda: False):
            raise StoppedError()

        with (
            mock.patch.object(run, "build_transcript", side_effect=_fake_bt),
            mock.patch("yke.llm.make_client", return_value=mock.Mock()),
            mock.patch.object(run.stage_repair, "repair_segments", side_effect=_stop_repair),
        ):
            res = run.run_pipeline(["u1"], self.cfg, stage="transcript")
        self.assertTrue(res.stopped)


class TestFmtHms(unittest.TestCase):
    def test_under_hour(self):
        self.assertEqual(run._fmt_hms(65), "1:05")

    def test_over_hour(self):
        self.assertEqual(run._fmt_hms(3661), "1:01:01")

    def test_negative_clamped_to_zero(self):
        self.assertEqual(run._fmt_hms(-5), "0:00")


class TestSttProgressReporterThrottle(unittest.TestCase):
    """빈번한 세그먼트 진행 콜백을 최소 간격으로 솎아내되, 100% 는 항상 통과시킨다."""

    def test_throttles_rapid_updates(self):
        events: list[run.Progress] = []
        reporter = run._make_stt_progress_reporter(events.append, 1, 1, "u")
        with mock.patch.object(run.time, "monotonic", side_effect=[100.0, 100.05, 100.3]):
            reporter(10, 100)  # 최초 호출 -> 항상 통과
            reporter(15, 100)  # 0.05s 뒤 -> 최소 간격(0.2s) 미달로 억제
            reporter(90, 100)  # 0.3s 뒤 -> 간격 충족, 통과
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0].sub_progress, 0.1)
        self.assertAlmostEqual(events[1].sub_progress, 0.9)

    def test_100_percent_always_emitted(self):
        events: list[run.Progress] = []
        reporter = run._make_stt_progress_reporter(events.append, 1, 1, "u")
        with mock.patch.object(run.time, "monotonic", side_effect=[100.0, 100.01]):
            reporter(50, 100)  # 최초 호출 -> 통과
            reporter(100, 100)  # 100%: 간격 미달이어도 항상 통과
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].sub_progress, 1.0)


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
    """build_transcript 의 소스 우선순위(수동 자막 > 유튜브 자동자막 > 로컬 STT) 검증."""

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
        # ingest 는 build_meta(메타만) + lazy 다운로드(자막/오디오)로 쪼개졌다. 자막은
        # setUp 이 미리 만들어 둔 audio.ko.vtt 를, 오디오는 audio.webm 을 돌려주도록 목킹해
        # 실제 네트워크 없이 우선순위/폴백 로직만 검증한다. (자막으로 처리되는 경로에서는
        # download_audio 가 호출되지 않아야 오디오 미다운로드 성능 요구가 지켜진다.)
        root = self.data / self.vid
        with (
            mock.patch.object(run.stage1_ingest, "probe", return_value={"id": self.vid}),
            mock.patch.object(run.stage1_ingest, "build_meta", return_value=meta),
            mock.patch.object(
                run.stage1_ingest, "download_manual_subtitle", return_value=root / "audio.ko.vtt"
            ),
            mock.patch.object(
                run.stage1_ingest, "download_audio", return_value=root / "audio.webm"
            ) as dl_audio,
            mock.patch.object(run.stage3_stt, "transcribe", side_effect=stt),
            mock.patch.object(run.stage2_subtitles, "parse_vtt", parse),
        ):
            self._dl_audio = dl_audio
            yield

    def test_default_prefers_valid_manual_caption_over_stt(self):
        # 기본(stt_first=False)은 업로더 제공 자막이 멀쩡하면 STT 를 아예 돌리지 않는다.
        meta = self._meta(manual_sub_lang="ko", auto_sub_lang="ko")

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            raise AssertionError("유효한 수동 자막이 있으면 STT 는 호출되지 않아야 함")

        parse = mock.Mock(
            return_value=[
                Segment(start=0, end=300, text="앞부분 내용"),
                Segment(start=300, end=600, text="뒷부분 내용"),
            ]
        )
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self.assertEqual([s.text for s in segs], ["앞부분 내용", "뒷부분 내용"])

    def test_valid_manual_caption_skips_audio_download(self):
        # 성능 핵심: 수동 자막으로 처리되면 오디오 원본을 아예 내려받지 않는다.
        meta = self._meta(manual_sub_lang="ko", auto_sub_lang="ko")

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            raise AssertionError("자막으로 처리되면 STT/오디오는 건드리지 않아야 함")

        parse = mock.Mock(
            return_value=[
                Segment(start=0, end=300, text="앞부분"),
                Segment(start=300, end=600, text="뒷부분"),
            ]
        )
        with self._patched(meta, stt, parse):
            _vid, m, _segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self._dl_audio.assert_not_called()  # 오디오 다운로드 없음
        self.assertEqual(m.get("transcript_source"), "manual")

    def test_stt_fallback_records_source_and_reason(self):
        # 자막이 모두 없어 STT 로 폴백하면, meta 에 source=stt 와 폴백 사유가 기록된다.
        meta = self._meta()  # manual/auto 자막 모두 없음

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            return [Segment(start=0, end=580, text="STT 결과")]

        parse = mock.Mock(return_value=[])
        with self._patched(meta, stt, parse):
            _vid, m, _segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self._dl_audio.assert_called_once()  # STT 폴백이므로 오디오는 받는다
        self.assertEqual(m.get("transcript_source"), "stt")
        self.assertTrue(m.get("fallback_reason"))

    def test_broken_manual_caption_falls_through_to_stt(self):
        # 기본(stt_first=False)으로 수동 자막을 먼저 시도하지만, 한 줄짜리라 STT 로 폴백.
        meta = self._meta(manual_sub_lang="ko")

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            return [Segment(start=0, end=580, text="STT 받아쓰기 결과")]

        parse = mock.Mock(return_value=[Segment(start=0, end=5, text="한 줄짜리 깨진 자막")])
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        parse.assert_called()  # 수동 자막을 시도는 했고
        self.assertEqual([s.text for s in segs], ["STT 받아쓰기 결과"])  # 깨져서 STT 로 예외 폴백

    def test_no_manual_caption_prefers_auto_over_stt(self):
        # 새 우선순위: 수동 자막이 아예 없는 영상(예: 업로더 미제공)이면, 로컬 STT 보다
        # 먼저 유튜브 자동 생성 자막을 시도한다 — STT 는 그마저 없거나 깨졌을 때만 돈다.
        meta = self._meta(auto_sub_lang="ko")  # manual_sub_lang 없음

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            raise AssertionError("자동 자막이 멀쩡하면 STT 는 호출되지 않아야 함")

        parse = mock.Mock(
            return_value=[
                Segment(start=0, end=300, text="자동 자막 앞부분"),
                Segment(start=300, end=600, text="자동 자막 뒷부분"),
            ]
        )
        with (
            self._patched(meta, stt, parse),
            mock.patch.object(
                run.stage1_ingest, "download_auto_subtitle", return_value=Path("audio.ko.vtt")
            ),
        ):
            _vid, _m, segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self.assertEqual([s.text for s in segs], ["자동 자막 앞부분", "자동 자막 뒷부분"])

    def test_missing_manual_and_broken_auto_falls_through_to_stt(self):
        # 수동 자막도 없고 자동 자막마저 깨졌을 때(한 줄짜리)만 로컬 STT 가 돈다.
        meta = self._meta(auto_sub_lang="ko")

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            return [Segment(start=0, end=580, text="STT 받아쓰기 결과")]

        parse = mock.Mock(return_value=[Segment(start=0, end=5, text="한 줄짜리 깨진 자동 자막")])
        with (
            self._patched(meta, stt, parse),
            mock.patch.object(
                run.stage1_ingest, "download_auto_subtitle", return_value=Path("audio.ko.vtt")
            ),
        ):
            _vid, _m, segs = run.build_transcript("testvid", self._cfg(), self.data, False)
        self.assertEqual([s.text for s in segs], ["STT 받아쓰기 결과"])

    def test_explicit_stt_first_uses_stt_and_skips_captions(self):
        # stt_first=True 로 명시하면(레거시 옵션) 여전히 STT 를 1순위로 쓸 수 있다.
        meta = self._meta(manual_sub_lang="ko", auto_sub_lang="ko")

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            return [Segment(start=0, end=580, text="STT 받아쓰기 결과")]

        parse = mock.Mock(return_value=[Segment(start=0, end=600, text="수동 자막")])
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript(
                "testvid", self._cfg(stt_first=True), self.data, False
            )
        self.assertEqual([s.text for s in segs], ["STT 받아쓰기 결과"])
        parse.assert_not_called()  # STT 성공 → 자막은 건드리지 않음

    def test_explicit_stt_first_failure_falls_back_to_valid_manual(self):
        meta = self._meta(manual_sub_lang="ko")

        def stt(audio, lang, cfg, log=print, on_progress=None, should_stop=None):
            raise RuntimeError("stt boom")

        parse = mock.Mock(
            return_value=[
                Segment(start=0, end=300, text="앞부분 내용"),
                Segment(start=300, end=590, text="뒷부분 내용"),
            ]
        )
        with self._patched(meta, stt, parse):
            _vid, _m, segs = run.build_transcript(
                "testvid", self._cfg(stt_first=True), self.data, False
            )
        self.assertEqual([s.text for s in segs], ["앞부분 내용", "뒷부분 내용"])

    def test_writes_raw_json_and_txt_not_repaired_files(self):
        # 보정 없는 경로: 원본 raw.json + raw.txt(+meta)만 생기고, 보정본(transcript.json)은 없다.
        meta = self._meta(manual_sub_lang="ko")
        parse = mock.Mock(
            return_value=[
                Segment(start=0, end=300, text="앞부분"),
                Segment(start=305, end=600, text="뒷부분"),
            ]
        )

        def stt(*a, **k):
            raise AssertionError("자막 경로")

        with self._patched(meta, stt, parse):
            run.build_transcript("testvid", self._cfg(), self.data, False)
        from yke.paths import VideoPaths

        vp = VideoPaths(self.data, self.vid)
        self.assertTrue(vp.transcript_raw.exists())
        self.assertTrue(vp.transcript_raw_txt.exists())
        self.assertFalse(vp.transcript.exists())  # 보정 안 했으니 보정본 없음
        self.assertFalse(vp.transcript_txt.exists())
        # txt 는 [MM:SS] 형식의 사람이 읽는 줄.
        txt = vp.transcript_raw_txt.read_text(encoding="utf-8")
        self.assertIn("[00:00] 앞부분", txt)
        self.assertIn("[05:05] 뒷부분", txt)

    def test_cache_hit_reads_raw_json(self):
        # raw.json + meta 가 있으면 네트워크 조회(probe) 없이 캐시에서 로딩한다.
        # (fast-path 는 URL 에서 11자 영상 ID 를 뽑으므로 유효한 ID 로 캐시를 만든다.)
        vid11 = "abcdefghijk"
        vp_root = self.data / vid11
        vp_root.mkdir(parents=True)
        (vp_root / "transcript.raw.json").write_text(
            json.dumps([{"start": 0, "end": 1, "text": "캐시된 원본"}]), encoding="utf-8"
        )
        (vp_root / "meta.json").write_text(json.dumps(self._meta(id=vid11)), encoding="utf-8")
        with mock.patch.object(
            run.stage1_ingest, "probe", side_effect=AssertionError("캐시 히트면 probe 안 함")
        ):
            _vid, _m, segs = run.build_transcript(
                f"https://youtu.be/{vid11}", self._cfg(), self.data, False
            )
        self.assertEqual([s.text for s in segs], ["캐시된 원본"])


if __name__ == "__main__":
    unittest.main()
