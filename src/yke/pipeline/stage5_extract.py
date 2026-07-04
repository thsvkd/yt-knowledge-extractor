"""5단계: 영상별 '지식 원자 단위' 추출 (LLM, 구조화 출력).

서술형 요약이 아니라 concept/statement/type/timestamp/quote_evidence 로 구성된
JSON 배열을 뽑는다. 트랜스크립트를 청크로 나눠 각 청크마다 호출한다.
"""

from __future__ import annotations

from ..models import KnowledgeUnit, Segment
from ..utils import fmt_ts, parse_json_array

_SYSTEM = """당신은 영상 트랜스크립트에서 '지식 원자 단위(atomic knowledge unit)'를 추출하는 도구입니다.
서술형 요약이 아니라, 검증 가능한 최소 지식 조각을 구조화된 JSON으로 뽑아냅니다.

각 단위는 다음 필드를 가집니다:
- concept: 핵심 개념/주제 (짧은 명사구, 영상 전반에서 일관되게 명명할 것)
- statement: 영상에서 주장/설명한 핵심 명제 (1~2문장, 원문 근거 기반)
- type: "fact" | "opinion" | "tip" | "definition" 중 하나
- timestamp: 해당 내용이 나온 시각 (입력에 표시된 [MM:SS] 중 가장 가까운 값)
- quote_evidence: 판단 근거가 된 원문 구절 (짧게, 검증용)

규칙:
- 광고/인사/잡담/구독요청 구간은 무시합니다.
- 트랜스크립트에 실제로 있는 내용만 추출하고 추측하지 않습니다.
- 개인 의견과 검증 가능한 사실을 반드시 type 으로 구분합니다.
- 출력은 오직 JSON 배열만. 다른 설명 텍스트를 절대 덧붙이지 마세요."""


def _chunk(segments: list[Segment], max_chars: int) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    size = 0
    for seg in segments:
        line = f"[{fmt_ts(seg.start)}] {seg.text}"
        if size + len(line) > max_chars and cur:
            chunks.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def extract_units(segments, video_id: str, llm_cfg, client) -> list[KnowledgeUnit]:
    units: list[KnowledgeUnit] = []
    chunks = _chunk(segments, llm_cfg.max_chars_per_chunk)
    for idx, chunk in enumerate(chunks, 1):
        user = (
            f"다음은 영상 트랜스크립트의 일부입니다. 지식 원자 단위를 JSON 배열로 추출하세요.\n\n{chunk}"
        )
        text = client.complete(_SYSTEM, user, model=llm_cfg.model, max_tokens=8000)
        for obj in parse_json_array(text):
            if not isinstance(obj, dict):
                continue
            obj["source_video_id"] = video_id
            try:
                units.append(KnowledgeUnit(**obj))
            except Exception:
                continue  # 스키마 불일치 항목은 건너뜀
        print(f"    청크 {idx}/{len(chunks)} 완료 (누적 {len(units)} 유닛)")
    return units
