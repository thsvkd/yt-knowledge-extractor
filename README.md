# yt-knowledge-extractor

유튜버 한 명의 **말 중심 콘텐츠**(토크·리뷰·인터뷰)를 대상으로, 여러 영상에 흩어진
지식을 **개념 단위로 통합한 위키형 지식베이스**로 가공하는 PoC.

설계 배경과 의사결정 기록은 [docs/HANDOFF.md](docs/HANDOFF.md) 참고.

## 파이프라인 (7단계)

| 단계 | 내용 | 구현 |
| --- | --- | --- |
| 0 | 영상 선정 | `config/channel.yaml` |
| 1 | 오디오 + 메타데이터 다운로드 | `yt-dlp` (bestaudio, ffmpeg 불필요) |
| 2 | 자막 확인 (수동 자막만) | VTT 파싱 |
| 3 | STT (자막 없을 때) | `faster-whisper` 로컬 |
| 4 | 텍스트 정제 | 규칙 기반(경량) |
| 5 | 영상별 지식 원자 단위 추출 | Claude (구조화 JSON) |
| 6 | 영상 간 통합 → 마크다운 | Claude (클러스터링·중복제거·상충 플래깅) |

핵심은 5·6단계: 서술형 요약을 병합하지 않고, **구조화된 지식 원자 단위**
(`concept / statement / type / timestamp / quote_evidence`)를 뽑아 개념별로 통합한다.
`timestamp`·`quote_evidence` 로 원본 역추적과 할루시네이션 검증이 가능하다.

## 설치

```bash
uv sync
```

## 설정

1. `.env.example` → `.env` 복사 후 토큰 입력
   - `CLAUDE_CODE_OAUTH_TOKEN` (Claude Code 구독 토큰, `claude setup-token` 으로 발급)
   - 또는 `ANTHROPIC_API_KEY`
2. `config/channel.yaml` 의 `videos` 목록에 대상 영상 URL 추가

> STT GPU 가속(`stt.device: cuda`)은 CTranslate2 용 CUDA/cuDNN 런타임이 필요합니다.
> 세팅이 없으면 `device: auto`가 실패 시 CPU(int8)로 자동 폴백합니다.

## 실행

```bash
# 전체 파이프라인
uv run yke

# 단계별로 끊어 실행 (검토용)
uv run yke --stage transcript   # 1~4단계: 트랜스크립트만
uv run yke --stage extract      # 5단계까지: data/<id>/units.json
uv run yke --stage integrate    # 6단계: output/wiki.md

# 캐시 무시하고 재생성
uv run yke --force
```

## 산출물

```
data/<video_id>/
  meta.json         # 제목/설명/업로드일/챕터 등
  audio.*           # 다운로드 오디오
  audio.<lang>.vtt  # 수동 자막(있는 경우)
  transcript.json   # 정제된 세그먼트 (start/end/text)
  units.json        # 지식 원자 단위

output/
  clusters.json     # 통합 개념 클러스터 (구조화)
  wiki.md           # 최종 지식베이스 (Obsidian 등과 호환)
```

## 현재 PoC 범위 / 미구현

- 화자 분리(diarization): 미포함 — 필요 시 3단계 뒤에 WhisperX 삽입
- 4단계 광고/인사 구간 LLM 클렌징: 미포함(공백 정규화만)
- STT 도구 실측 비교(리턴제로/클로바): 미실시
- 통합 결과는 **사람 검토 전제** (자동 검증 파이프라인은 스케일업 단계)
