"""2단계: 수동 자막 확인 및 VTT 파싱.

정책: 수동(크리에이터 제작) 자막만 신뢰. 자동생성 자막은 1단계에서 애초에
내려받지 않으므로, 여기서 발견되는 .vtt 는 수동 자막으로 간주한다.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Segment

_TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")


def find_subtitle_file(vpaths) -> Path | None:
    """다운로드된 자막 파일(수동/자동 중 실제로 받은 하나)을 찾는다.

    1단계에서 소스를 결정해 하나만 내려받으므로 파일도 하나만 존재한다.
    수동/자동 구분은 meta.json 의 subtitle_source 에 기록되어 있다.
    """
    hits = sorted(vpaths.root.glob("audio.*.vtt"))
    return hits[0] if hits else None


def _to_seconds(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _collapse_rollup(segments: list[Segment], group_words: int = 12) -> list[Segment]:
    """유튜브 자동자막의 롤업(슬라이딩 윈도우) 중복을 제거해 연속 스트림으로 재구성한다.

    자동자막은 스크롤 효과 때문에 인접 큐가 단어 단위로 겹친다
    (예: "A B C D" 다음 "C D E F"). 이전 큐의 접미와 현재 큐의 접두가 겹치는
    최대 구간을 찾아, 겹치지 않는 새 단어들만 이어 붙여 중복 없는 단어열을 만든 뒤
    ~group_words 단어 단위로 다시 세그먼트로 묶는다(타임스탬프 보존).
    """
    words: list[tuple[float, str]] = []  # (start, word)
    prev: list[str] = []
    for seg in segments:
        cur = seg.text.split()
        if not cur:
            continue
        overlap = 0
        for k in range(min(len(prev), len(cur)), 0, -1):
            if prev[-k:] == cur[:k]:
                overlap = k
                break
        words.extend((seg.start, w) for w in cur[overlap:])
        prev = cur

    out: list[Segment] = []
    for i in range(0, len(words), group_words):
        chunk = words[i : i + group_words]
        out.append(
            Segment(
                start=chunk[0][0],
                end=chunk[-1][0],
                text=" ".join(w for _, w in chunk),
            )
        )
    return out


def parse_vtt(path: Path, collapse_rollup: bool = False) -> list[Segment]:
    content = path.read_text(encoding="utf-8", errors="ignore")
    segments: list[Segment] = []
    seen: set[tuple[float, str]] = set()

    for block in re.split(r"\n\s*\n", content):
        start = end = None
        text_lines: list[str] = []
        for line in block.strip().splitlines():
            m = _TS_RE.search(line)
            if m:
                start = _to_seconds(*m.group(1, 2, 3, 4))
                end = _to_seconds(*m.group(5, 6, 7, 8))
            elif line.strip() and "WEBVTT" not in line and "-->" not in line:
                clean = _TAG_RE.sub("", line).strip()
                if clean and not clean.isdigit():
                    text_lines.append(clean)
        if start is not None and text_lines:
            text = " ".join(text_lines)
            key = (round(start, 1), text)
            if key in seen:  # VTT 롤업 자막 중복 제거
                continue
            seen.add(key)
            segments.append(Segment(start=start, end=end or start, text=text))

    if collapse_rollup:
        segments = _collapse_rollup(segments)
    return segments
