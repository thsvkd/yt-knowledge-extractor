"""4.5단계: 트랜스크립트 LLM 보정 (깨진 자막 복원, 선택).

업로더 수동 자막이나 유튜브 자동 자막이 깨진 채(오탈자, 구두점 없음, 어절이 잘못 붙거나
끊김, 인식 오류) 채택되는 경우가 있다. 규칙 기반 :mod:`.stage4_clean` 은 공백 정규화만
하므로 이런 손상은 그대로 남는다. 이 단계는 LLM 으로 각 세그먼트의 '텍스트만' 교정한다.

핵심 제약: **타임스탬프·세그먼트 경계는 절대 바꾸지 않는다.** 다운스트림(지식 추출·통합)이
세그먼트 시각으로 원본 딥링크를 만들기 때문이다. 그래서 세그먼트를 ``[MM:SS] 텍스트`` 줄로
직렬화해 LLM 에 보내고, 같은 형식·같은 줄 수로 돌려받아 원래 세그먼트에 위치 순서대로
텍스트만 덮어쓴다. 줄 수가 안 맞거나 호출이 실패한 청크는 원본을 그대로 둔다(안전 폴백).

:mod:`.stage5_extract` 와 같은 청킹/견고성 패턴을 따르며, 청크(=LLM 호출) 경계마다
``should_stop`` 을 확인해 협조적으로 멈춘다(:class:`~yke.utils.StoppedError`).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ..models import Segment
from ..utils import StoppedError, fmt_ts

# 청크(LLM 호출)를 동시에 몇 개까지 돌릴지 기본값(llm_cfg.repair_concurrency 로 덮어씀).
_DEFAULT_CONCURRENCY = 4

_SYSTEM = """당신은 영상 자막(트랜스크립트)의 손상을 교정하는 도구입니다.
입력은 `[MM:SS] 텍스트` 형식의 여러 줄입니다. 자동/수동 자막이 깨진 경우(구두점 없음,
어절이 잘못 붙거나 끊김, 오탈자, 음성 인식 오류)를 자연스러운 문장으로 다듬습니다.

엄격한 규칙:
- 줄 수, 줄 순서, 각 줄의 [MM:SS] 타임스탬프를 절대 바꾸지 마세요(그대로 복사).
- 각 줄의 '텍스트' 부분만 교정합니다. 없는 내용을 지어내지 말고, 있는 말을 다듬기만 하세요.
- 줄을 합치거나 나누지 말고, 요약·삭제하지 마세요(입력 줄 하나 = 출력 줄 하나).
- 출력은 입력과 같은 `[MM:SS] 텍스트` 형식의 줄들만. 다른 설명을 절대 덧붙이지 마세요."""

# 반환 줄에서 앞의 [MM:SS] / [H:MM:SS] 마커를 떼어내 순수 텍스트만 얻는다.
_LINE = re.compile(r"^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s?(.*)$")


def _chunk(segments: list[Segment], max_chars: int) -> list[tuple[int, list[str]]]:
    """세그먼트를 ``(시작 인덱스, [MM:SS] 줄들)`` 청크로 나눈다(원본 인덱스 보존)."""
    chunks: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    size = 0
    start = 0
    for i, seg in enumerate(segments):
        line = f"[{fmt_ts(seg.start)}] {seg.text}"
        if size + len(line) > max_chars and cur:
            chunks.append((start, cur))
            cur, size, start = [], 0, i
        cur.append(line)
        size += len(line) + 1
    if cur:
        chunks.append((start, cur))
    return chunks


def _parse_lines(text: str) -> list[str]:
    """LLM 응답을 줄 단위 텍스트 목록으로 파싱한다([MM:SS] 마커 제거, 빈 줄 무시)."""
    out: list[str] = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        m = _LINE.match(s)
        out.append(m.group(1).strip() if m else s)
    return out


def _resolve_concurrency(llm_cfg, n_chunks: int) -> int:
    """동시 실행 청크 수를 정한다(설정값 우선, 최소 1, 청크 수 이하)."""
    workers = getattr(llm_cfg, "repair_concurrency", None) or _DEFAULT_CONCURRENCY
    return max(1, min(int(workers), n_chunks))


def repair_segments(
    segments: list[Segment],
    llm_cfg,
    client,
    *,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
) -> list[Segment]:
    """세그먼트 텍스트를 LLM 으로 교정해 새 리스트를 반환한다(타임스탬프 보존).

    청크(LLM 호출)들은 서로 독립적이라 스레드풀로 **동시에** 처리한다 — 보정 시간의 대부분이
    순차 왕복 대기라, 이렇게 하면 벽시계 시간이 크게 준다(동시성은 llm_cfg.repair_concurrency).
    실패/불일치 청크는 원본을 유지하므로 최악의 경우에도 입력과 동일한 결과가 나온다.

    Raises:
        StoppedError: 시작 시 또는 결과 취합 도중 ``should_stop()`` 이 True 가 됐을 때.
    """
    if not segments:
        return segments
    if should_stop():
        raise StoppedError()
    chunks = _chunk(segments, llm_cfg.max_chars_per_chunk)
    repaired = list(segments)  # 위치별로 텍스트만 교체한다(원본 리스트는 건드리지 않음)
    total = len(chunks)

    def _call(lines: list[str]) -> str:
        return client.complete(_SYSTEM, "다음 자막을 교정하세요:\n\n" + "\n".join(lines), model=llm_cfg.model)

    executor = ThreadPoolExecutor(max_workers=_resolve_concurrency(llm_cfg, total))
    # 청크 순서대로 future 를 만들어, 결과도 순서대로(=진행 로그도 순서대로) 취합한다.
    futures = [executor.submit(_call, lines) for _start, lines in chunks]
    try:
        for idx, (fut, (start, lines)) in enumerate(zip(futures, chunks), 1):
            if should_stop():
                # 남은(아직 시작 안 한) 호출은 취소하고, 진행 중인 것은 버린 채 즉시 멈춘다.
                executor.shutdown(wait=False, cancel_futures=True)
                raise StoppedError()
            try:
                parsed = _parse_lines(fut.result())
            except Exception as exc:  # 한 청크 실패가 전체 보정을 무너뜨리지 않도록
                log(f"    보정 청크 {idx}/{total} 실패 → 원본 유지: {type(exc).__name__}: {exc}")
                continue
            if len(parsed) != len(lines):
                # 줄 수가 어긋나면 세그먼트 정렬이 밀려 내용이 뒤섞일 수 있다 → 통째로 원본 유지.
                log(f"    보정 청크 {idx}/{total} 줄 수 불일치({len(parsed)}≠{len(lines)}) → 원본 유지")
                continue
            changed = 0
            for offset, new_text in enumerate(parsed):
                nt = new_text.strip()
                if nt and nt != repaired[start + offset].text:
                    repaired[start + offset] = repaired[start + offset].model_copy(
                        update={"text": nt}
                    )
                    changed += 1
            log(f"    보정 청크 {idx}/{total} 완료 ({changed}/{len(lines)} 줄 교정)")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return repaired
