# SPEC — yt-knowledge-extractor

> 이 프로젝트의 **기획·의도에 대한 단일 진실 원천(SSoT)** 문서. 무엇을, 왜, 어떻게
> 만드는지를 규정한다. 코드가 이 문서와 어긋나면 둘 중 하나가 틀린 것이므로 맞춘다.
> (구현 세부·명령어는 [README](../README.md)·[AGENTS.md](../AGENTS.md) 참조.)

## 1. 목표

특정 유튜버 한 명의 **말/음성 중심 콘텐츠**(토크·리뷰·인터뷰)를 대상으로, 여러 영상에
흩어진 지식을 **개념 단위로 통합한 위키형 지식베이스**로 가공한다.

- **지향점**: 영상별 요약의 나열이 아니라, 같은 개념을 여러 영상에서 모아 통합한 지식.
- **핵심 가치**: 검증 가능성 — 모든 지식 조각은 `timestamp`·`quote_evidence`로 원본을
  역추적할 수 있어야 하고, 사실/의견이 구분되어야 한다.
- **용도**: 개인 학습용. 화면 정보(코드·시각 자료)는 대상이 아니다(음성이 본질).

## 2. 범위 (Scope)

**포함 (PoC)**: 영상 5~10개 규모의 소규모 검증. 한국어 콘텐츠 우선. 결과물은 **사람이
검토**하는 것을 전제로 한다.

**비포함 (현재 미구현, 스케일업 시 재검토)**:
- 화자 분리(diarization) — 필요 시 3단계 뒤 WhisperX 삽입.
- 4단계의 광고/인사 구간 LLM 클렌징 — 현재는 공백 정규화 등 규칙 기반만.
- STT 도구 실측 비교(리턴제로/클로바 등) — 로컬 faster-whisper 베이스라인만.
- 통합 결과 자동 검증 파이프라인 — 현재는 사람 검토.

**미해결 정책 이슈**: 유튜브 다운로드는 ToS상 그레이존이다. 개인 학습용 PoC 전제이며,
서비스화·공개 배포 시에는 채널 소유자 허락 또는 정당한 API 경로 검토가 필요하다(§8).

## 3. 파이프라인 (7단계)

중간 산출물은 저장 폴더 아래에 캐싱되어 재실행 시 이어서 진행한다(`--force`로 재생성).
CLI·GUI 모두 동일한 코어(`yke.run.run_pipeline`)를 호출한다.

| 단계 | 내용 | 핵심 결정 | 구현 |
| --- | --- | --- | --- |
| 0 | 영상/채널 선정 | 개별 영상 URL 또는 채널·재생목록 URL(→ 최근 N개 자동 확장) | `config/channel.yaml`, GUI, `stage1_ingest.expand_source` |
| 1 | 오디오 + 메타데이터 | 말 중심이므로 **오디오만**(bestaudio). 포맷 변환 안 해 시스템 ffmpeg 불필요 | `yt-dlp`, `stage1_ingest` |
| 2 | 자막 확인 | **수동(크리에이터) 자막을 최우선 신뢰**. 자동생성 자막은 그다음 소스. 타임스탬프 보존 | `stage2_subtitles` (VTT 파싱) |
| 3 | STT | 우선순위 **① 수동 자막 > ② 유튜브 자동자막 > ③ 로컬 STT(faster-whisper/Vosk)** — 자원을 쓰는 마지막 수단 | `stage3_stt` (faster-whisper), `stage3_stt_vosk` (Vosk) |
| 4 | 텍스트 정제 | 규칙 기반 경량 정제(공백 정규화 등) | `stage4_clean` |
| 5 | 지식 원자 단위 추출 | 서술형 요약이 아니라 **구조화 JSON**(§4) | `stage5_extract` (Claude) |
| 6 | 영상 간 통합 | 개념 클러스터링 → 중복 제거 → 상충 플래깅 → 마크다운 | `stage6_integrate` (Claude) |

STT 시장 참고(2026 기준): 영어권은 Whisper large-v3·상용 API가 강하고, 한국어는 교착어
특성상 CER 평가가 적절하며 리턴제로·클로바스피치가 상위권으로 보고된다. 본 프로젝트는
로컬 Whisper large-v3로 베이스라인을 잡고, 필요 시 한국어 특화 API와 비교하는 순서를 권장한다(§8-2).

## 4. 핵심 설계 결정 — 지식 원자 단위 (가장 중요)

"영상별 요약을 만들어 나중에 병합"하는 방식은 누락·중복·왜곡 위험이 커서 채택하지 않는다.
대신 **2단계 구조화 추출**을 쓴다.

**5단계 — 영상별 "지식 원자 단위(atomic knowledge unit)"**:

```json
{
  "concept": "핵심 개념/주제",
  "statement": "핵심 명제 (1~2문장, 원문 근거 기반)",
  "type": "fact | opinion | tip | definition",
  "source_video_id": "...",
  "timestamp": "12:34",
  "quote_evidence": "판단 근거가 된 원문 구절 (짧게, 검증용)"
}
```

- `type` 구분은 **필수** — 토크·리뷰는 개인 의견과 검증 가능한 사실이 섞이므로 신뢰도 판단에 필요.
- `timestamp`·`quote_evidence`로 **원본 역추적·할루시네이션 검증**이 가능하도록 설계.

**6단계 — 영상 간 통합**: 여러 영상의 원자 단위를 모아 LLM에게 "같은 concept 클러스터링 →
중복 제거 → 상충 내용 플래깅"을 시킨다. 구조화 데이터 병합이 서술형 병합보다 안정적이다.
결과물은 개념별 마크다운(개념 → 설명 → 출처 타임스탬프 딥링크)으로, Obsidian 등과 호환된다.
**PoC 규모에서는 통합 결과를 사람이 검토**해 할루시네이션·왜곡을 확인한다.

