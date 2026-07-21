"""CLI 진입점 + 재사용 가능한 파이프라인 오케스트레이션.

합의된 7단계 파이프라인:
  0. 영상 선정        -> config/channel.yaml
  1. 오디오+메타      -> stage1_ingest
  2. 자막 확인        -> stage2_subtitles (수동 자막만)
  3. STT              -> stage3_stt (자막 없을 때만)
  4. 텍스트 정제      -> stage4_clean
  5. 원자 단위 추출   -> stage5_extract (LLM)
  6. 통합/문서화      -> stage6_integrate (LLM)

중간 산출물은 data/ 아래에 캐싱되어 재실행 시 이어서 진행한다(force 로 재생성).

오케스트레이션은 :func:`run_pipeline` 하나로 모아 CLI(:func:`main`)와 GUI(gui.py)가
같은 코어를 공유한다. 진행 상황은 :class:`Progress` 이벤트를 콜백으로 흘려 보내고,
취소는 ``should_stop`` 콜러블로 처리한다. STT 는 세그먼트/청크 단위로 확인해 진행 중인
영상 하나의 STT 도중에도 즉시 멈추고(:class:`~.utils.StoppedError`, stage3_stt 참고),
LLM 호출은 API 응답을 기다리는 짧은 단일 요청이라 영상/단계 경계에서만 확인한다.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Config, load_config
from .models import KnowledgeUnit, Segment
from .paths import VideoPaths
from .pipeline import (
    stage1_ingest,
    stage2_subtitles,
    stage3_stt,
    stage4_clean,
    stage5_extract,
    stage6_integrate,
    stage_repair,
)
from .utils import StoppedError, channel_folder_slug, fmt_ts, is_channel_or_playlist_url

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")


def _video_id_from_url(url: str) -> str | None:
    m = _YT_ID.search(url)
    return m.group(1) if m else None


# --- 자막 완전성 검증 --------------------------------------------------------
#
# 유튜브가 제공하는 자막(수동/자동)이 간혹 깨진 채로 온다(예: 10분 영상인데 한 줄만).
# 그런 자막을 그대로 트랜스크립트로 채택하면 실제 발화 내용을 대부분 잃는다. 그래서
# 자막을 소스로 채택하기 전에 '영상 길이를 충분히 커버하는가 + 세그먼트가 한두 줄이
# 아닌가'를 검사해, 미달이면 다음 소스(STT/다른 자막)로 폴백한다.


def _caption_coverage(segs: list[Segment], duration: float) -> float:
    """자막이 영상 길이의 얼마를 커버하는지 0~1 로 반환한다.

    duration 을 모르면(0 이하) 검증할 수 없으므로 1.0(통과)으로 본다.
    """
    if not segs:
        return 0.0
    if duration and duration > 0:
        return min(1.0, max(s.end for s in segs) / duration)
    return 1.0


def _caption_is_usable(
    segs: list[Segment], duration: float, *, min_coverage: float, min_segments: int
) -> bool:
    """자막이 '깨지지 않고' 트랜스크립트로 쓸 만한지 판정한다.

    한 줄짜리(min_segments 미만)이거나, 영상 길이의 min_coverage 미만만 커버하면
    사용 불가로 본다. min_coverage 가 0 이면 커버리지 검사는 건너뛴다.
    """
    if len(segs) < min_segments:
        return False
    if min_coverage > 0 and _caption_coverage(segs, duration) < min_coverage:
        return False
    return True


# --- 진행 이벤트 / 결과 모델 -------------------------------------------------


@dataclass
class Progress:
    """파이프라인 진행 상황 이벤트.

    CLI 는 ``message`` 만 출력하고(단, ``transient`` 는 건너뜀), GUI 는 ``phase``·
    ``done``/``total``·``indeterminate``·``sub_progress`` 로 진행바와 상태 텍스트를
    갱신한다.
    """

    message: str
    level: str = "info"  # info | success | warning | error
    phase: str | None = None  # transcript | extract | integrate | done
    done: int | None = None  # 현재 단계에서 완료한 개수
    total: int | None = None  # 현재 단계의 전체 개수
    indeterminate: bool = False  # 끊을 수 없는 단일 작업(다운로드/STT/LLM) 진행 중
    transient: bool = False  # 스피너용 임시 상태 — 영속 로그(CLI 출력)에는 남기지 않음
    # 현재 진행 중인 단일 작업(예: 한 영상의 STT) 내부의 세부 진행률(0.0~1.0).
    # 있으면 GUI 진행바는 (done + sub_progress) / total 로 계산해, "몇 번째 영상"
    # 뿐 아니라 "그 영상의 어디까지" 도 촘촘히 반영한다.
    sub_progress: float | None = None
    # phase="transcript" 내부의 세부 단계: resolve(채널/재생목록 분석) | ingest(오디오·메타)
    # | subtitles(자막 확인) | stt(STT 변환) | clean(텍스트 정제). GUI 가 트랜스크립트 단계
    # 동안 "지금 뭘 하고 있는지" 를 보여주는 하위 타임라인에 쓴다.
    substep: str | None = None


@dataclass
class VideoRecord:
    """영상 하나의 처리 결과 기록.

    단일/채널 처리 모두 영상마다 하나씩 쌓여, 완료 요약(완료/실패/스킵 집계)과 durable
    리포트(run_report.json)에 쓰인다.
    """

    url: str
    video_id: str | None = None
    # done(완료) | failed(다운로드/STT 실패) | skipped(재생 불가로 사전 스킵)
    status: str = "done"
    source: str | None = None  # manual | auto | stt | cached — 트랜스크립트를 어디서 얻었는지
    fallback_reason: str | None = None  # STT 로 폴백했다면 그 사유
    error_reason: str | None = None  # 실패/스킵 사유(한글 라벨)
    error_detail: str | None = None  # 원본 예외 메시지(디버깅용)
    segments: int = 0
    elapsed_seconds: float = 0.0


@dataclass
class PipelineResult:
    """실행 요약. GUI 가 완료 후 산출물 위치·집계를 보여줄 때 쓴다."""

    video_count: int = 0
    unit_count: int = 0
    concept_count: int = 0
    # 이번 실행이 실제로 산출물을 쓴 폴더(채널/재생목록 입력이면 그 하위 폴더로 정리됐을 수
    # 있음). GUI 의 '저장 폴더 열기' 가 실행 전 추정치 대신 이 값을 우선한다.
    out_dir: Path | None = None
    wiki_path: Path | None = None
    clusters_path: Path | None = None
    failures: list[str] = field(default_factory=list)
    # 영상별 처리 기록(완료/실패/스킵). 완료 요약·리포트의 원천.
    results: list[VideoRecord] = field(default_factory=list)
    # 이번 실행의 총 경과 시간(초). 완료/중단 시 사용자에게 표시한다.
    elapsed_seconds: float = 0.0
    stopped: bool = False


ProgressCB = Callable[[Progress], None]


def _noop(_p: Progress) -> None:  # 기본 콜백(아무것도 안 함)
    pass


def _fmt_hms(seconds: float) -> str:
    """초를 "M:SS" 또는(1시간 이상) "H:MM:SS" 로 표시한다."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# STT 진행 이벤트를 너무 자주 흘리면(세그먼트마다) GUI 갱신이 밀릴 수 있어 최소 간격으로
