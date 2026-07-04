"""6단계: 영상 간 통합 (클러스터링 -> 중복제거 -> 상충 플래깅 -> 마크다운).

구조화된 데이터(지식 원자 단위)를 병합하므로 서술형 문서 병합보다 안정적이다.
PoC 규모(5~10개)에서는 결과를 사람이 직접 검토하는 것을 전제로 한다.
"""

from __future__ import annotations

import json

from ..models import ConceptCluster, KnowledgeUnit
from ..utils import parse_json_array, ts_to_seconds

_SYSTEM = """당신은 여러 영상에서 추출된 '지식 원자 단위'들을 통합하는 도구입니다.
같은 concept 끼리 클러스터링하고, 중복을 제거하고, 상충하는 내용을 플래깅합니다.

출력 JSON 스키마 (배열):
[
  {
    "concept": "개념명",
    "summary": "이 개념에 대한 통합 설명 (2~4문장)",
    "points": [
      {
        "statement": "명제",
        "type": "fact|opinion|tip|definition",
        "sources": [{"video_id": "...", "timestamp": "MM:SS"}]
      }
    ],
    "conflicts": ["상충하거나 검토가 필요한 내용 설명", "..."]
  }
]

규칙:
- 입력에 없는 내용을 새로 생성하지 않습니다 (할루시네이션 금지).
- 같은 취지의 명제는 하나로 합치되 출처(sources)는 모두 보존합니다.
- 사실(fact)과 의견(opinion)이 충돌하면 conflicts 에 명시합니다.
- 출력은 오직 JSON 배열만. 다른 설명 텍스트를 절대 덧붙이지 마세요."""

_TYPE_BADGE = {"fact": "사실", "opinion": "의견", "tip": "팁", "definition": "정의"}


def _source_link(s, meta_by_id: dict) -> str:
    """출처 딥링크. title 이 None 이어도 안전하고, 링크 텍스트의 대괄호를 무력화한다."""
    meta = meta_by_id.get(s.video_id) or {}
    title = (meta.get("title") or s.video_id)[:20]
    title = title.replace("[", "(").replace("]", ")")
    return f"[{title} @ {s.timestamp}](https://youtu.be/{s.video_id}?t={ts_to_seconds(s.timestamp)})"


def integrate(all_units: list[KnowledgeUnit], llm_cfg, client) -> list[ConceptCluster]:
    if not all_units:
        return []
    payload = json.dumps([u.model_dump() for u in all_units], ensure_ascii=False, indent=1)
    user = f"다음 지식 원자 단위들을 통합하세요:\n\n{payload}"
    text = client.complete(
        _SYSTEM, user, model=llm_cfg.model, max_tokens=16000, stream=True
    )
    parsed = parse_json_array(text)
    if not parsed and text.strip():
        print(
            "  경고: 통합 응답을 JSON 배열로 파싱하지 못했습니다"
            " (max_tokens 잘림/형식 오류 가능) — clusters 가 비게 됩니다."
        )
    clusters: list[ConceptCluster] = []
    for obj in parsed:
        if not isinstance(obj, dict):
            continue
        try:
            clusters.append(ConceptCluster(**obj))
        except Exception:
            continue
    return clusters


def render_markdown(clusters: list[ConceptCluster], meta_by_id: dict) -> str:
    lines: list[str] = ["# 지식베이스", ""]
    lines.append(f"> 개념 {len(clusters)}개 · 영상 {len(meta_by_id)}개 기반 · 사람 검토 필요(PoC)")
    lines.append("")

    for c in clusters:
        lines.append(f"## {c.concept}")
        lines.append("")
        if c.summary:
            lines.append(c.summary)
            lines.append("")
        for p in c.points:
            badge = _TYPE_BADGE.get(p.type, p.type)
            srcs = ", ".join(_source_link(s, meta_by_id) for s in p.sources)
            src_note = f"  \n  <sub>출처: {srcs}</sub>" if srcs else ""
            lines.append(f"- **[{badge}]** {p.statement}{src_note}")
        if c.conflicts:
            lines.append("")
            lines.append("> ⚠️ **상충 / 검토 필요**")
            for cf in c.conflicts:
                lines.append(f"> - {cf}")
        lines.append("")

    return "\n".join(lines)
