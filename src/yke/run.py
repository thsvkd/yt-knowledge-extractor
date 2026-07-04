"""CLI 진입점: 합의된 7단계 파이프라인 오케스트레이션.

단계:
  0. 영상 선정        -> config/channel.yaml
  1. 오디오+메타      -> stage1_ingest
  2. 자막 확인        -> stage2_subtitles (수동 자막만)
  3. STT              -> stage3_stt (자막 없을 때만)
  4. 텍스트 정제      -> stage4_clean
  5. 원자 단위 추출   -> stage5_extract (LLM)
  6. 통합/문서화      -> stage6_integrate (LLM)

중간 산출물은 data/ 아래에 캐싱되어 재실행 시 이어서 진행한다(--force 로 재생성).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .config import load_config
from .models import KnowledgeUnit, Segment
from .paths import VideoPaths
from .utils import load_dotenv
from .pipeline import (
    stage1_ingest,
    stage2_subtitles,
    stage3_stt,
    stage4_clean,
    stage5_extract,
    stage6_integrate,
)


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")


def _video_id_from_url(url: str) -> str | None:
    m = _YT_ID.search(url)
    return m.group(1) if m else None


def build_transcript(url, cfg, data_dir: Path, force: bool):
    """1~4단계: 영상 하나의 트랜스크립트를 생성/로딩한다."""
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
            print(f"[{vid}] ① 수동 자막 사용: {sub.name}")
            segs = stage2_subtitles.parse_vtt(sub)

    # ② faster-whisper STT
    if segs is None:
        audio = vp.audio()
        if audio is None:
            raise RuntimeError(f"[{vid}] 오디오 파일을 찾을 수 없습니다.")
        try:
            print(f"[{vid}] ② STT 실행 (faster-whisper {cfg.stt.model})...")
            segs = stage3_stt.transcribe(audio, cfg.language, cfg.stt)
        except Exception as exc:
            print(f"[{vid}] STT 실패: {type(exc).__name__}: {exc}")

    # ③ 유튜브 자동자막 (STT 실패 시 최후 폴백)
    if segs is None and meta.get("auto_sub_lang"):
        print(f"[{vid}] ③ 자동자막 폴백 다운로드 (lang={meta['auto_sub_lang']})...")
        sub = stage1_ingest.download_auto_subtitle(url, vp, meta["auto_sub_lang"])
        if sub:
            print(f"[{vid}]    자동자막 사용: {sub.name}")
            segs = stage2_subtitles.parse_vtt(sub, collapse_rollup=True)

    if segs is None:
        raise RuntimeError(f"[{vid}] 트랜스크립트를 확보하지 못했습니다 (수동/STT/자동 모두 실패).")

    segs = stage4_clean.clean_segments(segs)
    vp.transcript.write_text(
        json.dumps([s.model_dump() for s in segs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return vid, meta, segs


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
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.stt_model:
        cfg.stt.model = args.stt_model
    data_dir = Path(cfg.data_dir)
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 1~4단계: 트랜스크립트 ---
    metas: dict[str, dict] = {}
    transcripts: dict[str, list[Segment]] = {}
    failures: list[str] = []
    for url in cfg.videos:
        try:
            vid, meta, segs = build_transcript(url, cfg, data_dir, args.force)
        except Exception as exc:  # 한 영상 실패가 배치 전체를 막지 않도록
            print(f"[{url}] 실패 -> 건너뜀: {type(exc).__name__}: {exc}")
            failures.append(url)
            continue
        metas[vid] = meta
        transcripts[vid] = segs
        print(f"[{vid}] 트랜스크립트 {len(segs)} 세그먼트")

    if failures:
        print(f"경고: {len(failures)}개 영상 실패, 나머지로 계속: {failures}")
    if not transcripts:
        raise RuntimeError("처리된 영상이 없습니다 (모든 영상 실패).")

    if args.stage == "transcript":
        print("트랜스크립트 단계까지 완료.")
        return

    # LLM 은 5단계부터 필요 -> 여기서 지연 초기화
    from .llm.claude_client import ClaudeClient

    client = ClaudeClient()

    # --- 5단계: 영상별 지식 원자 단위 ---
    all_units: list[KnowledgeUnit] = []
    for vid, segs in transcripts.items():
        vp = VideoPaths(data_dir, vid)
        if vp.units.exists() and not args.force:
            units = [KnowledgeUnit(**u) for u in json.loads(vp.units.read_text(encoding="utf-8"))]
        else:
            print(f"[{vid}] 지식 원자 단위 추출...")
            units = stage5_extract.extract_units(segs, vid, cfg.llm, client)
            vp.units.write_text(
                json.dumps([u.model_dump() for u in units], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        print(f"[{vid}] {len(units)} 유닛")
        all_units.extend(units)

    if args.stage == "extract":
        print("추출 단계까지 완료.")
        return

    # --- 6단계: 통합 + 마크다운 ---
    print(f"통합 중... 총 {len(all_units)} 유닛")
    clusters = stage6_integrate.integrate(all_units, cfg.llm, client)
    (out_dir / "clusters.json").write_text(
        json.dumps([c.model_dump() for c in clusters], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md = stage6_integrate.render_markdown(clusters, metas)
    (out_dir / "wiki.md").write_text(md, encoding="utf-8")
    print(f"완료: {out_dir / 'wiki.md'} (개념 {len(clusters)}개)")


if __name__ == "__main__":
    main()
