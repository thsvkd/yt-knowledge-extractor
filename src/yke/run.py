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
취소는 ``should_stop`` 콜러블로 영상/단계 경계에서 협조적으로 처리한다(진행 중인
단일 영상의 STT·LLM 호출은 중간에 끊지 않고 다음 경계에서 멈춘다).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
)
from .utils import is_channel_or_playlist_url

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


@dataclass
class PipelineResult:
    """실행 요약. GUI 가 완료 후 산출물 위치·집계를 보여줄 때 쓴다."""

    video_count: int = 0
    unit_count: int = 0
    concept_count: int = 0
    wiki_path: Path | None = None
    clusters_path: Path | None = None
    failures: list[str] = field(default_factory=list)
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
                done=video_index - 1,
                total=total_videos,
                transient=True,
                sub_progress=frac,
            )
        )

    return _report


def _resolve_sources(
    sources: list[str],
    channel_limit: int | None,
    failures: list[str],
    on_progress: ProgressCB,
    should_stop: Callable[[], bool],
) -> list[str]:
    """채널/재생목록 URL 을 최근 영상 URL 로 확장하고, 개별 영상은 그대로 둔다.

    확장 실패는 ``failures`` 에 기록해 배치 전체를 막지 않는다. 결과는 순서를 보존해
    중복 제거한다(같은 영상을 채널+개별로 함께 넣어도 한 번만 처리).
    """
    resolved: list[str] = []
    for src in sources:
        if should_stop():
            break
        if not is_channel_or_playlist_url(src):
            resolved.append(src)
            continue
        on_progress(
            Progress(
                message=f"채널/재생목록 분석 중… {src}",
                phase="transcript",
                indeterminate=True,
                transient=True,
            )
        )
        try:
            found = stage1_ingest.expand_source(
                src,
                channel_limit,
                log=lambda m: on_progress(Progress(message=m, phase="transcript")),
            )
        except Exception as exc:
            on_progress(
                Progress(
                    message=f"[{src}] 채널/재생목록 분석 실패: {type(exc).__name__}: {exc}",
                    level="error",
                    phase="transcript",
                )
            )
            failures.append(src)
            continue
        on_progress(
            Progress(
                message=f"채널/재생목록에서 {len(found)}개 영상 확보: {src}",
                level="success",
                phase="transcript",
            )
        )
        resolved.extend(found)
    seen: set[str] = set()
    deduped: list[str] = []
    for url in resolved:
        if url not in seen:
            seen.add(url)
            deduped.append(url)
    return deduped


