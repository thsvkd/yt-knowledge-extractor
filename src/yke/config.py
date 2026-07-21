"""channel.yaml 설정 로딩 및 검증."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class STTConfig(BaseModel):
    # faster-whisper(AI, 기본, 정확도 높음) | vosk(경량 오프라인 엔진, non-AI 옵션 — Kaldi
    # 기반, 완전 오프라인·초경량이지만 정확도는 faster-whisper 보다 낮다). 자막이 깨져
    # STT 로 폴백할 때 어느 엔진을 쓸지 고른다. vosk 는 `uv sync --extra vosk` 로 설치해야
    # 쓸 수 있다(stage3_stt_vosk 모듈 참고).
    engine: str = "faster-whisper"
    # auto → 장치별 기본(GPU:large-v3 / CPU:small, stage3_stt._resolve_model).
    # 명시하려면 tiny|base|small|medium|large-v3. (engine: faster-whisper 전용)
    model: str = "auto"
    device: str = "auto"
    compute_type: str = "auto"  # auto → GPU 는 float16, CPU 는 int8 (stage3_stt._resolve)
    # 다운스트림은 세그먼트 시각(seg.start)만 쓰므로 단어 단위 타임스탬프는 불필요 → 끄면 더 빠르다.
    word_timestamps: bool = False
    # 배치 추론(BatchedInferencePipeline)로 처리량↑. GPU 뿐 아니라 CPU 에서도 유의미하게
    # 빠르다(실측 RTF 약 2.6배 개선, stage3_stt 모듈 docstring 참고).
    batched: bool = True
    # GPU 기준 기본값. 실제 적용값은 stage3_stt._effective_batch_size 가 장치·모델별로
    # 다시 낮춘다 — CPU 는 진행률 콜백이 자주 나오도록 4 로, VRAM 이 부족한 큰 GPU 모델도
    # 4 로 상한을 둔다.
    batch_size: int = 16
    # CPU 추론 스레드 수. 0(기본) 이면 물리 코어 수를 자동 감지(psutil)해 적용한다(실측
    # RTF 0.041→0.037, ~10%↑ — stage3_stt 모듈 docstring 참고). 하이퍼스레딩 논리 코어
    # 수(os.cpu_count())를 그대로 쓰면 스레드 경합으로 오히려 느려지므로, 명시값을 넣을
    # 땐 물리 코어 수를 권장한다. (engine: faster-whisper 전용, GPU 에서는 무시됨)
    cpu_threads: int = 0
    # engine="vosk" 일 때만 쓰는 모델 크기. small|large. 언어별로 large 가 없으면(예:
    # 한국어는 small 뿐) small 로 자동 대체된다(stage3_stt_vosk._MODEL_CATALOG 참고).
    vosk_model_size: str = "small"


class SubtitlesConfig(BaseModel):
    use_manual: bool = True  # 수동(크리에이터) 자막을 소스 후보로 쓸지
    use_auto_fallback: bool = True  # 유튜브 자동자막을 소스 후보로 쓸지
    # 소스 우선순위(합의): ① 업로더 제공 수동 자막 > ② 유튜브 자동 생성 자막 > ③ 로컬
    # STT(AI 모델, CPU/GPU). 수동/자동 자막이 없거나 깨져 있으면(예: 10분 영상에 한 줄,
    # 커버리지 미달 — _caption_is_usable 참고) 다음 소스로 넘어가고, 로컬 STT 는 자원을
    # 쓰는 마지막 수단이다. True 로 두면(레거시) STT 를 1순위로 강제한다.
    stt_first: bool = False
    # 자막 완전성 게이트: 자막이 영상 길이의 이 비율 미만만 커버하면 '깨진 자막'으로 보고
    # 건너뛴다(다음 소스로 폴백). 0 이면 커버리지 검사 비활성.
    min_coverage_ratio: float = 0.5
    # 자막 세그먼트가 이 개수 미만이면(예: 한 줄짜리) '깨진 자막'으로 보고 건너뛴다.
    min_caption_segments: int = 2


class LLMConfig(BaseModel):
    # LLM 프로바이더: claude(로컬 Claude Code CLI) | gemini(Google Gemini, 사용자 API 키).
    # 배포된 end-user 는 `claude` CLI 가 없으므로 본인 Gemini API 키(BYOK)로 gemini 를 쓴다.
    # 실제 호출은 provider 에 무관한 llm.make_client(cfg.llm) 팩토리가 고른다.
    provider: str = "claude"
    # 활성 모델 ID. provider 에 맞는 값이어야 한다(claude-* / gemini-*). GUI 는 프로바이더를
    # 바꿀 때 기본 모델도 함께 바꾼다. gemini 인데 claude-* 가 들어오면 GeminiClient 가
    # 기본 gemini 모델로 대체한다(_resolve_model).
    model: str = "claude-opus-4-8"
    max_chars_per_chunk: int = 8000
    # 트랜스크립트 확보(1~4단계) 직후 LLM 으로 자막을 보정할지. 업로더 수동 자막이나 유튜브
    # 자동 자막이 깨진 채(오탈자·구두점 없음·인식 오류) 채택되는 경우를 교정한다(stage_repair).
    # 타임스탬프는 보존하고 텍스트만 고친다. LLM 프로바이더 설정이 있어야 동작한다.
    repair_transcript: bool = False
    # 자막 보정 시 청크(LLM 호출)를 동시에 몇 개까지 처리할지. 보정 시간은 대부분 순차
    # 왕복 대기라, 청크는 서로 독립적이므로 병렬로 돌리면 벽시계 시간이 크게 준다. 무료
    # 티어의 분당 요청 제한(RPM)에 걸리면 실패 청크는 원본을 유지하므로, 제한이 빡빡하면
    # 이 값을 낮춘다(1 이면 순차).
    repair_concurrency: int = 4


class Config(BaseModel):
    videos: list[str]
    language: str = "ko"
    stt: STTConfig = STTConfig()
    subtitles: SubtitlesConfig = SubtitlesConfig()
    llm: LLMConfig = LLMConfig()
    output_dir: str = "output"
    data_dir: str = "data"


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Config(**raw)
