# 🎬 YouTube Knowledge Extractor

> 좋아하는 유튜버의 여러 영상에 흩어진 지식을 **개념 단위로 통합한, 검색 가능한 위키**로 만들어 줍니다.

한 유튜버의 **말 중심 콘텐츠**(토크·리뷰·인터뷰)를 모아, 영상마다 흩어진 이야기를
개념별로 정리한 **하나의 마크다운 지식베이스(`wiki.md`)**로 뽑아내는 데스크톱 앱입니다.
모든 지식 조각에는 **원본 영상 타임스탬프와 인용 근거**가 붙어 있어 바로 되짚어볼 수 있습니다.

**[⬇️ 최신 버전 다운로드](https://github.com/thsvkd/yt-knowledge-extractor/releases/latest)** · [작동 방식](#-작동-방식) · [자주 묻는 질문](#-자주-묻는-질문)

<!-- TODO: 앱 스크린샷 추가 → docs/screenshot.png 로 저장 후 아래 주석 해제 -->
<!-- ![스크린샷](docs/screenshot.png) -->

---

## ✨ 이런 걸 할 수 있어요

- 📌 **개념 단위 통합** — 영상별 요약을 나열하는 게 아니라, 같은 주제를 여러 영상에서 모아 한곳에.
- 🔎 **검증 가능** — 모든 항목에 타임스탬프 + 원문 인용이 붙어 "정말 그렇게 말했나"를 바로 확인.
- 🧠 **사실/의견 구분** — 각 지식이 사실·의견·팁·정의 중 무엇인지 표시.
- 🎧 **자막이 없어도 OK** — 자막이 없으면 로컬 음성인식(STT)으로 직접 받아쓰기.
- 🖥️ **설치 없이 실행** — 압축 풀고 바로 실행하는 데스크톱 앱(Windows). 파이썬·개발 지식 불필요.
- 🔄 **자동 업데이트** — 새 버전이 나오면 앱 안에서 버튼 한 번으로 업데이트.

---

## 🚀 시작하기 (약 5분)

> [!NOTE]
> 개발 지식이 없어도 괜찮습니다. 아래 순서대로만 따라 하세요. 현재 **Windows** 앱을 제공합니다.
> (macOS·Linux는 아래 [개발자용 — 소스에서 실행](#-개발자용)을 참고하세요.)

### 1. 다운로드

[**최신 릴리스 페이지**](https://github.com/thsvkd/yt-knowledge-extractor/releases/latest)에서 내 PC에 맞는 파일을 받으세요.

| 받을 파일 | 언제 받나요 | 크기 |
| --- | --- | --- |
| **`yke-cpu-windows.zip`** | 대부분의 경우. NVIDIA 그래픽카드가 없거나 잘 모르겠다면 이걸로. | ~145 MB |
| **`yke-gpu-windows.zip`** | **NVIDIA 그래픽카드(CUDA)**가 있어 음성인식을 더 빠르게 하고 싶을 때. | ~745 MB |

> [!TIP]
> 헷갈리면 **CPU 버전**을 받으세요. 잘 돌아갑니다. GPU 버전은 음성인식 **속도**만 빠를 뿐
> 결과물 품질은 사실상 같습니다. (GPU 버전도 그래픽카드가 없으면 자동으로 CPU로 동작합니다.)

### 2. 압축 풀고 실행

1. 받은 `.zip`을 **압축 해제**합니다.
2. 폴더 안의 **`.exe` 파일을 더블클릭**해 실행합니다.

> [!IMPORTANT]
> - 폴더 안에는 실행에 필요한 파일이 함께 들어 있습니다. **`.exe`만 따로 빼내지 말고 폴더째** 두세요.
> - 처음 실행할 때 *"Windows의 PC 보호"* 파란 창이 뜨면 → **추가 정보** → **실행**을 누르세요.
>   (서명되지 않은 개인 앱이라 뜨는 정상적인 경고입니다.)

### 3. (선택) Claude 토큰 준비

이 앱은 두 가지 일을 할 수 있습니다.

- **스크립트(받아쓰기)까지** — 토큰 **불필요**. 영상의 말을 텍스트로 옮기는 데까지.
- **지식 위키까지 (전체)** — 텍스트를 개념 단위로 정리하려면 **Claude(AI) 토큰**이 필요합니다.

> [!TIP]
> 우선 토큰 없이 **"스크립트 추출까지"**로 한 번 돌려보고, 마음에 들면 토큰을 넣어
> 전체 위키 생성으로 넘어가면 됩니다.

토큰을 얻는 방법은 두 가지입니다.

<details>
<summary><b>방법 A — Anthropic API 키 (가장 간단, 쓴 만큼 과금)</b></summary>

<br>

1. <https://console.anthropic.com> 에 가입하고 결제 수단을 등록합니다.
2. **API Keys → Create Key** 로 키를 발급받습니다 (`sk-ant-...`).
3. 이 키를 아래 **4단계**에서 앱에 붙여넣습니다.

</details>

<details>
<summary><b>방법 B — Claude Code 구독 토큰</b></summary>

<br>

Claude 구독이 있고 [Claude Code](https://claude.com/claude-code)가 설치돼 있다면 터미널에서:

```bash
claude setup-token
```

로 발급된 `sk-ant-oat01-...` 토큰을 사용합니다.

</details>

### 4. 앱에서 실행

1. **유튜브 URL** 칸에 영상 주소를 붙여넣습니다. 여러 개면 한 줄에 하나씩. **채널·재생목록 URL**을 넣으면 최근 영상 여러 개를 자동으로 가져옵니다.
2. **저장 폴더**를 지정합니다. (결과물 `wiki.md`와 중간 파일이 여기에 저장됩니다.)
3. **실행 단계**를 고릅니다 — `스크립트 추출까지`(토큰 불필요) 또는 `전체 (지식 문서화까지)`.
4. `전체`를 골랐다면 **고급 옵션**에서 Claude 토큰을 붙여넣고 **토큰 저장**을 누릅니다. 한 번 저장하면 다음부터 자동으로 불러옵니다.
5. **실행** 버튼을 누르고 진행바가 끝나길 기다립니다. 중간에 **중단**할 수 있고, 그때까지 받은 데이터는 저장 폴더에 남습니다.

### 5. 결과 보기

저장 폴더에 만들어진 **`wiki.md`**가 최종 지식베이스입니다.
[Obsidian](https://obsidian.md)·Typora·VS Code 등 마크다운 뷰어로 열면 개념별로 정리된
내용과 원본 타임스탬프를 볼 수 있습니다.

---

## 🔍 작동 방식

내부적으로 7단계 파이프라인을 거칩니다. (CLI와 GUI 모두 같은 코어를 사용합니다.)

| 단계 | 하는 일 |
| --- | --- |
| 0 | 영상/채널 선정 (채널·재생목록이면 최근 N개 자동 확장) |
| 1 | 오디오 + 메타데이터 다운로드 (`yt-dlp`, 오디오만 받아 빠름) |
| 2 | 자막 확인 (크리에이터가 단 **수동 자막** 우선) |
| 3 | 자막이 없으면 **음성인식(STT)** — 로컬 `faster-whisper` |
| 4 | 텍스트 정제 |
| 5 | 영상별 **지식 원자 단위** 추출 — Claude (구조화 JSON) |
| 6 | 영상 간 **통합** → `wiki.md` — Claude (클러스터링·중복제거·상충 표시) |

핵심은 **5·6단계**입니다. 서술형 요약을 그냥 합치는 대신, `개념 / 명제 / 유형(사실·의견·팁·정의)
/ 타임스탬프 / 인용 근거` 형태의 **구조화된 지식 조각**으로 뽑아 개념별로 통합합니다.
덕분에 원본 역추적과 AI 환각(할루시네이션) 검증이 가능합니다.

자세한 기획 의도는 **[docs/SPEC.md](docs/SPEC.md)**를 참고하세요.

---

## 📦 산출물

```
<저장 폴더>/
├─ <video_id>/
│   ├─ meta.json         # 제목·설명·업로드일·챕터
│   ├─ audio.*           # 받은 오디오
│   ├─ transcript.json   # 받아쓴 스크립트 (시작/끝/텍스트)
│   └─ units.json        # 영상별 지식 조각
├─ clusters.json         # 통합 개념 클러스터
└─ wiki.md               # ⭐ 최종 지식베이스
```

---

## ❓ 자주 묻는 질문

<details>
<summary><b>CPU 버전과 GPU 버전, 결과물이 다른가요?</b></summary>

<br>

결과물 품질은 사실상 같습니다. 차이는 **음성인식 속도**뿐입니다. NVIDIA 그래픽카드가
있으면 GPU 버전이 받아쓰기를 훨씬 빠르게 합니다. 없으면 CPU 버전을 쓰세요.

</details>

<details>
<summary><b>"Windows가 앱을 차단했어요"</b></summary>

<br>

서명되지 않은 개인 앱이라 뜨는 정상적인 경고입니다. *"Windows의 PC 보호"* 창에서
**추가 정보 → 실행**을 누르면 됩니다.

</details>

<details>
<summary><b>토큰이 꼭 필요한가요? 비용이 드나요?</b></summary>

<br>

**스크립트(받아쓰기)까지는 토큰 없이** 됩니다. AI가 내용을 개념별로 정리하는 **전체 위키
생성**에만 Claude 토큰이 필요하며, 이 부분은 사용한 만큼 Anthropic에 과금됩니다.

</details>

<details>
<summary><b>업데이트는 어떻게 하나요?</b></summary>

<br>

새 버전이 나오면 앱이 시작할 때 알려주고, 버튼 한 번으로 자동 업데이트됩니다.

</details>

<details>
<summary><b>결과를 그대로 믿어도 되나요?</b></summary>

<br>

아니요 — 이 프로젝트는 **사람의 검토를 전제로** 합니다. 각 항목의 타임스탬프·인용으로
원본과 대조해 확인하세요. (그래서 근거를 함께 남깁니다.)

</details>

---

## 🛠 개발자용

<details>
<summary><b>소스에서 실행 · 빌드 · 테스트 (펼치기)</b></summary>

<br>

**요구사항**: Python 3.11+, [uv](https://docs.astral.sh/uv/)

**설치**

```bash
uv sync
```

**설정**

1. `.env.example` → `.env` 복사 후 토큰 입력 (`CLAUDE_CODE_OAUTH_TOKEN` 또는 `ANTHROPIC_API_KEY`).
2. `config/channel.yaml` 의 `videos` 목록에 대상 영상/채널 URL 추가.

> STT는 기본값 `device: auto` + `compute_type: auto` 로 동작합니다. GPU가 있으면 `float16`,
> 없으면 `int8`을 자동 선택하고, GPU 사용이 실패하면 CPU(int8)로 폴백합니다.
> (`stt.device: cuda` 고정 시 CTranslate2용 CUDA 런타임이 필요합니다.)

**실행 (GUI)**

```bash
uv run yke-gui   # CLI와 동일한 파이프라인 코어를 쓰는 flet 데스크톱 앱
```

**실행 (CLI)**

```bash
uv run yke                        # 전체 파이프라인
uv run yke --stage transcript     # 1~4단계: 스크립트만
uv run yke --stage extract        # 5단계까지: units.json
uv run yke --stage integrate      # 6단계: wiki.md
uv run yke --force                # 캐시 무시하고 재생성
uv run yke --limit 5              # 채널/재생목록에서 최근 5개만
```

**빌드 (네이티브 데스크톱 앱)**

실행한 OS를 감지해 flet 네이티브 앱을 빌드합니다(Windows/macOS/Linux 공통).

```bash
python scripts/build.py         # CPU 전용 버전
python scripts/build.py --gpu   # NVIDIA CUDA 가속 버전
```

- 결과물: `dist/yke-<cpu|gpu>-<platform>/` — 실행파일 + DLL + `data/` 한 세트. **폴더째** 배포·실행합니다. 압축본 `dist/yke-<cpu|gpu>-<platform>.zip` 은 GitHub Releases 업로드용.
- GPU 변형은 STT(faster-whisper→CTranslate2) 가속에 필요한 `nvidia-cublas-cu12` 런타임을 번들에 포함합니다(빌드 동안만 `[project.dependencies]`에 임시 주입). CPU 전용은 이를 빼 더 가볍게 빌드합니다. GPU 번들도 GPU가 없으면 자동으로 CPU(int8)로 폴백합니다.
- 사전 준비(Windows): Visual Studio "Desktop development with C++" 워크로드(없으면 스크립트가 설치 방법을 안내). Flutter SDK는 `flet build`가 필요 시 자동으로 내려받습니다.

**테스트**

```bash
uv run python -m unittest discover -s tests -t .
```

**주요 문서**: 기획 SSoT [docs/SPEC.md](docs/SPEC.md) · 개발 규약 [AGENTS.md](AGENTS.md)

</details>

---

## ⚠️ 범위와 한계 (PoC)

- 개인 학습용 **개념 검증(PoC)**입니다. 통합 결과는 **사람 검토**를 전제로 합니다.
- 미구현: 화자 분리(diarization), 4단계 광고/인사 구간 LLM 클렌징, STT 도구 실측 비교, 자동 검증 파이프라인.

> [!CAUTION]
> 유튜브 다운로드는 서비스 약관상 **그레이존**입니다. 개인 학습 용도로만 사용하고,
> 공개·서비스화 시에는 채널 소유자 허락 또는 공식 API 경로를 검토하세요.
