"""4단계: 텍스트 정제 (규칙 기반, 경량).

PoC 에서는 공백 정규화와 무의미 세그먼트 제거만 한다. 한국어에서 filler 를
공격적으로 지우면 의미가 훼손될 수 있어, 광고/인사 구간 제거 같은 LLM 클렌징은
스케일업 단계로 미룬다.
"""

from __future__ import annotations

import re

from ..models import Segment


def clean_segments(segments: list[Segment]) -> list[Segment]:
    cleaned: list[Segment] = []
    for seg in segments:
        text = re.sub(r"\s+", " ", seg.text).strip()
        if len(text) < 2:
            continue
        cleaned.append(seg.model_copy(update={"text": text}))
    return cleaned