## 5. 산출물

```
<저장 폴더>/<video_id>/
  meta.json         # 제목/설명/업로드일/챕터 등
  audio.*           # 다운로드 오디오
  audio.<lang>.vtt  # 수동 자막(있는 경우)
  transcript.json   # 정제된 세그먼트 (start/end/text)
  units.json        # 지식 원자 단위
<저장 폴더>/
  clusters.json     # 통합 개념 클러스터 (구조화)
  wiki.md           # 최종 지식베이스 (사람 검토 대상)
```

CLI는 `data_dir`(캐시)와 `output_dir`(산출물)을 분리할 수 있고, GUI는 **하나의 저장
폴더**로 통일해 캐시와 `wiki.md`를 같은 곳에 둔다.

## 6. 인터페이스

- **공유 코어**: `run_pipeline(videos, cfg, *, stage, channel_limit, force, on_progress, should_stop)`.
  진행은 `Progress` 이벤트 스트림, 취소는 `should_stop`으로 영상/단계 경계에서 협조적으로.
- **CLI** (`yke`): `--stage transcript|extract|integrate|all`, `--stt-model`, `--limit`, `--force`.
- **GUI** (`yke-gui`, flet 데스크톱): 영상/채널 URL 입력(+ 최근 N개), 실행 단계 2종
  (`전체(지식 문서화까지)` / `스크립트 추출까지`(기본) — 후자 선택 시 언어 모델 UI 비활성화),
  스크립트 변환 모델·GPU 가속·언어 모델 선택, 진행바·로그·중단, 산출물 열기.

## 7. 자격증명 · 배포 · 자체 업데이트

- **LLM 호출**: Anthropic SDK/토큰 대신 로컬 **Claude Code CLI**(`claude -p`, 헤드리스 모드)를
  subprocess 로 호출한다(`src/yke/llm/claude_client.py`). 인증은 CLI 자체의 로그인 상태
  (`claude login`)를 그대로 쓰므로 앱이 토큰을 저장·주입하지 않는다. CLI 를 PATH 에서 찾지
  못하면 `ClaudeClient` 생성 시점에 안내 메시지와 함께 실패한다.
- **빌드**: `scripts/build.py [--gpu]` → `dist/yke-<cpu|gpu>-<platform>/` 폴더 번들 +
  `.zip`. GPU 변형은 **cuBLAS만** 포함한다(ctranslate2가 cuDNN 로더를 자체 번들하고
  whisper 추론에 cuDNN 서브라이브러리를 쓰지 않음 — 실측 확인). CPU≈419MB, GPU≈1.4GB(zip 752MB).
- **자체 업데이트**: GitHub Releases 기반 커스텀 사이드카. 최신 릴리스 감지 → 변형/플랫폼에
  맞는 에셋 선택 → 다운로드 + **SHA256 검증(GitHub digest 대조)** → 번들 밖 사이드카 스크립트가
  앱 종료 대기 후 폴더 스왑(롤백 포함) 후 재실행. GUI에서 시작 시 자동 확인 + 수동 버튼.
  배포 전 `updater.REPO_OWNER/REPO_NAME`을 실제 public 레포로 지정해야 한다.

## 8. 미해결 이슈 / 향후 과제

1. **ToS/저작권**: 개인 학습용 vs 서비스화·공유 가능성을 명확히 해야 한다(다운로드 방식·보관 정책에 영향).
2. **STT 실측 비교**: Whisper large-v3(로컬) vs 리턴제로 vs 클로바를 5~10개 샘플로 CER 비교(미실시).
3. **5단계 프롬프트 설계**: type 분류 정확도, concept 명명 일관성 검증.
4. **6단계 통합 알고리즘**: 순수 LLM vs 임베딩 유사도 클러스터링 후 LLM 정제 — 스케일업 시 재검토.
5. **대상 채널/카테고리**: 아직 미특정.
6. **자체 업데이트 end-to-end**: public 레포 + 릴리스 2개 이상으로 실측 필요(현재 순수 로직만 단위 테스트).

## 9. 용어

| 용어 | 설명 |
| --- | --- |
| **STT** | 음성 → 텍스트 변환 |
| **WER / CER** | 단어/문자 단위 오류율. 한국어는 교착어라 CER이 더 적절 |
| **Diarization** | 화자 분리. 인터뷰/토크에서 중요(현재 미구현) |
| **지식 원자 단위** | 하나의 검증 가능한 최소 지식 조각(개념+명제+type+근거+출처). 병합·중복제거가 쉬움 |
| **할루시네이션** | LLM이 원본에 없는 내용을 생성. timestamp/quote_evidence로 완화·검증 |
| **수동 자막 / 자동자막** | 크리에이터 제작(1순위, 신뢰) / 유튜브 자동생성(2순위, 낮은 품질). 로컬 STT 는 둘 다 없거나 깨졌을 때만 도는 최후 폴백 |

## 10. 참고자료

- Deepgram Nova-3: https://deepgram.com/learn/introducing-nova-3-speech-to-text-api
- 2026 STT API 비교: https://futureagi.com/blog/speech-to-text-apis-in-2026-benchmarks-pricing-developer-s-decision-guide/
- 한국어 STT 벤치마크(리턴제로): https://blog.rtzr.ai/korean-speechai-benchmark/
- Awesome Korean Speech Recognition: https://github.com/rtzr/Awesome-Korean-Speech-Recognition
- 네이버 클로바스피치: https://www.ncloud.com/product/aiService/csr
