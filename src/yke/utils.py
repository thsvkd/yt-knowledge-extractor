"""공통 유틸: 타임스탬프 변환, LLM JSON 응답 파싱."""

from __future__ import annotations

import json
import re


def is_channel_or_playlist_url(url: str) -> bool:
    """개별 영상이 아니라 채널/재생목록 URL 인지 판별한다(확장 대상).

    개별 영상(watch?v=, youtu.be/<id>, /shorts/, /embed/)은 그대로 두고, 채널
    (/@handle, /channel/, /c/, /user/)이나 재생목록(list=, /playlist)은 최근 N개
    영상으로 확장한다. ``watch?v=...&list=...``처럼 영상+목록이 섞이면 개별 영상으로
    본다(watch 우선).
    """
    u = url.strip().lower()
    if not u:
        return False
    if any(s in u for s in ("watch?v=", "youtu.be/", "/shorts/", "/embed/")):
        return False
    if "list=" in u or "/playlist" in u:
        return True
    return any(s in u for s in ("/@", "/channel/", "/c/", "/user/"))


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
