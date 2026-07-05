"""channel.yaml 설정 로딩 및 검증."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class STTConfig(BaseModel):
    model: str = "large-v3"
    device: str = "auto"
    compute_type: str = "auto"  # auto → GPU 는 float16, CPU 는 int8 (stage3_stt._resolve)
    word_timestamps: bool = True


class SubtitlesConfig(BaseModel):
    use_manual: bool = True  # ① 수동(크리에이터) 자막이 있으면 최우선 사용
    use_auto_fallback: bool = True  # ③ STT 실패 시 최후 폴백으로 유튜브 자동자막 사용


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
