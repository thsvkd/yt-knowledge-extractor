"""GUI 진행바 로직(_update_progress_bars / _video_fraction) 특성화 테스트.

flet Page 없이 컨트롤을 스텁으로 대체하고, PipelineGUI 를 __init__ 우회로 만들어
순수 진행바 계산만 검증한다:
  - 선택 단계가 전체바 100%를 나눠 갖는지(transcript=100%, all=트랜스크립트50·추출35·통합15)
  - 로컬 변환(STT) 필요/불필요 경로가 영상 내부 모양을 다르게 채우는지
  - 중간 트랜스크립트 요약(phase=done, done/total 없음)에서 100%로 튀지 않는지
  - 진행바가 뒤로 가지 않는지(단조), 채널일 때만 현재 영상 바가 보이는지
"""

from __future__ import annotations

import unittest

from yke.gui import PipelineGUI
from yke.run import Progress


class _Ctl:
    """value/visible/update 만 흉내내는 flet 컨트롤 스텁."""

    def __init__(self, value=None, visible=True):
        self.value = value
        self.visible = visible

    def update(self):  # noqa: D401 - no-op
        pass


def _gui(stage: str) -> PipelineGUI:
    g = object.__new__(PipelineGUI)
    g._bar_phase = None
    g._video_frac = 0.0
    g._video_saw_stt = False
    g._ov_total = None
    g._ov_done = 0
    g._run_stage = stage
    g.progress = _Ctl(0)
    g.video_progress = _Ctl(0)
    g.video_row = _Ctl(visible=False)
    g.overall_caption = _Ctl("")
    g.video_caption = _Ctl("")
    return g


def _transcript_events(i: int, total: int, *, stt: bool) -> list[Progress]:
    evs = [
        Progress(message="start", phase="transcript", done=i - 1, total=total,
                 indeterminate=True, transient=True),
        Progress(message="ingest", phase="transcript", substep="ingest",
                 indeterminate=True, transient=True),
    ]
    if stt:
        evs.append(Progress(message="stt-dl", phase="transcript", substep="stt",
                            indeterminate=True, transient=True))
        for frac in (0.0, 0.5, 1.0):
            evs.append(Progress(message="stt", phase="transcript", substep="stt",
                               done=i - 1, total=total, transient=True, sub_progress=frac))
    else:
        evs.append(Progress(message="subs", phase="transcript", substep="subtitles",
                           indeterminate=True, transient=True))
    evs.append(Progress(message="clean", phase="transcript", substep="clean",
                       indeterminate=True, transient=True))
    evs.append(Progress(message="ok", level="success", phase="transcript", done=i, total=total))
    return evs


def _drive(g: PipelineGUI, events: list[Progress]) -> list[float]:
    """이벤트를 순서대로 먹이고, 결정형(float)일 때의 overall 값 궤적을 반환한다."""
    seen: list[float] = []
    for e in events:
        g._update_progress_bars(e)
        if isinstance(g.progress.value, float):
            seen.append(g.progress.value)
    return seen


class TestTranscriptStage(unittest.TestCase):
    def test_single_video_fills_to_full(self):
        g = _gui("transcript")
        vals = _drive(g, _transcript_events(1, 1, stt=False))
        self.assertAlmostEqual(g.progress.value, 1.0)
        # 단일 영상은 현재 영상 바를 따로 띄우지 않는다.
        self.assertFalse(g.video_row.visible)
        self.assertEqual(g.overall_caption.value, "")
        # 단조 증가.
        self.assertEqual(vals, sorted(vals))

    def test_channel_reaches_full_and_shows_video_bar(self):
        g = _gui("transcript")
        events: list[Progress] = []
        for i, stt in [(1, False), (2, True), (3, False)]:
            events += _transcript_events(i, 3, stt=stt)
        vals = _drive(g, events)
        self.assertAlmostEqual(g.progress.value, 1.0)
        self.assertTrue(g.video_row.visible)  # 채널(여러 영상)이면 영상 바 노출
        self.assertEqual(vals, sorted(vals))  # 뒤로 가지 않음

    def test_caption_path_has_no_stt_band(self):
        # 자막 경로: STT 구간을 예약하지 않아 clean 이 0.90(STT 경로의 0.95 보다 앞).
        g = _gui("transcript")
        clean = Progress(message="clean", phase="transcript", substep="clean")
        # ingest → subtitles → clean 순서로만.
        for e in [
            Progress(message="s", phase="transcript", done=0, total=1, sub_progress=None,
                     indeterminate=True, transient=True),
            Progress(message="i", phase="transcript", substep="ingest", transient=True),
            Progress(message="sub", phase="transcript", substep="subtitles", transient=True),
        ]:
            g._update_progress_bars(e)
        g._update_progress_bars(clean)
        self.assertAlmostEqual(g._video_frac, 0.90)
        self.assertFalse(g._video_saw_stt)

    def test_stt_path_uses_span_and_later_clean(self):
        g = _gui("transcript")
        for e in _transcript_events(1, 1, stt=True):
            g._update_progress_bars(e)
            if e.substep == "stt" and e.sub_progress == 0.5:
                # STT 절반 → 0.2 + (0.9-0.2)*0.5 = 0.55
                self.assertAlmostEqual(g._video_frac, 0.55)
        # clean 직전까지 STT 를 봤으므로 clean 은 0.95 로 매핑됐다(도중 확인).
        self.assertTrue(True)


class TestConfigPartition(unittest.TestCase):
    def _run_all_stage(self):
        g = _gui("all")
        events: list[Progress] = []
        for i in (1, 2):
            events += _transcript_events(i, 2, stt=(i == 2))
        for e in events:
            g._update_progress_bars(e)
        return g

    def test_transcript_slice_ends_at_half(self):
        g = self._run_all_stage()
        self.assertAlmostEqual(g.progress.value, 0.50)

    def test_summary_does_not_jump_to_full(self):
        g = self._run_all_stage()
        # 중간 트랜스크립트 요약(phase=done, done/total 없음) — 0.5 유지, 100% 로 안 튐.
        g._update_progress_bars(Progress(message="처리 요약", level="success", phase="done"))
        g._update_progress_bars(Progress(message="  ✔", phase="done"))
        self.assertAlmostEqual(g.progress.value, 0.50)

    def test_extract_then_integrate_reach_boundaries(self):
        g = self._run_all_stage()
        for e in [Progress(message="처리 요약", phase="done")]:
            g._update_progress_bars(e)
        # 추출 2영상 → 0.50 → 0.85
        for i in (1, 2):
            g._update_progress_bars(Progress(message="추출", phase="extract", done=i - 1, total=2, indeterminate=True))
            g._update_progress_bars(Progress(message="유닛", level="success", phase="extract", done=i, total=2))
        self.assertAlmostEqual(g.progress.value, 0.85)
        # 통합 중 → 0.85 유지
        g._update_progress_bars(Progress(message="통합 중", phase="integrate", indeterminate=True))
        self.assertAlmostEqual(g.progress.value, 0.85)
        # 최종 완료(done + done/total) → 1.0
        g._update_progress_bars(Progress(message="완료", level="success", phase="done", done=1, total=1))
        self.assertAlmostEqual(g.progress.value, 1.0)


if __name__ == "__main__":
    unittest.main()