def build_transcript(
    url: str,
    cfg: Config,
    data_dir: Path,
    force: bool,
    *,
    log: Callable[[str], None] = print,
    on_stt_progress: stage3_stt.ProgressCB | None = None,
):
    """1~4단계: 영상 하나의 트랜스크립트를 생성/로딩한다.

    ``log`` 로 진행 메시지를 흘려 CLI 는 stdout 에, GUI 는 로그 뷰에 보이게 한다.
    ``on_stt_progress`` 는 STT 가 실제로 돌 때만(자막 채택 시엔 호출되지 않음) 세그먼트
    단위로 (완료 초, 전체 초)를 알려 GUI 진행바를 세밀하게 갱신할 수 있게 한다.
    """
    # 완전 캐시된 경우 URL 에서 id 를 뽑아 네트워크 조회 없이 로딩(오프라인 재실행 가능)
    cached_id = _video_id_from_url(url)
    if cached_id and not force:
        vp = VideoPaths(data_dir, cached_id)
        if vp.transcript.exists() and vp.meta.exists():
            meta = json.loads(vp.meta.read_text(encoding="utf-8"))
            segs = [Segment(**s) for s in json.loads(vp.transcript.read_text(encoding="utf-8"))]
            return cached_id, meta, segs

    info = stage1_ingest.probe(url)
    vid = info["id"]
    vp = VideoPaths(data_dir, vid)
    meta = stage1_ingest.ingest(url, info, vp, cfg.language, cfg.subtitles, force)

    if vp.transcript.exists() and not force:
        segs = [Segment(**s) for s in json.loads(vp.transcript.read_text(encoding="utf-8"))]
        return vid, meta, segs

    # 트랜스크립트 소스 우선순위. 기본은 STT(실제 발화 받아쓰기)를 1순위로 두고, 실패/불가
    # 시에만 자막으로 폴백한다(cfg.subtitles.stt_first). 자막은 채택 전에 완전성을 검증해
    # '깨진 자막'(예: 10분 영상에 한 줄)을 걸러 다음 소스로 넘긴다.
    subs = cfg.subtitles
    duration = float(meta.get("duration") or 0)

    def _from_stt() -> list[Segment] | None:
        audio = vp.audio()
        if audio is None:
            log(f"[{vid}] 오디오 파일을 찾을 수 없어 STT 를 건너뜁니다.")
            return None
        try:
            log(f"[{vid}] STT 실행 (faster-whisper {cfg.stt.model})...")
            return stage3_stt.transcribe(
                audio, cfg.language, cfg.stt, log=log, on_progress=on_stt_progress
            )
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
        if not subs.use_manual or not meta.get("manual_sub_lang"):
            return None
        sub = vp.root / f"audio.{meta['manual_sub_lang']}.vtt"
        if not sub.exists():
            return None
        return _accept_caption(stage2_subtitles.parse_vtt(sub), "수동", sub.name)

    def _from_auto() -> list[Segment] | None:
        if not subs.use_auto_fallback or not meta.get("auto_sub_lang"):
            return None
        log(f"[{vid}] 유튜브 자동자막 다운로드 (lang={meta['auto_sub_lang']})...")
        sub = stage1_ingest.download_auto_subtitle(url, vp, meta["auto_sub_lang"])
        if not sub:
            return None
        parsed = stage2_subtitles.parse_vtt(sub, collapse_rollup=True)
        return _accept_caption(parsed, "자동", sub.name)

    sources = (
        [_from_stt, _from_manual, _from_auto]
        if subs.stt_first
        else [_from_manual, _from_stt, _from_auto]
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

    segs = stage4_clean.clean_segments(segs)
    vp.transcript.write_text(
        json.dumps([s.model_dump() for s in segs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
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
    data_dir = Path(data_dir if data_dir is not None else cfg.data_dir)
    out_dir = Path(out_dir if out_dir is not None else cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 0단계: 채널/재생목록 URL 을 최근 N개 영상으로 확장 ---
    failures: list[str] = []
    videos = _resolve_sources(videos, channel_limit, failures, on_progress, should_stop)

    # --- 1~4단계: 트랜스크립트 ---
    metas: dict[str, dict] = {}
    transcripts: dict[str, list[Segment]] = {}
    total = len(videos)
    for i, url in enumerate(videos, start=1):
        if should_stop():
            return PipelineResult(
                video_count=len(transcripts), failures=failures, stopped=True
            )
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
        try:
            vid, meta, segs = build_transcript(
                url,
                cfg,
                data_dir,
                force,
                log=lambda m: on_progress(Progress(message=m, phase="transcript")),
                on_stt_progress=_make_stt_progress_reporter(on_progress, i, total, url),
            )
        except Exception as exc:  # 한 영상 실패가 배치 전체를 막지 않도록
            on_progress(
                Progress(
                    message=f"[{url}] 실패 -> 건너뜀: {type(exc).__name__}: {exc}",
                    level="error",
                    phase="transcript",
                )
            )
            failures.append(url)
            continue
        metas[vid] = meta
        transcripts[vid] = segs
        on_progress(
            Progress(
                message=f"[{vid}] 트랜스크립트 {len(segs)} 세그먼트",
                level="success",
                phase="transcript",
                done=i,
                total=total,
            )
        )

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
        raise RuntimeError("처리된 영상이 없습니다 (모든 영상 실패).")

    if stage == "transcript":
        on_progress(
            Progress(message="트랜스크립트 단계까지 완료.", level="success", phase="done")
        )
        return PipelineResult(
            video_count=len(transcripts), failures=failures
        )

    # LLM 은 5단계부터 필요 -> 여기서 지연 초기화 (자격증명 없으면 RuntimeError)
    from .llm.claude_client import ClaudeClient

    client = ClaudeClient()

    # --- 5단계: 영상별 지식 원자 단위 ---
    all_units: list[KnowledgeUnit] = []
    total_v = len(transcripts)
    for i, (vid, segs) in enumerate(transcripts.items(), start=1):
        if should_stop():
            return PipelineResult(
                video_count=len(transcripts),
                unit_count=len(all_units),
                failures=failures,
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
        on_progress(Progress(message="추출 단계까지 완료.", level="success", phase="done"))
        return PipelineResult(
            video_count=len(transcripts),
            unit_count=len(all_units),
            failures=failures,
        )

    if should_stop():
        return PipelineResult(
            video_count=len(transcripts),
            unit_count=len(all_units),
            failures=failures,
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
    on_progress(
        Progress(
            message=f"완료: {wiki_path} (개념 {len(clusters)}개)",
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
        wiki_path=wiki_path,
        clusters_path=clusters_path,
        failures=failures,
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
