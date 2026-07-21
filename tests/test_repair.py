"""stage_repair(트랜스크립트 LLM 보정) 특성화 테스트.

LLM 은 가짜 클라이언트로 대체하고, 텍스트 교정·타임스탬프 보존·안전 폴백·취소를 검증한다.
"""

from __future__ import annotations

import unittest

from yke.config import LLMConfig
from yke.models import Segment
from yke.pipeline import stage_repair
from yke.utils import StoppedError


class _FakeClient:
    """complete(user) 를 주어진 변환 함수로 처리하는 가짜 LLM 클라이언트."""

    def __init__(self, transform) -> None:
        self.transform = transform
        self.calls: list[str] = []

    def complete(self, system: str, user: str, *, model: str) -> str:
        self.calls.append(user)
        return self.transform(user)


def _segs() -> list[Segment]:
    return [
        Segment(start=0.0, end=2.0, text="안녕 하세요"),
        Segment(start=2.0, end=4.0, text="반갑 습니다"),
    ]


class TestRepairSegments(unittest.TestCase):
    def test_empty_returns_same(self) -> None:
        self.assertEqual(
            stage_repair.repair_segments([], LLMConfig(), _FakeClient(lambda u: u)), []
        )

    def test_text_replaced_timestamps_preserved(self) -> None:
        client = _FakeClient(lambda u: "[00:00] 안녕하세요.\n[00:02] 반갑습니다.")
        out = stage_repair.repair_segments(_segs(), LLMConfig(), client)
        self.assertEqual([s.text for s in out], ["안녕하세요.", "반갑습니다."])
        self.assertEqual([(s.start, s.end) for s in out], [(0.0, 2.0), (2.0, 4.0)])

    def test_lines_without_marker_mapped_positionally(self) -> None:
        client = _FakeClient(lambda u: "안녕하세요.\n반갑습니다.")
        out = stage_repair.repair_segments(_segs(), LLMConfig(), client)
        self.assertEqual([s.text for s in out], ["안녕하세요.", "반갑습니다."])

    def test_line_count_mismatch_keeps_original(self) -> None:
        client = _FakeClient(lambda u: "[00:00] 한 줄만 반환")
        out = stage_repair.repair_segments(_segs(), LLMConfig(), client)
        self.assertEqual([s.text for s in out], ["안녕 하세요", "반갑 습니다"])

    def test_client_failure_keeps_original(self) -> None:
        class _Boom:
            def complete(self, *a, **k):
                raise RuntimeError("api down")

        out = stage_repair.repair_segments(_segs(), LLMConfig(), _Boom())
        self.assertEqual([s.text for s in out], ["안녕 하세요", "반갑 습니다"])

    def test_should_stop_raises_stopped(self) -> None:
        client = _FakeClient(lambda u: u)
        with self.assertRaises(StoppedError):
            stage_repair.repair_segments(
                _segs(), LLMConfig(), client, should_stop=lambda: True
            )

    def test_chunking_splits_large_input(self) -> None:
        # 아주 작은 청크 크기 → 세그먼트마다 별도 LLM 호출.
        client = _FakeClient(lambda u: u)  # 그대로 반환(교정 없음)
        stage_repair.repair_segments(_segs(), LLMConfig(max_chars_per_chunk=5), client)
        self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
