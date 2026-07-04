"""공통 유틸: 타임스탬프 변환, LLM JSON 응답 파싱."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """의존성 없이 .env 를 읽어 os.environ 에 주입한다 (이미 설정된 키는 유지)."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def fmt_ts(seconds: float | None) -> str:
    """초 -> "MM:SS" (또는 1시간 넘으면 "H:MM:SS")."""
    total = int(seconds or 0)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def ts_to_seconds(ts: str) -> int:
    """"MM:SS" 또는 "H:MM:SS" -> 초."""
    try:
        parts = [int(p) for p in ts.strip().split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def parse_json_array(text: str) -> list:
    """LLM 응답 텍스트에서 JSON 배열을 최대한 견고하게 추출한다."""
    if not text:
        return []
    cleaned = text.strip()
    # ```json ... ``` 코드펜스 제거
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    # 첫 '[' ~ 마지막 ']' 구간만
    i, j = cleaned.find("["), cleaned.rfind("]")
    if i != -1 and j != -1 and j > i:
        cleaned = cleaned[i : j + 1]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