# 솎아낸다. 마지막(100%) 이벤트는 간격과 무관하게 항상 통과시켜 바가 끝까지 차게 한다.
_STT_PROGRESS_MIN_INTERVAL_S = 0.2


def _make_stt_progress_reporter(
    on_progress: ProgressCB, video_index: int, total_videos: int, url: str
) -> Callable[[float, float], None]:
    """영상 하나의 STT 세부 진행(완료 초/전체 초)을 :class:`Progress` 이벤트로 변환한다.

    ``sub_progress`` 에 0~1 분수를 실어, GUI 가 "몇 번째 영상" 뿐 아니라 그 영상의 STT 가
    어디까지 갔는지까지 반영해 바를 촘촘히 채우게 한다. transient 라 CLI 출력·영속 로그에는
    남지 않는다(스피너/바 전용 갱신).
    """
    last_emit = 0.0

    def _report(done_s: float, total_s: float) -> None:
        nonlocal last_emit
        frac = min(1.0, done_s / total_s) if total_s else 0.0
        now = time.monotonic()
        if frac < 1.0 and now - last_emit < _STT_PROGRESS_MIN_INTERVAL_S:
            return
        last_emit = now
        on_progress(
            Progress(
                message=(
                    f"[{video_index}/{total_videos}] STT 변환 중… "
                    f"{_fmt_hms(done_s)} / {_fmt_hms(total_s)} ({frac:.0%}) — {url}"
                ),
                phase="transcript",
                substep="stt",
                done=video_index - 1,
                total=total_videos,
                transient=True,
                sub_progress=frac,
            )
        )

    return _report


# 트랜스크립트를 어디서 얻었는지(VideoRecord.source) → 사람이 읽는 라벨.
_SOURCE_LABELS = {"manual": "수동 자막", "auto": "자동 자막", "stt": "로컬 STT", "cached": "캐시"}


def _emit_summary(results: list[VideoRecord], elapsed: float, on_progress: ProgressCB) -> None:
    """영상별 처리 결과(완료/실패/스킵)와 총 경과 시간을 요약해 로그로 흘린다.

    단일 영상이면 1건, 채널이면 전체 목록의 집계가 된다("최종 채널 처리 완료 결과").
    """
    done = [r for r in results if r.status == "done"]
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]
    on_progress(
        Progress(
            message=(
                f"처리 요약 — 총 {len(results)}개 · 완료 {len(done)}개 · "
                f"실패 {len(failed)}개 · 스킵 {len(skipped)}개 · 경과 {_fmt_hms(elapsed)}"
            ),
            level="warning" if (failed or skipped) else "success",
            phase="done",
        )
    )
    for r in done:
        src = _SOURCE_LABELS.get(r.source or "", r.source or "?")
        extra = f" · 폴백: {r.fallback_reason}" if r.fallback_reason else ""
        on_progress(
            Progress(
                message=f"  ✔ [{r.video_id}] {src} · {_fmt_hms(r.elapsed_seconds)}{extra}",
                phase="done",
            )
        )
    for r in skipped:
        on_progress(
            Progress(
                message=f"  ⏭ [{r.video_id or r.url}] 스킵: {r.error_reason}",
                level="warning",
                phase="done",
            )
        )
    for r in failed:
        detail = f" — {r.error_detail}" if r.error_detail else ""
        on_progress(
            Progress(
                message=f"  ✘ [{r.video_id or r.url}] 실패: {r.error_reason}{detail}",
                level="error",
                phase="done",
            )
        )


