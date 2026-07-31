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
- **자막 부재 시 STT 대체** — 수동·자동 자막이 모두 없거나 깨졌을 때만 로컬 음성인식(faster-whisper, 또는 완전 오프라인 경량 엔진 sherpa-onnx)으로 전사합니다.
- **원클릭 설치** — 설치기 하나(Windows `Setup.exe` / macOS `Setup.pkg`)로 설치됩니다(Windows는 관리자 권한 불필요). NVIDIA GPU 가속은 별도 버전이 아니라 앱에서 필요할 때 켭니다(Windows 전용, 전체 지식 위키 생성에는 [Claude Code CLI](https://claude.com/claude-code) 설치가 필요합니다).
- **자동 업데이트** — 새 버전은 앱 내에서 바뀐 부분만 받아 자동 갱신됩니다.

---

## 시작하기

> [!NOTE]
> 현재 **Windows**·**macOS(Apple Silicon)** 설치 파일을 제공합니다. Intel Mac과 Linux는
> 아래 [개발 환경](#개발-환경)의 소스 빌드를 참고하십시오.

### 1. 다운로드

[최신 릴리스](https://github.com/thsvkd/yt-knowledge-extractor/releases/latest)에서 본인 OS의 설치기 하나를 받습니다.

| 파일 | 대상 | 크기 |
| --- | --- | --- |
| **`YtKnowledgeExtractor-win-Setup.exe`** | 모든 Windows 사용자. 설치 후 자동 업데이트됩니다. | ~160 MB |
| **`YtKnowledgeExtractor-osx-Setup.pkg`** | macOS 사용자 — **Apple Silicon(M1 이상) 전용**. 설치 후 자동 업데이트됩니다. | — |

> [!TIP]
> **NVIDIA GPU 가속**은 별도 버전이 아니라, 설치 후 앱의 **고급 옵션 → GPU 가속 다운로드**에서
> 켭니다(그때 cuBLAS 런타임 ~900MB를 한 번만 받습니다). GPU 가속은 음성인식 **속도**만 다를 뿐
> 결과물 품질은 동일하며, NVIDIA GPU가 없으면 자동으로 CPU로 동작합니다.
> **Windows 전용 기능**이라 macOS 앱에는 이 항목이 표시되지 않습니다(macOS는 CPU로 동작).

### 2. 설치

#### Windows

1. 받은 `YtKnowledgeExtractor-win-Setup.exe`를 실행합니다. 사용자 폴더(`%LocalAppData%`)에
   설치되고 바탕화면·시작 메뉴 바로가기가 생성됩니다(관리자 권한 불필요). 설치가 끝나면
   앱이 자동으로 실행됩니다.

> [!IMPORTANT]
> - 최초 실행 시 *"Windows의 PC 보호"* 창이 표시되면 **추가 정보 → 실행**을 선택하십시오. 정식
>   CA가 아닌 개인(self-signed) 서명이라 나타나는 정상 경고이며, 이후 자동 업데이트에는 뜨지 않습니다.
> - 드물게 `PathNotFoundException` 오류로 앱이 뜨지 않으면 Windows 보안의 **제어된 폴더
>   액세스** 때문일 수 있습니다. 아래 [자주 묻는 질문](#자주-묻는-질문)을 참고하십시오.

#### macOS

> [!IMPORTANT]
> macOS 배포본은 현재 **Apple 개발자 서명·공증(notarization)을 하지 않은 미서명 빌드**입니다.
> 그래서 **처음 열 때 macOS가 반드시 한 번 막습니다**. 아래 우회 절차를 순서대로 시도하십시오
> (이는 최초 설치 1회에만 필요하며, 이후 앱 내 자동 업데이트에는 나타나지 않습니다).

1. 받은 `YtKnowledgeExtractor-osx-Setup.pkg`를 더블클릭합니다. 설치 위치로 **`/Applications`
   (응용 프로그램)** 또는 **`~/Applications`(사용자 전용)** 을 고를 수 있습니다. 설치가 끝나면
   앱이 자동으로 실행됩니다.
   - 설치되는 앱 이름은 **`YouTube Knowledge Extractor.app`** 입니다.
   - `/Applications`에 설치하면 이후 자동 업데이트가 앱 번들을 교체할 때 **관리자 인증
     팝업**이 뜰 수 있습니다. 이 팝업을 원하지 않으면 `~/Applications`를 고르십시오.

**Gatekeeper(“확인되지 않은 개발자”) 우회 — 위에서부터 순서대로**

1. `.pkg` 파일을 **Control-클릭(우클릭) → 열기** 한 뒤, 나타나는 대화상자에서 **"열기"** 를
   누릅니다. (Finder에서 그냥 더블클릭하면 "열기" 버튼 없이 차단만 됩니다.)
2. 그래도 막히면 **시스템 설정 → 개인정보 보호 및 보안** 을 열고, 화면 아래쪽의
   *"…이(가) 차단되었습니다"* 문구 옆 **"그래도 열기"** 를 누른 뒤 다시 실행합니다.
   - **macOS 15 (Sequoia) 이상**에서는 **앱**에 대한 Control-클릭 우회가 제거되어 이 경로만
     남습니다. 즉 1단계가 통하지 않으면 반드시 여기서 허용해야 합니다.
3. *"…열 수 없습니다. 손상되었기 때문입니다"* 라는 메시지가 뜨면, 다운로드 격리(quarantine)
   속성을 제거한 뒤 다시 엽니다.

   ```bash
   xattr -dr com.apple.quarantine ~/Downloads/YtKnowledgeExtractor-osx-Setup.pkg
   ```

   설치 후 **앱**에서 같은 증상이 나타나면 앱 번들에 대해 같은 명령을 실행합니다.

   ```bash
   xattr -dr com.apple.quarantine "/Applications/YouTube Knowledge Extractor.app"
   # 설치 위치로 ~/Applications 를 골랐다면:
   xattr -dr com.apple.quarantine "$HOME/Applications/YouTube Knowledge Extractor.app"
   ```

> [!NOTE]
> 설치되는 번들 이름 `YouTube Knowledge Extractor.app`은 빌드 시 `vpk --packTitle` 값에서
> 옵니다 — Velopack이 패키징하면서 flet이 만든 `yt-knowledge-extractor.app`을 이 이름으로
> 바꿔 담습니다. 앱을 어디에 설치했는지 확실하지 않으면 Finder의 **응용 프로그램** 폴더에서
> 이 이름을 찾으십시오.

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
| 3 | 자막이 없거나 깨졌을 때만 **음성인식(STT)** — 로컬 `faster-whisper`(기본) 또는 경량 오프라인 `sherpa-onnx` |
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
├─ <영상 제목> [<video_id>]/      # 개별 영상 URL 입력 시 저장 폴더 바로 아래에 정리
│   ├─ meta.json         # 제목·설명·업로드일·챕터
│   ├─ audio.*           # 다운로드한 오디오
│   ├─ transcript.json   # 전사 스크립트 (시작/끝/텍스트)
│   └─ units.json        # 영상별 지식 원자 단위
├─ <채널/재생목록>/               # 채널·재생목록 URL 입력 시 전용 하위 폴더로 정리
│   └─ <영상 제목> [<video_id>]/ ...   # 위와 동일한 구조
├─ transcripts.all.raw.txt   # 모든 영상의 스크립트 합본 (원본)
├─ transcripts.all.txt       # 모든 영상의 스크립트 합본 (자막 보정을 켰을 때)
├─ clusters.json         # 통합 개념 클러스터
└─ wiki.md               # 최종 지식베이스
```

영상 폴더 이름은 **영상 제목**을 쓰고, 제목이 같은 영상끼리 섞이지 않도록 뒤에
`[<video_id>]`를 붙입니다(폴더 이름에 쓸 수 없는 문자는 공백으로 바뀝니다). 예전 버전이
만든 `<video_id>` 폴더는 그대로 인식하며, 다시 실행하면 새 이름으로 옮겨집니다.

**`transcripts.all.raw.txt`**는 이번 실행에서 확보한 **모든 영상의 스크립트를 한 파일로**
이어 붙인 합본입니다(영상마다 제목·채널·길이·소스·URL 헤더가 붙습니다). 중간에 중단해도
그때까지 처리한 영상까지의 합본이 남습니다. 자막 보정을 켜면 보정본 합본
`transcripts.all.txt`도 함께 생성됩니다.

---

## 자주 묻는 질문

<details>
<summary><b>CPU 버전과 GPU 버전의 결과물이 다릅니까?</b></summary>

<br>

결과물 품질은 동일합니다. 차이는 음성인식 **속도**뿐입니다. NVIDIA GPU가 있으면 GPU
버전이 전사를 더 빠르게 처리합니다. GPU가 없으면 CPU 버전을 사용하십시오.

**GPU 가속은 NVIDIA GPU + Windows 에서만 제공됩니다.** macOS는 CPU로 동작하며, 앱에
GPU 관련 UI(고급 옵션의 "GPU 가속" 항목)가 아예 표시되지 않습니다.

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
<summary><b>macOS가 앱을 차단합니다 / "손상되었기 때문에 열 수 없습니다".</b></summary>

<br>

macOS 배포본은 **Apple 개발자 서명·공증(notarization)을 하지 않은** 빌드입니다. 그래서
브라우저로 내려받은 파일에 붙는 **격리(quarantine) 속성**을 Gatekeeper가 확인하고 실행을
막습니다(파일이 실제로 깨진 것이 아닙니다 — "손상되었습니다"라는 문구도 서명 검증 실패를
가리키는 macOS의 표현입니다). 다음을 위에서부터 순서대로 시도하십시오.

1. `.pkg` 파일을 **Control-클릭(우클릭) → 열기** 후 대화상자에서 **"열기"**.
2. 그래도 막히면 **시스템 설정 → 개인정보 보호 및 보안** 아래쪽의 *"…이(가) 차단되었습니다"*
   옆 **"그래도 열기"**. macOS 15(Sequoia) 이상은 앱의 Control-클릭 우회가 제거되어 이 경로만
   남습니다.
3. 그래도 "손상되었기 때문에 열 수 없습니다"가 뜨면 터미널에서 격리 속성을 제거합니다.

   ```bash
   xattr -dr com.apple.quarantine ~/Downloads/YtKnowledgeExtractor-osx-Setup.pkg
   # 설치 후 앱에서 같은 증상이면(설치 위치로 ~/Applications 를 골랐다면 그쪽 경로로):
   xattr -dr com.apple.quarantine "/Applications/YouTube Knowledge Extractor.app"
   ```

> 이 절차는 **최초 1회 설치에만** 필요합니다. 이후 앱 내 자동 업데이트로 받는 새 버전에는
> 격리 속성이 붙지 않습니다(프로그램이 직접 내려받기 때문).

</details>

<details>
<summary><b>Claude Code CLI가 반드시 필요합니까? 비용이 발생합니까?</b></summary>

<br>

전사(스크립트) 단계까지는 CLI 없이 동작합니다. 전사 결과를 개념 단위로 정리하는 **전체
위키 생성**에만 로그인된 Claude Code CLI가 필요하며, 이 단계는 사용량(또는 구독)에 따라
Anthropic에 과금됩니다.

</details>

<details>
<summary><b>(Windows 한정) 실행하면 <code>PathNotFoundException</code>(문서 폴더 관련) 오류가 나며 앱이 뜨지 않습니다.</b></summary>

<br>

이 항목은 **Windows 한정**입니다. Windows 보안의 **제어된 폴더 액세스**(랜섬웨어 방지 기능)가 서명되지 않은 이 앱이 "문서"
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
python scripts/setup.py         # 환경 구성: uv sync --extra sherpa (GPU 가속: --gpu 추가)
python scripts/run.py           # 앱 실행(GUI). CLI: python scripts/run.py --cli [옵션]
python scripts/build.py         # 네이티브 데스크톱 앱 빌드 + 설치기 (GPU 온디맨드 에셋까지: --gpu-runtime)
python scripts/deploy.py        # 버전 확인 -> 빌드 -> 릴리스 노트 생성 -> GitHub 릴리스 업로드
python scripts/test.py          # 테스트 실행 (uv run pytest tests/; 인자는 그대로 pytest 로 전달)
```

각 단계를 직접 실행하려면 아래를 참고하십시오.

**설치**

```bash
uv sync --extra sherpa # 기본. GPU 가속까지 포함하려면: uv sync --extra sherpa --extra gpu
```

**설정**

1. [Claude Code CLI](https://claude.com/claude-code)를 설치하고 `claude login`으로 로그인합니다(전사 단계까진 불필요).
2. `config/channel.yaml`의 `videos` 목록에 대상 영상/채널 URL을 추가합니다.

> STT는 기본값 `device: auto` + `compute_type: auto`로 동작합니다. GPU가 있으면 `float16`,
> 없으면 `int8`을 자동 선택하며, GPU 사용이 실패하면 CPU(int8)로 폴백합니다.
> (`stt.device: cuda`로 고정하면 CTranslate2용 CUDA 런타임이 필요합니다.)
> 기본 엔진은 `faster-whisper`(AI, 정확도 우선)이며, 완전 오프라인·초경량 대안으로
> `stt.engine: sherpa`를 선택할 수 있습니다(정확도는 더 낮음). GUI에서는 드롭다운으로 고릅니다.
> 경량 엔진은 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)를 쓰며, 한국어 모델
> (Moonshine tiny, 49MB)을 최초 1회 `~/.cache/yke/sherpa-models/`에 내려받습니다. GPU 없이
> CPU만으로 실시간의 수십 배 속도로 처리합니다(실측 RTF 0.018, Apple Silicon 기준).

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

실행 OS를 감지해 flet 네이티브 앱을 빌드하고, Windows·macOS 에서는 [Velopack](https://velopack.io) 설치기까지 만듭니다.
빌드는 **각 OS에서 로컬로** 실행합니다(크로스 컴파일하지 않습니다).

```bash
python scripts/build.py                 # Velopack 설치기(dist/velopack/)만 빌드
python scripts/build.py --gpu-runtime   # 설치기 빌드 + cuBLAS 온디맨드 에셋 zip(GPU 가속 배포용, Windows)
```

- 결과물(기본): `dist/velopack/`. 릴리스에 올리는 파일은 OS별로 다음 네 종류입니다(파일명이
  `-win-`/`-osx-` 로 갈려 한 릴리스에 같이 있어도 충돌하지 않습니다).

  | 채널 | 설치기 | 업데이트 페이로드 | 피드 |
  | --- | --- | --- | --- |
  | `win` (Windows) | `*-Setup.exe` | `*-<버전>-full.nupkg` · `*-<버전>-delta.nupkg` | `releases.win.json` |
  | `osx` (macOS) | `*-Setup.pkg` | `*-<버전>-osx-full.nupkg` · `*-<버전>-osx-delta.nupkg` | `releases.osx.json` |

  `Portable.zip`(대용량)·`RELEASES`(레거시)·`assets.*.json`(로컬 인덱스)은 GithubSource 가 쓰지
  않으므로 올리지 않습니다. 델타(`*-delta.nupkg`)는 직전 릴리스가 있을 때만 만들어집니다.
- **GPU는 온디맨드(Windows 전용)**: CPU 설치기에는 cuBLAS를 넣지 않습니다(가볍게). NVIDIA 사용자는 앱의 **고급 옵션 → GPU 가속 다운로드**로 cuBLAS 런타임을 받습니다. 이 런타임 zip은 `--gpu-runtime`으로 만들어 `gpu-runtime-cu12` 릴리스에 한 번 올려 둡니다(앱 버전과 무관). GPU가 없으면 앱이 자동으로 CPU(int8)로 폴백합니다.
- 사전 준비(Windows): Visual Studio "Desktop development with C++" 워크로드 + [Velopack CLI](https://velopack.io)(`dotnet tool install -g vpk`)가 필요합니다. Flutter SDK는 `flet build`가 필요 시 자동으로 다운로드합니다.
- 사전 준비(macOS): **전체 Xcode**(App Store에서 설치 후 `sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer`) + **CocoaPods**(`brew install cocoapods`) + [Velopack CLI](https://velopack.io)(`dotnet tool install -g vpk` — [.NET SDK](https://dotnet.microsoft.com/download) 8 이상 필요). Command Line Tools(`xcode-select --install`)만으로는 빌드되지 않습니다 — Flutter의 macOS 데스크톱 빌드가 전체 Xcode를 요구합니다. macOS 빌드는 **Apple Silicon 전용(`--arch arm64`)**입니다: `cryptography`(google-genai가 의존)가 49.0.0부터 macOS 휠을 arm64만 배포해, universal 빌드의 x86_64 레그가 소스 컴파일로 떨어져 실패합니다. Intel까지 지원하려면 `cryptography<49`로 되돌려야 합니다. `vpk` 는 앱이 쓰는 `velopack` 파이썬 패키지와 **같은 1.2.0 계열**을 쓰십시오(메이저·마이너가 어긋나면 패키지 형식이 맞지 않습니다). Flutter SDK는 `flet build`가 자동으로 받습니다.

**배포 (버전 릴리스 자동화)**

`pyproject.toml`의 `version`(SSOT)을 먼저 올린 뒤(직접 수행 — 이전 릴리스와 버전이 같으면
진행되지 않습니다) 실행하면, 빌드부터 GitHub 릴리스 업로드까지 한 번에 처리합니다.
(`src/yke/__init__.py`의 `__version__`은 빌드 시 여기서 자동으로 반영되는 결과물이라 직접
고치지 않습니다 — flet build가 앱을 site-packages로 정식 설치하지 않고 `src/`를 그대로
복사해 넣어서, 배포된 앱 안에서는 `importlib.metadata`로 버전을 읽을 수 없기 때문입니다.)

```bash
python scripts/deploy.py            # 버전 확인 -> 빌드 -> 릴리스 노트 생성 -> 릴리스 생성/업로드
python scripts/deploy.py --dry-run  # 릴리스 노트만 생성해 출력(빌드·업로드 없음)
python scripts/deploy.py --force    # 이미 올라간 같은 이름의 에셋을 덮어쓰며 재실행(복구용)
```

**2단계 릴리스 — 두 OS의 에셋을 한 태그에 모읍니다**

크로스 컴파일이 되지 않으므로 각 OS에서 한 번씩 실행합니다. 같은 스크립트가 상황을 보고
"릴리스 생성" 또는 "기존 릴리스에 에셋 추가"로 갈립니다.

1. **첫 번째 OS** — `python scripts/deploy.py` → 태그와 GitHub 릴리스를 새로 만들고, 릴리스
   노트를 생성한 뒤 그 OS의 에셋을 업로드합니다.
2. **두 번째 OS** — **반드시 같은 커밋·같은 버전**을 체크아웃한 뒤 `python scripts/deploy.py`
   → 이미 있는 릴리스를 감지해 **에셋만 추가**합니다(릴리스 노트를 다시 만들지 않습니다).
   - ⚠️ 다른 커밋에서 두 번째 OS를 빌드하면 같은 버전 태그 안에 **내용이 다른 두 빌드**가
     섞입니다. 스크립트가 이를 강제하지 않으므로 사람이 지켜야 합니다.
3. **왜 한 태그에 모아야 하는가** — Velopack의 델타 계산(`vpk download github`)은 **최신 릴리스
   10개**만 훑습니다. OS별로 릴리스를 나누면 릴리스가 두 배 속도로 쌓여 직전 버전이 그 창
   밖으로 밀려나고, 그러면 델타가 조용히 사라져 사용자가 매번 전체 패키지(100MB 단위)를 받게
   됩니다. 파일명은 `-win-`/`-osx-` 접미어로 갈리므로 한 릴리스에 같이 둬도 충돌하지 않습니다.
4. 업로드가 중간에 끊겼거나 같은 OS를 다시 올려야 하면 `--force`로 재실행합니다(같은 이름의
   기존 에셋을 교체합니다).

- 릴리스 노트는 이전 릴리스 태그 이후의 git 커밋 로그를 `claude -p`에 넘겨 생성합니다. 작성
  지침은 `scripts/release_notes_guide.md`에 있으며, 톤·형식을 바꾸고 싶으면 이 파일을 고치면
  됩니다. **노트는 1단계에서 한 번만 생성**되므로 두 OS의 설치기를 모두 설명해야 합니다.
- 사전 준비: 위 빌드 사전 준비 + [GitHub CLI](https://cli.github.com/)(`gh auth login`) +
  [Claude Code CLI](https://claude.com/claude-code)(`claude login`).
- GPU 온디맨드 런타임(`gpu-runtime-cu12` 릴리스)은 앱 버전과 무관해 이 스크립트가 다루지
  않습니다. 필요할 때 `python scripts/build.py --gpu-runtime`으로 따로 올려 둡니다.

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
- 이미 빌드된 폴더를 재서명하려면: `python scripts/sign.py dist/yke-base-windows`

**macOS 코드 서명·공증은 현재 범위 밖입니다.**

- macOS 빌드는 **미서명·미공증**으로 배포합니다(Apple Developer Program 유료 멤버십이 필요해
  이번 범위에서 제외했습니다). 그 결과 사용자는 최초 설치 시 위
  [Gatekeeper 우회 절차](#macos)를 한 번 거쳐야 합니다 — README·릴리스 노트에서 이 안내를
  빼면 "설치가 아예 안 된다"는 문의로 돌아옵니다.
- 향후 Apple Developer ID 인증서를 도입하면 별도 스크립트 없이 Velopack 패키징 단계에
  `vpk pack --signAppIdentity <Developer ID Application: …> --notaryProfile <프로필>` 을 붙여
  서명·공증을 함께 처리합니다.

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
