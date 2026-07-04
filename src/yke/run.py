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
from .utils import is_channel_or_playlist_url, load_dotenv

_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")


def _video_id_from_url(url: str) -> str | None:
    m = _YT_ID.search(url)
    return m.group(1) if m else None


# --- 진행 이벤트 / 결과 모델 -------------------------------------------------


@dataclass
class Progress:
    """파이프라인 진행 상황 이벤트.

    CLI 는 ``message`` 만 출력하고(단, ``transient`` 는 건너뜀), GUI 는 ``phase``·
    ``done``/``total``·``indeterminate`` 로 진행바와 상태 텍스트를 갱신한다.
    """

    message: str
    level: str = "info"  # info | success | warning | error
    phase: str | None = None  # transcript | extract | integrate | done
    done: int | None = None  # 현재 단계에서 완료한 개수
    total: int | None = None  # 현재 단계의 전체 개수
    indeterminate: bool = False  # 끊을 수 없는 단일 작업(다운로드/STT/LLM) 진행 중
    transient: bool = False  # 스피너용 임시 상태 — 영속 로그(CLI 출력)에는 남기지 않음


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
):
    """1~4단계: 영상 하나의 트랜스크립트를 생성/로딩한다.

    ``log`` 로 진행 메시지를 흘려 CLI 는 stdout 에, GUI 는 로그 뷰에 보이게 한다.
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

    segs: list[Segment] | None = None

    # ① 수동 자막 (최우선) — 기록된 언어의 파일을 정확히 지목
    if meta.get("manual_sub_lang"):
        sub = vp.root / f"audio.{meta['manual_sub_lang']}.vtt"
        if sub.exists():
            log(f"[{vid}] ① 수동 자막 사용: {sub.name}")
            segs = stage2_subtitles.parse_vtt(sub)

    # ② faster-whisper STT
    if segs is None:
        audio = vp.audio()
        if audio is None:
            raise RuntimeError(f"[{vid}] 오디오 파일을 찾을 수 없습니다.")
        try:
            log(f"[{vid}] ② STT 실행 (faster-whisper {cfg.stt.model})...")
            segs = stage3_stt.transcribe(audio, cfg.language, cfg.stt)
        except Exception as exc:
            log(f"[{vid}] STT 실패: {type(exc).__name__}: {exc}")

    # ③ 유튜브 자동자막 (STT 실패 시 최후 폴백)
    if segs is None and meta.get("auto_sub_lang"):
        log(f"[{vid}] ③ 자동자막 폴백 다운로드 (lang={meta['auto_sub_lang']})...")
        sub = stage1_ingest.download_auto_subtitle(url, vp, meta["auto_sub_lang"])
        if sub:
            log(f"[{vid}]    자동자막 사용: {sub.name}")
            segs = stage2_subtitles.parse_vtt(sub, collapse_rollup=True)

    if segs is None:
        raise RuntimeError(f"[{vid}] 트랜스크립트를 확보하지 못했습니다 (수동/STT/자동 모두 실패).")

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
    load_dotenv()  # .env 의 CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY 를 환경변수로

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
