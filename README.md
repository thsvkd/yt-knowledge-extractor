# YouTube Knowledge Extractor

한 유튜버의 **말 중심 콘텐츠**(토크·리뷰·인터뷰)를 대상으로, 여러 영상에 흩어진 지식을
**개념 단위로 통합한 위키형 지식베이스(`wiki.md`)**로 가공하는 데스크톱 애플리케이션입니다.
모든 지식 항목에는 원본 영상의 타임스탬프와 인용 근거가 포함되어 원문 역추적이 가능합니다.

**[최신 릴리스 다운로드](https://github.com/thsvkd/yt-knowledge-extractor/releases/latest)** · [작동 방식](#작동-방식) · [자주 묻는 질문](#자주-묻는-질문)

<!-- TODO: 앱 스크린샷 추가 → docs/screenshot.png 로 저장 후 아래 주석 해제 -->
<!-- ![스크린샷](docs/screenshot.png) -->

---

## 주요 기능

- **개념 단위 통합** — 영상별 요약을 나열하는 대신, 같은 개념을 여러 영상에서 모아 통합합니다.
- **검증 가능성** — 모든 항목에 타임스탬프와 원문 인용이 포함되어 원본과 대조할 수 있습니다.
- **사실/의견 구분** — 각 항목을 사실·의견·팁·정의로 분류합니다.
- **자막 부재 시 STT 대체** — 수동 자막이 없으면 로컬 음성인식(faster-whisper)으로 전사합니다.
- **설치 불필요** — 압축 해제 후 바로 실행하는 Windows 데스크톱 앱으로, 별도의 개발 환경이 필요 없습니다.
- **자동 업데이트** — 새 버전 출시 시 앱 내에서 갱신합니다.

---

## 시작하기

> [!NOTE]
> 현재 **Windows** 실행 파일을 제공합니다. macOS·Linux는 아래 [개발 환경](#개발-환경)의
> 소스 빌드를 참고하십시오.

### 1. 다운로드

[최신 릴리스](https://github.com/thsvkd/yt-knowledge-extractor/releases/latest)에서 환경에 맞는 파일을 받습니다.

| 파일 | 대상 | 크기 |
| --- | --- | --- |
| **`yke-cpu-windows.zip`** | NVIDIA GPU가 없거나 확실하지 않은 경우. 대부분 이 버전을 사용합니다. | ~145 MB |
| **`yke-gpu-windows.zip`** | **NVIDIA GPU(CUDA)**를 사용해 음성인식을 가속하려는 경우. | ~745 MB |

> [!TIP]
> GPU 버전은 음성인식 **속도**만 다를 뿐 결과물 품질은 동일합니다. GPU 버전도 GPU가
> 없으면 자동으로 CPU로 동작하므로, 확실하지 않으면 CPU 버전을 권장합니다.

### 2. 압축 해제 후 실행

1. 받은 `.zip`을 압축 해제합니다.
2. 폴더 안의 `.exe`(또는 `실행.bat`)를 실행합니다.

> [!IMPORTANT]
> - 폴더에는 실행에 필요한 파일이 함께 들어 있습니다. `.exe`나 `실행.bat`만 분리하지 말고
>   **폴더째** 유지하십시오.
> - 최초 실행 시 *"Windows의 PC 보호"* 창이 표시되면 **추가 정보 → 실행**을 선택하십시오.
>   코드 서명이 없는 개인 배포본에서 발생하는 정상적인 경고입니다.
> - 실행 시 `PathNotFoundException` 오류로 앱이 뜨지 않으면 Windows 보안의 **제어된 폴더
>   액세스** 때문일 수 있습니다. 아래 [자주 묻는 질문](#자주-묻는-질문)을 참고하십시오.

### 3. Claude 토큰 준비 (선택)

이 앱은 두 가지 수준으로 동작합니다.

- **스크립트(전사)까지** — 토큰이 **필요 없습니다.** 영상의 발화를 텍스트로 전사하는 단계까지 수행합니다.
- **지식 위키까지 (전체)** — 전사 결과를 개념 단위로 정리하려면 **Claude API 토큰**이 필요합니다.

토큰 없이 전사 단계만 먼저 실행해 결과를 확인한 뒤, 전체 위키 생성으로 전환할 수 있습니다.
토큰 발급 방법은 두 가지입니다.

<details>
<summary><b>방법 A — Anthropic API 키 (사용량 기반 과금)</b></summary>

<br>

1. <https://console.anthropic.com> 에 가입하고 결제 수단을 등록합니다.
2. **API Keys → Create Key** 에서 키를 발급합니다 (`sk-ant-...`).
3. 발급한 키를 아래 **4단계**에서 입력합니다.

</details>

<details>
<summary><b>방법 B — Claude Code 구독 토큰</b></summary>

<br>

Claude 구독과 [Claude Code](https://claude.com/claude-code)가 설치된 환경이라면 터미널에서 다음을 실행합니다.

```bash
claude setup-token
```

발급된 `sk-ant-oat01-...` 토큰을 사용합니다.

</details>

### 4. 실행

1. **유튜브 URL** 칸에 영상 주소를 입력합니다. 여러 개는 한 줄에 하나씩 입력하며, **채널·재생목록 URL**을 넣으면 최근 영상이 자동으로 확장됩니다.
2. **저장 폴더**를 지정합니다. 결과물 `wiki.md`와 중간 산출물이 이 폴더에 저장됩니다.
3. **실행 단계**를 선택합니다 — `스크립트 추출까지`(토큰 불필요) 또는 `전체 (지식 문서화까지)`.
4. `전체`를 선택한 경우, **고급 옵션**에서 Claude 토큰을 입력하고 **토큰 저장**을 누릅니다. 저장하면 다음 실행부터 자동으로 불러옵니다.
5. **실행**을 누릅니다. 진행 중 **중단**할 수 있으며, 그때까지 처리된 데이터는 저장 폴더에 유지됩니다.

### 5. 결과 확인

저장 폴더에 생성된 **`wiki.md`**가 최종 지식베이스입니다.
[Obsidian](https://obsidian.md)·Typora·VS Code 등 마크다운 뷰어로 열면 개념별로 정리된
내용과 원본 타임스탬프를 확인할 수 있습니다.

---

## 작동 방식

내부적으로 7단계 파이프라인을 거칩니다. CLI와 GUI는 동일한 코어(`run_pipeline`)를 사용합니다.

| 단계 | 내용 |
| --- | --- |
| 0 | 영상/채널 선정 (채널·재생목록은 최근 N개 자동 확장) |
| 1 | 오디오 + 메타데이터 다운로드 (`yt-dlp`, 오디오만 다운로드) |
| 2 | 자막 확인 (크리에이터 제작 **수동 자막** 우선) |
| 3 | 자막 부재 시 **음성인식(STT)** — 로컬 `faster-whisper` |
| 4 | 텍스트 정제 |
| 5 | 영상별 **지식 원자 단위** 추출 — Claude (구조화 JSON) |
| 6 | 영상 간 **통합** → `wiki.md` — Claude (클러스터링·중복제거·상충 표기) |

핵심은 5·6단계입니다. 서술형 요약을 병합하는 대신 `개념 / 명제 / 유형(사실·의견·팁·정의)
/ 타임스탬프 / 인용 근거` 형태의 **구조화된 지식 원자 단위**로 추출해 개념별로 통합합니다.
이를 통해 원본 역추적과 할루시네이션 검증이 가능합니다.

기획 의도의 단일 진실 원천(SSoT)은 **[docs/SPEC.md](docs/SPEC.md)**를 참고하십시오.

---

## 산출물

```
<저장 폴더>/
├─ <video_id>/
│   ├─ meta.json         # 제목·설명·업로드일·챕터
│   ├─ audio.*           # 다운로드한 오디오
│   ├─ transcript.json   # 전사 스크립트 (시작/끝/텍스트)
│   └─ units.json        # 영상별 지식 원자 단위
├─ clusters.json         # 통합 개념 클러스터
└─ wiki.md               # 최종 지식베이스
```

---

## 자주 묻는 질문

<details>
<summary><b>CPU 버전과 GPU 버전의 결과물이 다릅니까?</b></summary>

<br>

결과물 품질은 동일합니다. 차이는 음성인식 **속도**뿐입니다. NVIDIA GPU가 있으면 GPU
버전이 전사를 더 빠르게 처리합니다. GPU가 없으면 CPU 버전을 사용하십시오.

</details>

<details>
<summary><b>Windows가 앱을 차단합니다.</b></summary>

<br>

코드 서명이 없는 개인 배포본에서 발생하는 정상적인 경고입니다. *"Windows의 PC 보호"*
창에서 **추가 정보 → 실행**을 선택하면 됩니다.

</details>

<details>
<summary><b>토큰이 반드시 필요합니까? 비용이 발생합니까?</b></summary>

<br>

전사(스크립트) 단계까지는 토큰 없이 동작합니다. 전사 결과를 개념 단위로 정리하는 **전체
위키 생성**에만 Claude 토큰이 필요하며, 이 단계는 사용량에 따라 Anthropic에 과금됩니다.

</details>

<details>
<summary><b>실행하면 <code>PathNotFoundException</code>(문서 폴더 관련) 오류가 나며 앱이 뜨지 않습니다.</b></summary>

<br>

Windows 보안의 **제어된 폴더 액세스**(랜섬웨어 방지 기능)가 서명되지 않은 이 앱이 "문서"
폴더에 쓰는 것을 차단해서 생기는 오류입니다("허용되지 않은 변경이 차단됨" 알림이 함께 뜨는
경우 이 원인일 가능성이 높습니다). 다음 중 하나로 해결할 수 있습니다.

- **(권장) 이 앱만 허용** — `Windows 보안 → 바이러스 및 위협 방지 → 랜섬웨어 방지 관리 →
  제어된 폴더 액세스를 통해 앱 허용` 에서 이 앱의 `.exe`를 추가합니다. 다른 폴더에 대한
  보호는 그대로 유지됩니다.
- **제어된 폴더 액세스 자체를 끄기** — 같은 화면에서 기능을 끕니다(간단하지만 랜섬웨어
  방지 기능이 전체적으로 비활성화됩니다).

</details>

<details>
<summary><b>업데이트는 어떻게 합니까?</b></summary>

<br>

새 버전이 출시되면 앱 시작 시 이를 감지하며, 앱 내에서 갱신할 수 있습니다.

</details>

<details>
<summary><b>결과를 그대로 신뢰해도 됩니까?</b></summary>

<br>

이 프로젝트는 **사람의 검토**를 전제로 합니다. 각 항목의 타임스탬프와 인용을 통해 원본과
대조하여 확인하십시오. 근거를 함께 남기는 이유가 여기에 있습니다.

</details>

---

## 개발 환경

<details>
<summary><b>소스에서 실행 · 빌드 · 테스트</b></summary>

<br>

**요구사항**: Python 3.11+, [uv](https://docs.astral.sh/uv/)

**설치**

```bash
uv sync
```

**설정**

1. `.env.example`을 `.env`로 복사한 뒤 토큰을 입력합니다 (`CLAUDE_CODE_OAUTH_TOKEN` 또는 `ANTHROPIC_API_KEY`).
2. `config/channel.yaml`의 `videos` 목록에 대상 영상/채널 URL을 추가합니다.

> STT는 기본값 `device: auto` + `compute_type: auto`로 동작합니다. GPU가 있으면 `float16`,
> 없으면 `int8`을 자동 선택하며, GPU 사용이 실패하면 CPU(int8)로 폴백합니다.
> (`stt.device: cuda`로 고정하면 CTranslate2용 CUDA 런타임이 필요합니다.)

**실행 (GUI)**

```bash
uv run yke-gui   # CLI와 동일한 파이프라인 코어를 사용하는 flet 데스크톱 앱
```

**실행 (CLI)**

```bash
uv run yke                        # 전체 파이프라인
uv run yke --stage transcript     # 1~4단계: 스크립트만
uv run yke --stage extract        # 5단계까지: units.json
uv run yke --stage integrate      # 6단계: wiki.md
uv run yke --force                # 캐시 무시 후 재생성
uv run yke --limit 5              # 채널/재생목록에서 최근 5개만
```

**빌드 (네이티브 데스크톱 앱)**

실행 OS를 감지해 flet 네이티브 앱을 빌드합니다(Windows/macOS/Linux 공통).

```bash
python scripts/build.py         # CPU 전용 버전
python scripts/build.py --gpu   # NVIDIA CUDA 가속 버전
```

- 결과물: `dist/yke-<cpu|gpu>-<platform>/` — 실행 파일 + DLL + `data/` 한 세트. **폴더째** 배포·실행합니다. 압축본 `dist/yke-<cpu|gpu>-<platform>.zip`은 GitHub Releases 업로드용입니다.
- GPU 변형은 STT(faster-whisper→CTranslate2) 가속에 필요한 `nvidia-cublas-cu12` 런타임을 번들에 포함합니다(빌드 중에만 `[project.dependencies]`에 임시 주입). CPU 전용은 이를 제외해 더 가볍게 빌드합니다. GPU 번들도 GPU가 없으면 자동으로 CPU(int8)로 폴백합니다.
- 사전 준비(Windows): Visual Studio "Desktop development with C++" 워크로드가 필요합니다(없으면 스크립트가 설치 방법을 안내). Flutter SDK는 `flet build`가 필요 시 자동으로 다운로드합니다.

**테스트**

```bash
uv run python -m unittest discover -s tests -t .
```

**주요 문서**: 기획 SSoT [docs/SPEC.md](docs/SPEC.md) · 개발 규약 [AGENTS.md](AGENTS.md)

</details>

---

## 범위와 한계 (PoC)

- 개인 학습용 개념 검증(PoC)입니다. 통합 결과는 **사람의 검토**를 전제로 합니다.
- 미구현: 화자 분리(diarization), 4단계 광고/인사 구간 LLM 클렌징, STT 도구 실측 비교, 자동 검증 파이프라인.

> [!CAUTION]
> 유튜브 다운로드는 서비스 약관상 그레이존입니다. 개인 학습 용도로만 사용하고,
> 공개·서비스화 시에는 채널 소유자의 허락 또는 공식 API 경로를 검토하십시오.