def _write_report(out_dir: Path, results: list[VideoRecord], elapsed: float) -> None:
    """영상별 처리 기록을 out_dir/run_report.json 에 durable 하게 남긴다(실패는 무시)."""
    try:
        report = {
            "elapsed_seconds": round(elapsed, 3),
            "total": len(results),
            "done": sum(1 for r in results if r.status == "done"),
            "failed": sum(1 for r in results if r.status == "failed"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
            "videos": [asdict(r) for r in results],
        }
        (out_dir / "run_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # 리포트 기록 실패가 실행 자체를 막지 않도록
        pass


def _resolve_sources(
    sources: list[str],
    channel_limit: int | None,
    failures: list[str],
    on_progress: ProgressCB,
    should_stop: Callable[[], bool],
) -> list[stage1_ingest.VideoEntry]:
    """채널/재생목록 URL 을 최근 영상 :class:`VideoEntry` 로 확장하고, 개별 영상은 그대로 둔다.

    확장 실패는 ``failures`` 에 기록해 배치 전체를 막지 않는다. 결과는 순서를 보존해
    중복 제거한다(같은 영상을 채널+개별로 함께 넣어도 한 번만 처리). 각 항목은 채널
    분석 때 알아낸 ``availability`` 를 실어, 재생 불가 영상을 영상별 처리에서 재조회
    없이 곧바로 스킵할 수 있게 한다.
    """
    resolved: list[stage1_ingest.VideoEntry] = []
    for src in sources:
        if should_stop():
            break
        if not is_channel_or_playlist_url(src):
            resolved.append(stage1_ingest.VideoEntry(url=src))
            continue
        on_progress(
            Progress(
                message=f"채널/재생목록 분석 중… {src}",
                phase="transcript",
                substep="resolve",
                indeterminate=True,
                transient=True,
            )
        )
        try:
            found = stage1_ingest.expand_source(
                src,
                channel_limit,
                log=lambda m: on_progress(Progress(message=m, phase="transcript", substep="resolve")),
            )
        except Exception as exc:
            on_progress(
                Progress(
                    message=f"[{src}] 채널/재생목록 분석 실패: {type(exc).__name__}: {exc}",
                    level="error",
                    phase="transcript",
                    substep="resolve",
                )
            )
            failures.append(src)
            continue
        on_progress(
            Progress(
                message=f"채널/재생목록에서 {len(found)}개 영상 확보: {src}",
                level="success",
                phase="transcript",
                substep="resolve",
            )
        )
        resolved.extend(found)
    seen: set[str] = set()
    deduped: list[stage1_ingest.VideoEntry] = []
    for entry in resolved:
        key = entry.video_id or entry.url
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    return deduped


# --- 트랜스크립트 산출물(JSON + 사람이 읽는 TXT) -----------------------------
#
# 산출물 규칙:
#   • transcript.raw.json / transcript.raw.txt — 원본(자막·STT + 규칙 정제). 항상 생성.
#   • transcript.json     / transcript.txt     — LLM 보정본. 자막 보정을 켰을 때만 생성.
# 다운스트림(추출·통합)은 보정본이 있으면 그걸, 없으면 원본을 쓴다. 캐시 판정도 원본
# (transcript.raw.json) 기준이다. 보정 기능 이전 데이터(transcript.json 만 있고 raw 가 없는
# 경우)는 그 transcript.json 을 원본으로 간주해 재다운로드를 피한다(_load_cached_raw).


def _segments_to_text(segs: list[Segment]) -> str:
    """세그먼트를 사람이 읽는 ``[MM:SS] 텍스트`` 줄들로 직렬화한다."""
    return "".join(f"[{fmt_ts(s.start)}] {s.text}\n" for s in segs)


def _write_transcript(json_path: Path, txt_path: Path, segs: list[Segment]) -> None:
    """트랜스크립트를 JSON(정본)과 TXT(읽기용)로 함께 쓴다."""
    json_path.write_text(
        json.dumps([s.model_dump() for s in segs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    txt_path.write_text(_segments_to_text(segs), encoding="utf-8")


def _load_cached_raw(vp: VideoPaths) -> list[Segment] | None:
    """캐시된 '원본' 트랜스크립트를 로드한다(raw.json 우선, 레거시 transcript.json 폴백)."""
    for p in (vp.transcript_raw, vp.transcript):
        if p.exists():
            return [Segment(**s) for s in json.loads(p.read_text(encoding="utf-8"))]
    return None


def build_transcript(
    url: str,
    cfg: Config,
    data_dir: Path,
    force: bool,
    *,
    log: Callable[[str], None] = print,
    on_progress: ProgressCB = _noop,
    on_stt_progress: stage3_stt.ProgressCB | None = None,
    should_stop: Callable[[], bool] = lambda: False,
):
    """1~4단계: 영상 하나의 트랜스크립트를 생성/로딩한다.

    ``log`` 로 진행 메시지를 흘려 CLI 는 stdout 에, GUI 는 로그 뷰에 보이게 한다.
    ``on_progress`` 는 ingest/subtitles/stt/clean 경계마다 ``substep`` 이 실린 이벤트를
    흘려, GUI 가 트랜스크립트 단계 내부의 세부 타임라인(칩)을 실시간으로 그릴 수 있게
    한다. 같은 경계에서 ``log`` 로도 한 줄 남기므로, 채널처럼 여러 영상을 연달아 처리할
    때도 로그에 단계별 흐름이 그대로 남는다.
    ``on_stt_progress`` 는 STT 가 실제로 돌 때만(자막 채택 시엔 호출되지 않음) 세그먼트
    단위로 (완료 초, 전체 초)를 알려 GUI 진행바를 세밀하게 갱신할 수 있게 한다.
    ``should_stop`` 은 STT 에 그대로 전달되어 세그먼트/청크 단위로 확인되며, True 가 되면
    이 영상의 STT 가 끝나길 기다리지 않고 :class:`StoppedError` 로 즉시 전파된다.
    """

    def _substep(name: str, message: str) -> None:
        log(message)
        on_progress(
            Progress(message=message, phase="transcript", substep=name, indeterminate=True, transient=True)
        )

    # 완전 캐시된 경우 URL 에서 id 를 뽑아 네트워크 조회 없이 로딩(오프라인 재실행 가능)
    cached_id = _video_id_from_url(url)
    if cached_id and not force:
        vp = VideoPaths(data_dir, cached_id)
        if vp.meta.exists():
            segs = _load_cached_raw(vp)
            if segs is not None:
                meta = json.loads(vp.meta.read_text(encoding="utf-8"))
                return cached_id, meta, segs

    _substep("ingest", f"메타·자막 정보 확인 중… {url}")
    info = stage1_ingest.probe(url, log=log)
    vid = info["id"]
    vp = VideoPaths(data_dir, vid)
    meta = stage1_ingest.build_meta(info, vp, cfg.language, cfg.subtitles)

    if not force:
        segs = _load_cached_raw(vp)
        if segs is not None:
            return vid, meta, segs

    # 트랜스크립트 소스 우선순위(기본, cfg.subtitles.stt_first=False):
    #   ① 업로더 제공 수동 자막 > ② 유튜브 자동 생성 자막 > ③ 로컬 STT(AI 모델, CPU/GPU).
    # 자막(①②)은 오디오 없이 .vtt 만 lazy 다운로드하므로 자막으로 처리되는 영상은 오디오
    # 원본을 아예 받지 않아 빠르다. 로컬 STT(③)는 자원을 쓰는 마지막 수단이므로, 그때
    # 비로소 오디오를 내려받는다. 자막은 채택 전에 완전성을 검증해 '깨진 자막'(예: 10분
    # 영상에 한 줄)을 걸러 다음 소스로 넘긴다. stt_first=True 로 두면(레거시) STT 강제.
    subs = cfg.subtitles
    duration = float(meta.get("duration") or 0)
    # STT 로 폴백하게 된 사유(수동/자동 자막이 왜 못 쓰였는지)를 모아, STT 채택 시 기록한다.
    fallback_reasons: list[str] = []

    def _from_stt() -> list[Segment] | None:
        reason = (
            "강제 로컬 STT(자막 무시)"
            if subs.stt_first
            else ("자막 폴백: " + ", ".join(fallback_reasons) if fallback_reasons else "자막 사용 불가")
        )
        log(f"[{vid}] 로컬 STT 로 폴백 — 사유: {reason}")
        _substep("stt", f"[{vid}] 오디오 다운로드 중… ({reason})")
        try:
            audio = stage1_ingest.download_audio(url, vp, force=force, log=log)
        except Exception as exc:
            log(f"[{vid}] 오디오 다운로드 실패: {type(exc).__name__}: {exc}")
            return None
        try:
            engine = cfg.stt.engine
            engine_desc = cfg.stt.model if engine == "faster-whisper" else f"vosk/{cfg.stt.vosk_model_size}"
            _substep("stt", f"[{vid}] STT 실행 ({engine} {engine_desc})...")
            segs = stage3_stt.transcribe(
                audio,
                cfg.language,
                cfg.stt,
                log=log,
                on_progress=on_stt_progress,
                should_stop=should_stop,
            )
            meta["transcript_source"] = "stt"
            meta["fallback_reason"] = reason
            return segs
        except StoppedError:
            # 중단 요청은 STT 실패가 아니므로 다음 소스(수동/자동 자막)로 넘기지 않고
            # build_transcript 호출자(run_pipeline)까지 그대로 전파한다.
            raise
        except Exception as exc:
            log(f"[{vid}] STT 실패: {type(exc).__name__}: {exc}")
            return None

    def _accept_caption(parsed: list[Segment], kind: str, name: str) -> list[Segment] | None:
        if not _caption_is_usable(
            parsed,
            duration,
            min_coverage=subs.min_coverage_ratio,
            min_segments=subs.min_caption_segments,
        ):
            log(
                f"[{vid}] {kind} 자막이 불완전(세그먼트 {len(parsed)}개, "
                f"커버리지 {_caption_coverage(parsed, duration):.0%}) → 건너뜁니다: {name}"
            )
            return None
        log(f"[{vid}] {kind} 자막 사용: {name} ({len(parsed)} 세그먼트)")
        return parsed

    def _from_manual() -> list[Segment] | None:
        if not subs.use_manual:
            return None
        if not meta.get("manual_sub_lang"):
            fallback_reasons.append("업로더 수동 자막 없음")
            return None
        _substep("subtitles", f"[{vid}] 수동 자막 다운로드 중… (lang={meta['manual_sub_lang']})")
        sub = stage1_ingest.download_manual_subtitle(url, vp, meta["manual_sub_lang"], log=log)
        if not sub:
            fallback_reasons.append("수동 자막 다운로드 실패")
            return None
        result = _accept_caption(stage2_subtitles.parse_vtt(sub), "수동", sub.name)
        if result is None:
            fallback_reasons.append("수동 자막 불완전")
        else:
            # transcript.json 에 이미 다 파싱해 넣었으니, 원본 .vtt 는 채택된 뒤로는 더 이상
            # 안 읽는다(재실행해도 transcript.json 캐시 히트로 다시 안 열어봄) — 지운다.
            meta["transcript_source"] = "manual"
            sub.unlink(missing_ok=True)
        return result

    def _from_auto() -> list[Segment] | None:
        if not subs.use_auto_fallback:
            return None
        if not meta.get("auto_sub_lang"):
            fallback_reasons.append("유튜브 자동자막 없음")
            return None
        _substep("subtitles", f"[{vid}] 유튜브 자동자막 다운로드 중… (lang={meta['auto_sub_lang']})")
        sub = stage1_ingest.download_auto_subtitle(url, vp, meta["auto_sub_lang"], log=log)
        if not sub:
            fallback_reasons.append("자동자막 다운로드 실패")
            return None
        parsed = stage2_subtitles.parse_vtt(sub, collapse_rollup=True)
        result = _accept_caption(parsed, "자동", sub.name)
        if result is None:
            fallback_reasons.append("자동자막 불완전")
        else:
            meta["transcript_source"] = "auto"
            sub.unlink(missing_ok=True)
        return result

    sources = (
        [_from_stt, _from_manual, _from_auto]
        if subs.stt_first
        else [_from_manual, _from_auto, _from_stt]
    )
    segs: list[Segment] | None = None
    for source in sources:
        segs = source()
        if segs:
            break

    if not segs:
        raise RuntimeError(
            f"[{vid}] 트랜스크립트를 확보하지 못했습니다 (STT·수동·자동 자막 모두 실패하거나 불완전)."
        )

    _substep("clean", f"[{vid}] 텍스트 정제 중…")
    segs = stage4_clean.clean_segments(segs)
    # 원본 트랜스크립트를 JSON + TXT 로 남긴다(항상). 보정본은 이후 stage_repair 가 별도 파일로.
    _write_transcript(vp.transcript_raw, vp.transcript_raw_txt, segs)
    # 채택된 소스/폴백 사유를 meta.json 에도 남겨(재실행 캐시·요약에 사용) 갱신 저장한다.
    vp.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return vid, meta, segs


def run_pipeline(
    videos: list[str],
    cfg: Config,
    *,
    data_dir: str | Path | None = None,
    out_dir: str | Path | None = None,
    force: bool = False,
    stage: str = "all",
    channel_limit: int | None = None,
    on_progress: ProgressCB = _noop,
    should_stop: Callable[[], bool] = lambda: False,
) -> PipelineResult:
    """전체 파이프라인을 실행하고 :class:`PipelineResult` 를 반환한다.

    Args:
        videos: 대상 유튜브 URL 목록.
        cfg: 로딩된 설정(언어/STT/LLM 등).
        data_dir/out_dir: 미지정 시 cfg 값을 사용.
        stage: "transcript" | "extract" | "integrate" | "all". (integrate == all)
        on_progress: 진행 이벤트 콜백.
        should_stop: True 를 반환하면 다음 경계에서 협조적으로 중단한다.

    Raises:
        RuntimeError: 처리된 영상이 하나도 없거나(모든 영상 실패) LLM 자격증명이 없을 때.
    """
    t0 = time.monotonic()  # 총 경과 시간 측정 시작(완료/중단 시 표시).
    data_dir = Path(data_dir if data_dir is not None else cfg.data_dir)
    out_dir = Path(out_dir if out_dir is not None else cfg.output_dir)

    # 채널/재생목록 입력이면 그 채널 전용 하위 폴더로 정리해, 여러 채널을 반복 처리해도
    # 캐시·산출물이 저장 폴더에 뒤섞이지 않게 한다. 소스가 섞여 있으면 첫 채널/재생목록
    # 하나만 기준으로 삼는다(개별 영상만 있으면 기존처럼 저장 폴더에 바로 정리).
    channel_name = next((n for v in videos if (n := channel_folder_slug(v))), None)
    if channel_name:
        data_dir = data_dir / channel_name
        out_dir = out_dir / channel_name
        on_progress(Progress(message=f"채널 폴더로 정리: {out_dir}", phase="transcript"))
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 0단계: 채널/재생목록 URL 을 최근 N개 영상으로 확장 ---
    failures: list[str] = []
    results: list[VideoRecord] = []
    videos = _resolve_sources(videos, channel_limit, failures, on_progress, should_stop)

    def _stopped_now(elapsed: float) -> PipelineResult:
        _emit_summary(results, elapsed, on_progress)
        _write_report(out_dir, results, elapsed)
        return PipelineResult(
            video_count=len(transcripts),
            out_dir=out_dir,
            failures=failures,
            results=results,
            elapsed_seconds=elapsed,
            stopped=True,
        )

    # --- 1~4단계: 트랜스크립트 ---
    metas: dict[str, dict] = {}
    transcripts: dict[str, list[Segment]] = {}
    total = len(videos)
    for i, entry in enumerate(videos, start=1):
        url = entry.url
        if should_stop():
            return _stopped_now(time.monotonic() - t0)

        # 채널 분석에서 이미 재생 불가로 확인된 영상(멤버 전용/비공개 등)은 영상별 재조회
        # (probe/다운로드) 없이 곧바로 스킵한다 — 같은 판정을 두 번 하지 않는다("빠른 스킵").
        skip = stage1_ingest.unplayable_reason(entry.availability)
        if skip:
            _code, label = skip
            results.append(
                VideoRecord(
                    url=url,
                    video_id=entry.video_id,
                    status="skipped",
                    error_reason=label,
                    error_detail=f"availability={entry.availability}",
                )
            )
            failures.append(url)
            on_progress(
                Progress(
                    message=f"[{i}/{total}] 재생 불가로 스킵: {label} — {entry.video_id or url}",
                    level="warning",
                    phase="transcript",
                    done=i,
                    total=total,
                )
            )
            continue

        on_progress(
            Progress(
                message=f"[{i}/{total}] 트랜스크립트 처리 중… {url}",
                phase="transcript",
                done=i - 1,
                total=total,
                indeterminate=True,
                transient=True,
            )
        )
        t_video = time.monotonic()
        try:
            vid, meta, segs = build_transcript(
                url,
                cfg,
                data_dir,
                force,
                log=lambda m: on_progress(Progress(message=m, phase="transcript")),
                on_progress=on_progress,
                on_stt_progress=_make_stt_progress_reporter(on_progress, i, total, url),
                should_stop=should_stop,
            )
        except StoppedError:
            # 이 영상의 STT 도중 중단 요청을 감지했다 — 실패로 기록하지 않고 즉시 멈춘다.
            elapsed = time.monotonic() - t0
            on_progress(
                Progress(
                    message=f"[{url}] 중단 요청으로 STT 를 멈췄습니다. (경과 {_fmt_hms(elapsed)})",
                    level="warning",
                    phase="transcript",
                )
            )
            return _stopped_now(elapsed)
        except Exception as exc:  # 한 영상 실패가 배치 전체를 막지 않도록
            _code, label = stage1_ingest.classify_download_failure(exc)
            results.append(
                VideoRecord(
                    url=url,
                    status="failed",
                    error_reason=label,
                    error_detail=f"{type(exc).__name__}: {exc}",
                    elapsed_seconds=time.monotonic() - t_video,
                )
            )
            on_progress(
                Progress(
                    message=f"[{url}] 실패 → 건너뜀: {label} ({type(exc).__name__}: {exc})",
                    level="error",
                    phase="transcript",
                    done=i,
                    total=total,
                )
            )
            failures.append(url)
            continue
        metas[vid] = meta
        transcripts[vid] = segs
        src = meta.get("transcript_source")
        rec = VideoRecord(
            url=url,
            video_id=vid,
            status="done",
            source=src,
            fallback_reason=meta.get("fallback_reason"),
            segments=len(segs),
            elapsed_seconds=time.monotonic() - t_video,
        )
        results.append(rec)
        src_label = _SOURCE_LABELS.get(src or "", src or "?")
        on_progress(
            Progress(
                message=(
                    f"[{vid}] 트랜스크립트 {len(segs)} 세그먼트 · {src_label} · "
                    f"{_fmt_hms(rec.elapsed_seconds)}"
                ),
                level="success",
                phase="transcript",
                done=i,
                total=total,
            )
        )

    # 영상 목록 처리가 끝났다 — 완료/실패/스킵 집계와 총 경과를 요약해 남긴다
    # (단일 영상이면 1건, 채널이면 전체 요약 = "최종 채널 처리 완료 결과").
    _emit_summary(results, time.monotonic() - t0, on_progress)
    _write_report(out_dir, results, time.monotonic() - t0)

    if failures:
        on_progress(
            Progress(
                message=f"경고: {len(failures)}개 영상 실패, 나머지로 계속: {failures}",
                level="warning",
                phase="transcript",
            )
        )
    if not transcripts:
        if failures and not videos:
            raise RuntimeError(f"처리할 영상이 없습니다 (채널/재생목록 분석 실패: {failures}).")
        if failures:
            raise RuntimeError(
                f"처리된 영상이 없습니다. {len(failures)}개 영상이 모두 실패했습니다:\n"
                f"  {failures}\n\n"
                f"가능한 원인:\n"
                f"  • 영상이 삭제되었거나 비공개로 변경됨\n"
                f"  • YouTube 지역 제한 또는 일시적 서버 오류\n"
                f"  • 네트워크 문제 또는 IP 차단\n\n"
                f"위 로그에서 각 영상별 구체적인 에러를 확인하세요."
            )
        raise RuntimeError("처리할 영상이 없습니다.")

    # --- 4.5단계: 트랜스크립트 LLM 보정(선택, cfg.llm.repair_transcript) ---
    # 트랜스크립트를 확보한 직후, 깨진 자막(수동/자동)을 LLM 으로 교정한다. LLM 을 쓰므로
    # 여기서 클라이언트를 처음 만들고(자격증명 없으면 보정만 건너뛴다), 이후 추출/통합
    # 단계에서 재사용한다. transcript 단계까지만 실행해도(스크립트 추출까지) 보정은 적용된다.
    client = None
    if cfg.llm.repair_transcript:
        from .llm import make_client

        try:
            client = make_client(cfg.llm)
        except Exception as exc:
            # 자격증명/설치 미비 → 보정만 건너뛰고 파이프라인은 계속(트랜스크립트는 이미 확보).
            on_progress(
                Progress(
                    message=f"자막 보정 건너뜀 — LLM 준비 안 됨: {exc}",
                    level="warning",
                    phase="transcript",
                    substep="clean",
                )
            )
        if client is not None:
            rtotal = len(transcripts)
            for i, (vid, segs) in enumerate(transcripts.items(), start=1):
                if should_stop():
                    return _stopped_now(time.monotonic() - t0)
                vp = VideoPaths(data_dir, vid)
                meta = metas.get(vid, {})
                # 이미 보정된 캐시가 있으면(재실행) 보정본을 메모리로 로드해 다운스트림이 쓰게
                # 하고, 재보정으로 LLM 을 낭비하지 않는다.
                if not force and meta.get("transcript_repaired") and vp.transcript.exists():
                    transcripts[vid] = [
                        Segment(**s) for s in json.loads(vp.transcript.read_text(encoding="utf-8"))
                    ]
                    continue
                on_progress(
                    Progress(
                        message=f"[{i}/{rtotal}] 자막 보정 중… (LLM) {vid}",
                        phase="transcript",
                        substep="clean",
                        done=i - 1,
                        total=rtotal,
                        indeterminate=True,
                        transient=True,
                    )
                )
                try:
                    fixed = stage_repair.repair_segments(
                        segs,
                        cfg.llm,
                        client,
                        log=lambda m: on_progress(
                            Progress(message=m, phase="transcript", substep="clean")
                        ),
                        should_stop=should_stop,
                    )
                except StoppedError:
                    return _stopped_now(time.monotonic() - t0)
                transcripts[vid] = fixed
                # 원본(transcript.raw.*)은 build_transcript 가 이미 남겼다 — 보정본은 별도
                # 파일(transcript.json/.txt)로 저장해 원본을 보존한다.
                _write_transcript(vp.transcript, vp.transcript_txt, fixed)
                meta["transcript_repaired"] = True
                metas[vid] = meta
                with contextlib.suppress(Exception):
                    vp.meta.write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                on_progress(
                    Progress(
                        message=f"[{vid}] 자막 보정 완료 ({len(fixed)} 세그먼트)",
                        level="success",
                        phase="transcript",
                        substep="clean",
                        done=i,
                        total=rtotal,
                    )
                )

    if should_stop():
        return PipelineResult(
            video_count=len(transcripts),
            out_dir=out_dir,
            failures=failures,
            results=results,
            elapsed_seconds=time.monotonic() - t0,
            stopped=True,
        )

    if stage == "transcript":
        on_progress(
            Progress(
                message=f"트랜스크립트 단계까지 완료. (경과 {_fmt_hms(time.monotonic() - t0)})",
                level="success",
                phase="done",
            )
        )
        return PipelineResult(
            video_count=len(transcripts),
            out_dir=out_dir,
            failures=failures,
            results=results,
            elapsed_seconds=time.monotonic() - t0,
        )

    # LLM 은 5단계부터 필요 -> 지연 초기화(자격증명 없으면 RuntimeError). 보정 단계에서
    # 이미 만들었으면 그 클라이언트를 재사용한다(provider 에 맞는 것으로 팩토리가 선택).
    if client is None:
        from .llm import make_client

        client = make_client(cfg.llm)

    # --- 5단계: 영상별 지식 원자 단위 ---
    all_units: list[KnowledgeUnit] = []
    total_v = len(transcripts)
    for i, (vid, segs) in enumerate(transcripts.items(), start=1):
        if should_stop():
            return PipelineResult(
                video_count=len(transcripts),
                unit_count=len(all_units),
                out_dir=out_dir,
                failures=failures,
                results=results,
                elapsed_seconds=time.monotonic() - t0,
                stopped=True,
            )
        vp = VideoPaths(data_dir, vid)
        if vp.units.exists() and not force:
            units = [KnowledgeUnit(**u) for u in json.loads(vp.units.read_text(encoding="utf-8"))]
        else:
            on_progress(
                Progress(
                    message=f"[{vid}] 지식 원자 단위 추출...",
                    phase="extract",
                    done=i - 1,
                    total=total_v,
                    indeterminate=True,
                )
            )
            units = stage5_extract.extract_units(segs, vid, cfg.llm, client)
            vp.units.write_text(
                json.dumps([u.model_dump() for u in units], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        on_progress(
            Progress(
                message=f"[{vid}] {len(units)} 유닛",
                level="success",
                phase="extract",
                done=i,
                total=total_v,
            )
        )
        all_units.extend(units)

    if stage == "extract":
        on_progress(
            Progress(
                message=f"추출 단계까지 완료. (경과 {_fmt_hms(time.monotonic() - t0)})",
                level="success",
                phase="done",
            )
        )
        return PipelineResult(
            video_count=len(transcripts),
            unit_count=len(all_units),
            out_dir=out_dir,
            failures=failures,
            results=results,
            elapsed_seconds=time.monotonic() - t0,
        )

    if should_stop():
        return PipelineResult(
            video_count=len(transcripts),
            unit_count=len(all_units),
            out_dir=out_dir,
            failures=failures,
            results=results,
            elapsed_seconds=time.monotonic() - t0,
            stopped=True,
        )

    # --- 6단계: 통합 + 마크다운 ---
    on_progress(
        Progress(
            message=f"통합 중... 총 {len(all_units)} 유닛",
            phase="integrate",
            indeterminate=True,
        )
    )
    clusters = stage6_integrate.integrate(all_units, cfg.llm, client)
    clusters_path = out_dir / "clusters.json"
    clusters_path.write_text(
        json.dumps([c.model_dump() for c in clusters], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = stage6_integrate.render_markdown(clusters, metas)
    wiki_path = out_dir / "wiki.md"
    wiki_path.write_text(md, encoding="utf-8")
    elapsed = time.monotonic() - t0
    on_progress(
        Progress(
            message=f"완료: {wiki_path} (개념 {len(clusters)}개, 경과 {_fmt_hms(elapsed)})",
            level="success",
            phase="done",
            done=1,
            total=1,
        )
    )
    return PipelineResult(
        video_count=len(transcripts),
        unit_count=len(all_units),
        concept_count=len(clusters),
        out_dir=out_dir,
        wiki_path=wiki_path,
        clusters_path=clusters_path,
        failures=failures,
        results=results,
        elapsed_seconds=elapsed,
    )


def main() -> None:
    # cp949 콘솔로 리다이렉트된 CLI 에서 인코딩 불가 문자(이모지 등)로 진행 로그 print 가
    # 죽지 않도록 대체 출력으로 바꾼다(한글은 cp949 로 그대로 나가고, 불가 문자만 대체).
    import sys

    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="유튜브 채널 지식 문서화 PoC")
    ap.add_argument("--config", default="config/channel.yaml")
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 재생성")
    ap.add_argument(
        "--stage",
        choices=["transcript", "extract", "integrate", "all"],
        default="all",
        help="어디까지 실행할지",
    )
    ap.add_argument(
        "--stt-model",
        default=None,
        help="config 의 stt.model 을 이번 실행에 한해 덮어씀 (예: medium, small)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="채널/재생목록 URL 을 넣었을 때 처리할 최근 영상 수 (개별 영상 URL 엔 무관)",
    )
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.stt_model:
        cfg.stt.model = args.stt_model

    def on_progress(p: Progress) -> None:
        # 스피너용 임시 상태는 CLI 출력에서 건너뛰어 기존 stdout 동작을 보존한다.
        if not p.transient:
            print(p.message)

    run_pipeline(
        cfg.videos,
        cfg,
        force=args.force,
        stage=args.stage,
        channel_limit=args.limit,
        on_progress=on_progress,
    )


if __name__ == "__main__":
    main()
