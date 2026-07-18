"""channel.yaml 설정 로딩 및 검증."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class STTConfig(BaseModel):
    # auto → 장치별 기본(GPU:large-v3 / CPU:small, stage3_stt._resolve_model).
    # 명시하려면 tiny|base|small|medium|large-v3.
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


class SubtitlesConfig(BaseModel):
    use_manual: bool = True  # 수동(크리에이터) 자막을 소스 후보로 쓸지
    use_auto_fallback: bool = True  # 유튜브 자동자막을 소스 후보로 쓸지
    # 소스 우선순위(합의): 유튜브 + 업로더 제공 자막(수동 자막)을 1순위로 삼는다.
    # 단, 그 자막이 깨져 있으면(예: 10분 영상에 한 줄, 커버리지 미달 — _caption_is_usable
    # 참고) 예외적으로만 STT(AI 모델)로 폴백한다. True 로 두면 반대로 STT 를 1순위로 쓴다.
    stt_first: bool = False
    # 자막 완전성 게이트: 자막이 영상 길이의 이 비율 미만만 커버하면 '깨진 자막'으로 보고
    # 건너뛴다(다음 소스로 폴백). 0 이면 커버리지 검사 비활성.
    min_coverage_ratio: float = 0.5
    # 자막 세그먼트가 이 개수 미만이면(예: 한 줄짜리) '깨진 자막'으로 보고 건너뛴다.
    min_caption_segments: int = 2


class LLMConfig(BaseModel):
    model: str = "claude-opus-4-8"
    max_chars_per_chunk: int = 8000


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
