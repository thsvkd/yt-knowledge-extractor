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
- **자막 부재 시 STT 대체** — 수동·자동 자막이 모두 없거나 깨졌을 때만 로컬 음성인식(faster-whisper, 또는 완전 오프라인 경량 엔진 Vosk)으로 전사합니다.
- **원클릭 설치** — 설치기(Setup.exe) 하나로 관리자 권한 없이 설치됩니다. NVIDIA GPU 가속은 별도 버전이 아니라 앱에서 필요할 때 켭니다(전체 지식 위키 생성에는 [Claude Code CLI](https://claude.com/claude-code) 설치가 필요합니다).
- **자동 업데이트** — 새 버전은 앱 내에서 바뀐 부분만 받아 자동 갱신됩니다.

---

## 시작하기

> [!NOTE]
> 현재 **Windows** 실행 파일을 제공합니다. macOS·Linux는 아래 [개발 환경](#개발-환경)의
> 소스 빌드를 참고하십시오.

### 1. 다운로드

[최신 릴리스](https://github.com/thsvkd/yt-knowledge-extractor/releases/latest)에서 설치기 하나를 받습니다.

| 파일 | 대상 | 크기 |
| --- | --- | --- |
| **`YtKnowledgeExtractor-win-Setup.exe`** | 모든 Windows 사용자. 설치 후 자동 업데이트됩니다. | ~160 MB |

> [!TIP]
> **NVIDIA GPU 가속**은 별도 버전이 아니라, 설치 후 앱의 **고급 옵션 → GPU 가속 다운로드**에서
> 켭니다(그때 cuBLAS 런타임 ~900MB를 한 번만 받습니다). GPU 가속은 음성인식 **속도**만 다를 뿐
> 결과물 품질은 동일하며, NVIDIA GPU가 없으면 자동으로 CPU로 동작합니다.

### 2. 설치

1. 받은 `YtKnowledgeExtractor-win-Setup.exe`를 실행합니다. 사용자 폴더(`%LocalAppData%`)에
   설치되고 바탕화면·시작 메뉴 바로가기가 생성됩니다(관리자 권한 불필요). 설치가 끝나면
   앱이 자동으로 실행됩니다.

> [!IMPORTANT]
> - 최초 실행 시 *"Windows의 PC 보호"* 창이 표시되면 **추가 정보 → 실행**을 선택하십시오. 정식
>   CA가 아닌 개인(self-signed) 서명이라 나타나는 정상 경고이며, 이후 자동 업데이트에는 뜨지 않습니다.
> - 드물게 `PathNotFoundException` 오류로 앱이 뜨지 않으면 Windows 보안의 **제어된 폴더
>   액세스** 때문일 수 있습니다. 아래 [자주 묻는 질문](#자주-묻는-질문)을 참고하십시오.

### 3. Claude Code CLI 준비 (선택)

이 앱은 두 가지 수준으로 동작합니다.

- **스크립트(전사)까지** — **필요 없습니다.** 영상의 발화를 텍스트로 전사하는 단계까지 수행합니다.
- **지식 위키까지 (전체)** — 전사 결과를 개념 단위로 정리하려면 **[Claude Code](https://claude.com/claude-code) CLI**가 설치되고 로그인되어 있어야 합니다. 이 앱은 내부적으로 `claude -p`(헤드리스 모드)를 호출합니다.

CLI 없이 전사 단계만 먼저 실행해 결과를 확인한 뒤, 전체 위키 생성으로 전환할 수 있습니다.

1. [claude.com/claude-code](https://claude.com/claude-code)에서 Claude Code CLI를 설치합니다.
2. 터미널에서 로그인합니다.

   ```bash
   claude login
   ```

   Claude 구독 또는 Anthropic API 키 중 편한 방법으로 로그인하면 됩니다. 앱은 로그인된 CLI를
   그대로 호출하므로 별도로 토큰을 입력·저장할 필요가 없습니다.

### 4. 실행

1. **유튜브 URL** 칸에 영상 주소를 입력합니다. 여러 개는 한 줄에 하나씩 입력하며, **채널·재생목록 URL**을 넣으면 최근 영상이 자동으로 확장됩니다.
2. **저장 폴더**를 지정합니다. 결과물 `wiki.md`와 중간 산출물이 이 폴더에 저장됩니다. **채널·재생목록 URL**을 입력하면 그 채널·재생목록 전용 하위 폴더에 정리되어, 여러 채널을 반복 실행해도 산출물이 섞이지 않습니다.
3. **실행 단계**를 선택합니다 — `스크립트 추출까지`(CLI 불필요) 또는 `전체 (지식 문서화까지)`(Claude Code CLI 로그인 필요).
4. **실행**을 누릅니다. 진행바 아래 **단계 타임라인**에 현재 진행 중인 하위 단계(채널 분석·다운로드·자막 확인·STT·정제 등)가 실시간으로 표시됩니다. 진행 중 **중단**할 수 있으며, 그때까지 처리된 데이터는 저장 폴더에 유지됩니다.

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
| 2 | 자막 확인 — **수동(크리에이터) 자막** 우선, 없으면 **유튜브 자동 생성 자막** |
| 3 | 자막이 없거나 깨졌을 때만 **음성인식(STT)** — 로컬 `faster-whisper`(기본) 또는 경량 오프라인 `Vosk` |
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
├─ <video_id>/                    # 개별 영상 URL 입력 시 저장 폴더 바로 아래에 정리
│   ├─ meta.json         # 제목·설명·업로드일·챕터
│   ├─ audio.*           # 다운로드한 오디오
│   ├─ transcript.json   # 전사 스크립트 (시작/끝/텍스트)
│   └─ units.json        # 영상별 지식 원자 단위
├─ <채널/재생목록>/               # 채널·재생목록 URL 입력 시 전용 하위 폴더로 정리
│   └─ <video_id>/ ...            # 위와 동일한 구조
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

정식 CA가 아닌 개인(self-signed) 서명 배포본에서 발생하는 **SmartScreen 평판 경고**입니다
(바이러스로 격리된 것이 아닙니다). 다음 중 하나로 실행할 수 있습니다.

- *"Windows의 PC 보호"* 창에서 **추가 정보 → 실행**.
- 또는 받은 `Setup.exe`를 **우클릭 → 속성 → 하단의 "차단 해제" 체크 → 확인** 후 실행.

> 참고: 설치는 사용자 폴더(`%LocalAppData%`)에 이뤄지며, 앱 내 **자동 업데이트**로 받는 새
> 버전에는 이 경고가 뜨지 않습니다(프로그램이 직접 내려받아 다운로드 표식이 붙지 않기 때문).
> 즉 위 절차는 **최초 1회 설치에만** 필요합니다.

</details>

<details>
<summary><b>Claude Code CLI가 반드시 필요합니까? 비용이 발생합니까?</b></summary>

<br>

전사(스크립트) 단계까지는 CLI 없이 동작합니다. 전사 결과를 개념 단위로 정리하는 **전체
위키 생성**에만 로그인된 Claude Code CLI가 필요하며, 이 단계는 사용량(또는 구독)에 따라
Anthropic에 과금됩니다.

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

아래 `uv`/`flet` 명령을 감싼 편의 스크립트가 있습니다(모두 `--help` 지원).

```bash
python scripts/setup.py         # 환경 구성: uv sync --extra vosk (GPU 가속: --gpu 추가)
python scripts/run.py           # 앱 실행(GUI). CLI: python scripts/run.py --cli [옵션]
python scripts/build.py         # 네이티브 데스크톱 앱 빌드 (GPU: --gpu)
python scripts/test.py          # 테스트 실행 (uv run pytest tests/; 인자는 그대로 pytest 로 전달)
```

각 단계를 직접 실행하려면 아래를 참고하십시오.

**설치**

```bash
uv sync --extra vosk   # 기본. GPU 가속까지 포함하려면: uv sync --extra vosk --extra gpu
```

**설정**

1. [Claude Code CLI](https://claude.com/claude-code)를 설치하고 `claude login`으로 로그인합니다(전사 단계까진 불필요).
2. `config/channel.yaml`의 `videos` 목록에 대상 영상/채널 URL을 추가합니다.

> STT는 기본값 `device: auto` + `compute_type: auto`로 동작합니다. GPU가 있으면 `float16`,
> 없으면 `int8`을 자동 선택하며, GPU 사용이 실패하면 CPU(int8)로 폴백합니다.
> (`stt.device: cuda`로 고정하면 CTranslate2용 CUDA 런타임이 필요합니다.)
> 기본 엔진은 `faster-whisper`(AI, 정확도 우선)이며, 완전 오프라인·초경량 대안으로
> `stt.engine: vosk`를 선택할 수 있습니다(정확도는 더 낮음). GUI에서는 드롭다운으로 고릅니다.

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

**빌드 (네이티브 데스크톱 앱 + 설치기)**

실행 OS를 감지해 flet 네이티브 앱을 빌드하고, Windows에서는 [Velopack](https://velopack.io) 설치기까지 만듭니다.

```bash
python scripts/build.py                 # CPU 번들 → Velopack 설치기(dist/velopack/)
python scripts/build.py --gpu-runtime   # cuBLAS 온디맨드 에셋 zip(GPU 가속 배포용)
python scripts/build.py --no-installer  # CPU 번들 폴더/zip 만(설치기 생략)
```

- 결과물(기본): `dist/velopack/` — `YtKnowledgeExtractor-win-Setup.exe`(설치기) + `*-full/delta.nupkg`(업데이트 패키지) + `releases.win.json`(매니페스트). 이 폴더 **전체**를 GitHub 릴리스에 올리면 앱이 자동 업데이트(변경분만 받는 **델타** 포함)에 사용합니다.
- **GPU는 온디맨드**: CPU 설치기에는 cuBLAS를 넣지 않습니다(가볍게). NVIDIA 사용자는 앱의 **고급 옵션 → GPU 가속 다운로드**로 cuBLAS 런타임을 받습니다. 이 런타임 zip은 `--gpu-runtime`으로 만들어 `gpu-runtime-cu12` 릴리스에 한 번 올려 둡니다(앱 버전과 무관). GPU가 없으면 앱이 자동으로 CPU(int8)로 폴백합니다.
- 사전 준비(Windows): Visual Studio "Desktop development with C++" 워크로드 + [Velopack CLI](https://velopack.io)(`dotnet tool install -g vpk`)가 필요합니다. Flutter SDK는 `flet build`가 필요 시 자동으로 다운로드합니다.

**코드 서명 (선택, Windows)**

미서명 배포본은 SmartScreen 경고가 뜹니다(위 [자주 묻는 질문](#자주-묻는-질문) 참고). 서명하면
게시자 이름이 표시되고, 정식 CA 인증서면 경고도 사라집니다. 지문을 지정하면 **Velopack이
설치기와 앱 번들 전체 파일을 서명**합니다.

```bash
# 1) self-signed 인증서 생성(한 번). 출력된 지문(Thumbprint)을 복사합니다.
pwsh -File scripts/make_selfsigned_cert.ps1
# 2) 지문을 환경 변수로 지정하고 빌드하면 Velopack 이 전 파일을 자동 서명합니다(PowerShell).
$env:YKE_SIGN_THUMBPRINT = "<복사한 지문>"
python scripts/build.py
```

- 인증서를 지정하지 않으면 서명을 건너뛰고 미서명으로 빌드합니다(기본 동작).
- **self-signed 의 한계**: 그 인증서를 "신뢰할 수 있는 루트/게시자"에 설치한 PC 에서만
  신뢰되며 **SmartScreen 경고는 없애지 못합니다**(본인·소수 배포용). 넓은 배포에는 정식 CA
  인증서나 오픈소스 무료 서명([SignPath Foundation](https://signpath.org/))이 필요합니다.
- 정식 `.pfx` 인증서가 있으면 지문 대신 `YKE_SIGN_PFX`(+`YKE_SIGN_PFX_PASSWORD`)로 지정합니다.
- 이미 빌드된 폴더를 재서명하려면: `python scripts/sign.py dist/yke-cpu-windows`

**테스트**

```bash
uv run pytest tests/
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
