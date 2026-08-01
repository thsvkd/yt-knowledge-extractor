"""업데이트 진행률 표시(_ease_progress / _download_progress) 검증.

사용자 피드백에서 출발한다: v0.1.3 → v0.1.4 업데이트가 "0에서 70%까지 갔다가 멈춘 뒤
갑자기 업데이트 창이 뜬다". 원인은 우리 코드가 아니라 Velopack 이 주는 진행률이 사실상
셋뿐이라는 데 있다(velopack 1.2.0 rust manager.rs):

    download_release_entry(delta, .., None)      ← 델타 바이트 진행률이 꺼져 있다
    progress.send((i / N) * 70)                  ← 델타를 다 받은 뒤에야
    progress.send(70)                            ← 패치 시작 직전
    <Update.exe 로 전체 패키지 재구성 — 길다>
    progress.send(100)

델타가 1개면 0 → 70 → (긴 무음) → 100 이 전부다. 그래서 표시값은 체크포인트를 하한으로만
쓰고 시간 기반으로 계속 올라가야 한다. 이 테스트가 고정하는 성질은 셋이다.
  1. 표시값은 절대 뒤로 가지 않는다.
  2. 실제 완료 전에는 100 을 표시하지 않는다(100 에서 멈추면 70 에서 멈추던 것과 같다).
  3. 무음 구간(패치)에서도 계속 올라간다 — 이게 원래 버그의 핵심이다.
"""

from __future__ import annotations

import unittest

from yke import gui
from yke.gui import PipelineGUI


class _Ctl:
    """value/color/update 만 흉내내는 flet 컨트롤 스텁."""

    def __init__(self) -> None:
        self.value = None
        self.color = None

    def update(self) -> None:
        pass


def _gui() -> PipelineGUI:
    """__init__ 을 우회해 진행률 로직만 쓰는 인스턴스를 만든다(flet Page 불필요)."""
    g = object.__new__(PipelineGUI)
    g.update_status = _Ctl()
    # _start_update_ticker 가 세우는 상태를 스레드 없이 직접 초기화한다.
    import threading

    g._update_lock = threading.Lock()
    g._update_shown = 0.0
    g._update_ceiling = gui._UPDATE_CEIL_DOWNLOAD
    g._update_ease = gui._UPDATE_EASE_DOWNLOAD
    return g


def _tick(g: PipelineGUI, times: int) -> float:
    """티커 루프의 계산만 times 번 돌린다(스레드·sleep 없이)."""
    for _ in range(times):
        g._update_shown = gui._ease_progress(
            g._update_shown, g._update_ceiling, g._update_ease, gui._UPDATE_TICK_SECONDS
        )
    return g._update_shown


class TestEaseProgress(unittest.TestCase):
    def test_never_exceeds_ceiling(self) -> None:
        shown = 0.0
        for _ in range(10_000):
            shown = gui._ease_progress(shown, 70.0, 8.0, 0.12)
            self.assertLessEqual(shown, 70.0)

    def test_monotonically_increases(self) -> None:
        shown, prev = 0.0, -1.0
        for _ in range(500):
            shown = gui._ease_progress(shown, 70.0, 8.0, 0.12)
            self.assertGreater(shown, prev)
            prev = shown

    def test_approaches_ceiling(self) -> None:
        """충분히 오래 두면 천장에 실질적으로 도달한다(영원히 30%에 머물지 않는다)."""
        shown = 0.0
        for _ in range(2000):
            shown = gui._ease_progress(shown, 70.0, 8.0, 0.12)
        self.assertGreater(shown, 69.9)

    def test_zero_ease_snaps(self) -> None:
        self.assertEqual(gui._ease_progress(0.0, 99.0, 0.0, 0.12), 99.0)


class TestVelopackCheckpoints(unittest.TestCase):
    def test_checkpoint_below_70_raises_floor(self) -> None:
        g = _gui()
        g._download_progress(0.35)  # 델타 여러 개 중 중간
        self.assertGreaterEqual(g._update_shown, 35.0)

    def test_checkpoint_does_not_move_backwards(self) -> None:
        """velopack 은 델타 1개일 때 (0/1)*70 = 0 을 보낸다 — 이미 오른 표시값을 되돌리면 안 된다."""
        g = _gui()
        _tick(g, 100)
        before = g._update_shown
        self.assertGreater(before, 0.0)
        g._download_progress(0.0)  # 실제로 오는 값
        self.assertGreaterEqual(g._update_shown, before)

    def test_patch_checkpoint_switches_ceiling(self) -> None:
        g = _gui()
        g._download_progress(0.70)
        self.assertGreaterEqual(g._update_shown, gui._UPDATE_CEIL_DOWNLOAD)
        self.assertEqual(g._update_ceiling, gui._UPDATE_CEIL_PATCH)

    def test_progress_keeps_moving_during_silent_patch(self) -> None:
        """원래 버그: 70 체크포인트 뒤 무음 구간에서 표시값이 멈춰 있었다."""
        g = _gui()
        g._download_progress(0.70)
        at_70 = g._update_shown
        after_5s = _tick(g, int(5 / gui._UPDATE_TICK_SECONDS))
        after_30s = _tick(g, int(25 / gui._UPDATE_TICK_SECONDS))
        self.assertGreater(after_5s, at_70 + 1.0)  # 5초만 지나도 눈에 띄게 올라간다
        self.assertGreater(after_30s, after_5s)
        self.assertLess(after_30s, 100.0)

    def test_never_shows_100_before_completion(self) -> None:
        """velopack 의 100 체크포인트 뒤에도 download() 는 rename·정리로 몇 초 더 돈다."""
        g = _gui()
        g._download_progress(0.70)
        g._download_progress(1.0)
        shown = _tick(g, int(60 / gui._UPDATE_TICK_SECONDS))
        self.assertLess(shown, 100.0)
        self.assertGreater(int(shown), 98)  # 그래도 완료 직전임이 보여야 한다

    def test_callback_without_ticker_is_safe(self) -> None:
        """콜백이 예외를 내면 velopack 다운로드가 끊긴다 — 티커가 없어도 죽지 않아야 한다."""
        g = object.__new__(PipelineGUI)
        g._download_progress(0.5)  # 예외 없이 무시


if __name__ == "__main__":
    unittest.main()
