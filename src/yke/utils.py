"""공통 유틸: 타임스탬프 변환, LLM JSON 응답 파싱, 협조적 취소 신호."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit


class StoppedError(Exception):
    """``should_stop()`` 콜백이 True 가 되어 진행 중이던 작업을 중간에 멈췄다는 신호.

    STT 처럼 오래 걸리는 단계가 세그먼트/청크 단위로 협조적 취소를 감지했을 때 이 예외를
    던져, run_pipeline 이 이를 '실패'가 아니라 '중단'으로 구분해 처리하게 한다.
    """


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


def channel_folder_slug(url: str) -> str | None:
    """채널/재생목록 URL 에서 산출물을 정리할 폴더 이름을 뽑는다.

    ``/@handle``·``/channel/ID``·``/c/name``·``/user/name`` 은 핸들/이름을 그대로 쓰고,
    재생목록(``list=``)은 재생목록 ID 를 쓴다. 폴더 이름으로 쓸 수 없는 문자는 밑줄로
    치환한다. 개별 영상 URL 이거나 이름을 뽑을 수 없으면 ``None`` (기존처럼 저장 폴더에
    바로 정리).
    """
    if not is_channel_or_playlist_url(url):
        return None
    parts = urlsplit(url.strip())
    qs = parse_qs(parts.query)
    if qs.get("list") and qs["list"][0]:
        raw = f"playlist_{qs['list'][0]}"
    else:
        segments = [s for s in parts.path.split("/") if s]
        if not segments:
            return None
        if segments[0].startswith("@"):
            raw = segments[0][1:]
        elif segments[0] in ("channel", "c", "user") and len(segments) >= 2:
            raw = segments[1]
        else:
            return None
    slug = re.sub(r"[^\w\-]+", "_", raw).strip("_")
    return slug or None


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
