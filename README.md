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

# 채널/재생목록 URL 을 config 에 넣었을 때 최근 N개만 처리
uv run yke --limit 5
```

## GUI (flet)

CLI 와 동일한 파이프라인 코어(`run_pipeline`)를 재사용하는 데스크톱 GUI.

```bash
uv run yke-gui
```

- **유튜브 URL**: 영상 또는 **채널/재생목록** URL 을 한 줄에 하나씩 입력. 채널을 넣으면
  "채널: 최근 영상 수" 만큼 최근 영상을 자동으로 확장해 처리한다.
- **실행 단계**(2단계): `전체 (지식 문서화까지)` / `스크립트 추출까지`(기본값).
  스크립트 추출까지만 할 때는 언어 모델·토큰 UI 가 비활성화된다.
- **저장 폴더**: 캐시와 최종 산출물(`wiki.md`)을 한 폴더에 둔다.
- **고급 옵션**: 언어, 스크립트 변환 모델, GPU 가속(자동/사용/사용 안함), 언어 모델(드롭다운),
  강제로 재생성, 그리고 Claude 토큰.
- **Claude 토큰**: `전체` 단계에 필요. 입력 후 **토큰 저장** 을 누르면 앱 저장소(기본) 또는
  지정한 파일에 보관되어 다음 실행에 자동 로드된다(배포본에서 `.env` 없이 동작).
- 파이프라인은 백그라운드 스레드에서 돌며 진행바·상태·로그가 실시간 갱신되고,
  **중단** 버튼으로 영상/단계 경계에서 협조적으로 멈춘다(받은 부분은 저장 폴더에 캐시).

## 빌드 / 배포

실행한 OS 를 감지해 flet 네이티브 데스크톱 앱을 빌드한다(Windows/macOS/Linux 공통).

```bash
python scripts/build.py         # CPU 전용 버전
python scripts/build.py --gpu   # NVIDIA CUDA 가속 버전
```

- 결과물: `dist/yke-<cpu|gpu>-<platform>/` — 실행파일 + DLL + `data/` 한 세트. **폴더째** 배포·실행한다.
- CPU / GPU: STT(faster-whisper)의 CUDA 가속에는 `nvidia-cublas-cu12`/`nvidia-cudnn-cu12` 런타임이
  필요하다. `--gpu` 는 이 둘을 번들에 포함하고(빌드 동안만 `[project.dependencies]` 에 임시 주입),
  없으면 CPU 전용으로 더 가볍게 빌드한다. GPU 번들도 GPU 가 없으면 자동으로 CPU(int8)로 폴백한다.
- 사전 준비: Windows 는 Visual Studio "Desktop development with C++" 워크로드가 필요하다(없으면
  스크립트가 설치 방법을 안내). Flutter SDK 는 `flet build` 가 필요 시 자동으로 내려받는다.

## 테스트

결정론적 순수 로직(유틸/자막 파싱/롤업 축소/정제/설정/렌더링)에 대한 특성화 테스트:

```bash
uv run python -m unittest discover -s tests -t .
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
