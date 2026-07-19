"""1단계: 메타데이터 · 자막 · (필요할 때만) 오디오 다운로드 (yt-dlp).

성능 원칙: **오디오 원본은 로컬 STT 가 실제로 필요할 때만 내려받는다.** 수동/자동 자막
으로 트랜스크립트를 확보할 수 있으면 오디오는 아예 받지 않아 빠르게 끝난다. 그래서 이
모듈은 세 가지를 분리해 제공한다.

  * :func:`probe` / :func:`build_meta` — 다운로드 없이 메타데이터·자막 가용성만 확인.
  * :func:`download_manual_subtitle` / :func:`download_auto_subtitle` — 자막 .vtt 만
    내려받는다(오디오 없음).
  * :func:`download_audio` — bestaudio 만 내려받는다(자막 폴백이 STT 로 갈 때만 호출).

말 중심 콘텐츠이므로 영상 전체가 아닌 오디오만 받는다(bestaudio). 포맷 변환을 하지
않으므로 시스템 ffmpeg 없이도 동작하며, faster-whisper 가 PyAV 로 원본 컨테이너
(.m4a/.webm)를 직접 디코딩한다.

트랜스크립트 우선순위(합의): 수동 자막 > 유튜브 자동 자막 > faster-whisper STT.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import yt_dlp

from ..paths import VideoPaths

try:  # ffmpeg 가 있으면 후처리 견고성 향상 (필수는 아님)
    import imageio_ffmpeg

    _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    _FFMPEG = None


# --- 채널 확장 결과 항목 -----------------------------------------------------


@dataclass
class VideoEntry:
    """채널/재생목록 확장(또는 개별 영상 입력) 하나를 나타낸다.

    채널 분석(flat 나열) 단계에서 이미 알아낸 ``availability`` 를 실어, 재생 불가한
    영상(멤버 전용/비공개 등)을 영상별 처리에서 재조회(probe) 없이 즉시 스킵할 수 있게
    한다. ``availability`` 가 없으면(개별 영상 입력이거나 flat 이 안 알려주면) 기존처럼
    영상별 probe 로 확인한다.
    """

    url: str
    video_id: str | None = None
    title: str | None = None
    availability: str | None = None


# --- 재생 불가 판정 / 실패 사유 분류 ----------------------------------------
#
# 채널 분석의 flat 항목에는 종종 ``availability`` 가 실려 온다(subscriber_only 등).
# 이 값이 '인증 없이는 재생 불가'를 뜻하면, 오디오/자막을 받아보려 시도할 것도 없이
# 곧바로 스킵한다(빠른 스킵). 값이 없으면 정상 경로로 진행하고, 실제 다운로드가 실패하면
# 아래 classify_download_failure 로 메시지를 보고 사유를 분류한다.

# yt-dlp availability 값 → (사유 코드, 한글 라벨). 여기 있는 값들은 인증 없이는 재생
# 불가하므로 사전 스킵 대상이다. public/unlisted 는 재생 가능하므로 목록에 없다.
_UNPLAYABLE_AVAILABILITY: dict[str, tuple[str, str]] = {
    "subscriber_only": ("members_only", "멤버 전용 영상"),
    "premium_only": ("premium_only", "프리미엄 전용 영상"),
    "needs_auth": ("needs_auth", "로그인/인증이 필요한 영상"),
    "private": ("private", "비공개 영상"),
}


def unplayable_reason(availability: str | None) -> tuple[str, str] | None:
    """flat 나열에서 얻은 availability 가 재생 불가면 (코드, 한글 라벨)을, 아니면 None."""
    if not availability:
        return None
    return _UNPLAYABLE_AVAILABILITY.get(availability.lower())


# 메시지(소문자)에 이 마커가 있으면 해당 사유로 분류한다. 위에서부터 우선 매칭.
_FAILURE_MARKERS: list[tuple[tuple[str, ...], str, str]] = [
    (("members-only", "members only", "join this channel", "channel's members"),
     "members_only", "멤버 전용 영상"),
    (("private video", "this video is private", "video is private"),
     "private", "비공개 영상"),
    (("premium", "youtube premium"), "premium_only", "프리미엄 전용 영상"),
    (("not available in your country", "not available in your", "isn't available in your",
      "georestrict", "geo restrict", "geo-restrict", "blocked it in your country"),
     "geo_blocked", "지역 제한 영상"),
    (("age-restrict", "age restrict", "confirm your age", "inappropriate for some"),
     "age_restricted", "연령 제한 영상"),
    (("has been removed", "no longer available", "removed by the uploader",
      "account associated with this video has been terminated", "video has been removed",
      "video unavailable", "deleted", "not available", "unavailable"),
     "removed", "삭제되었거나 이용할 수 없는 영상"),
    (("sign in to confirm", "not a bot", "bot"), "auth_or_bot", "로그인/봇 확인이 필요"),
]


def classify_download_failure(exc: Exception | str) -> tuple[str, str]:
    """다운로드/조회 예외(또는 메시지)를 (사유 코드, 한글 라벨)로 분류한다.

    비공개·멤버 전용·지역 제한·삭제됨 등 재시도해도 풀리지 않는 영구 실패를 구분해,
    상위(run_pipeline)가 사용자에게 구체적 사유를 남길 수 있게 한다. 일시적(네트워크)
    오류는 "network", 그 외는 "unknown" 으로 떨어진다.
    """
    msg = str(exc).lower()
    for markers, code, label in _FAILURE_MARKERS:
        if any(m in msg for m in markers):
            return code, label
    if _is_retryable_download_error(exc if isinstance(exc, Exception) else RuntimeError(msg)):
        return "network", "네트워크/일시적 오류"
    return "unknown", "알 수 없는 오류"


# --- 일시적 다운로드 오류 재시도 --------------------------------------------
#
# 유튜브는 서명(URL) 만료나 순간 스로틀링으로 간헐적 403/429/5xx 를 내는 경우가 흔하다
# (동일 URL 이 몇 초 뒤 재시도에 바로 성공하는 것으로 재현 확인됨). '삭제됨'·'비공개'·
# '멤버 전용'처럼 재시도해도 절대 풀리지 않는 오류까지 반복하면 시간만 낭비하므로,
# 메시지에 일시적 오류로 보이는 마커가 있을 때만 짧은 backoff(1초 간격) 후 재시도한다.

_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_RETRY_BACKOFF_S = 1.0  # 1초 간격 고정

_RETRYABLE_MARKERS = (
    "403",
    "forbidden",
    "429",
    "too many requests",
    "500",
    "502",
    "503",
    "504",
    "timed out",
    "timeout",
    "connection reset",
    "temporary failure",
    "econnreset",
)


def _is_retryable_download_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _RETRYABLE_MARKERS)


def _extract_info(
    opts: dict, url: str, *, download: bool, log: Callable[[str], None] = print
) -> dict:
    """``yt_dlp.extract_info`` 를 실행하고, 일시적 오류만 1초 간격 3회 재시도한다.

    영구 실패(비공개/멤버 전용/삭제 등)는 재시도 없이 즉시 예외를 올려, 재생 불가 영상을
    빠르게 실패 처리한다("빠른 스킵" 요구).
    """
    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=download)
        except yt_dlp.utils.DownloadError as exc:
            if attempt >= _DOWNLOAD_MAX_ATTEMPTS or not _is_retryable_download_error(exc):
                raise
            wait = _DOWNLOAD_RETRY_BACKOFF_S
            log(
                f"  다운로드 실패({type(exc).__name__}: {exc}) → {wait:.0f}초 후 재시도 "
                f"({attempt}/{_DOWNLOAD_MAX_ATTEMPTS})..."
            )
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def _download(opts: dict, url: str, *, log: Callable[[str], None] = print) -> dict:
    """``yt_dlp`` 다운로드(download=True)를 실행하고, 일시적 오류는 짧게 재시도한다."""
    return _extract_info(opts, url, download=True, log=log)


def probe(url: str, *, log: Callable[[str], None] = print) -> dict:
    """다운로드 없이 메타데이터/자막 가용성 정보를 조회한다(일시적 오류는 재시도).

    이 한 번의 조회로 자막 테이블(subtitles/automatic_captions)·길이·제목 등을 모두
    얻으므로, 자막만으로 처리 가능한 영상은 여기서 그친다(오디오 미다운로드).
    """
    opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    return _extract_info(opts, url, download=False, log=log)


def _normalize_channel_url(url: str) -> str:
    """채널 루트 URL 이면 최근 영상이 나오는 ``/videos`` 탭으로 정규화한다.

    ``/@handle``·``/channel/UC..``·``/c/name``·``/user/name`` 뒤에 이미 탭
    (``/videos``·``/streams``·``/shorts`` 등)이나 재생목록이 붙어 있으면 그대로 둔다.
    """
    u = url.strip()
    low = u.lower()
    if "list=" in low or "/playlist" in low:
        return u  # 재생목록은 그대로
    if not any(s in low for s in ("/@", "/channel/", "/c/", "/user/")):
        return u
    # 쿼리스트링/프래그먼트(?si=..., ?sub_confirmation=1 등)가 있어도 경로에만 /videos 를
    # 붙이도록 URL 을 분해해 처리한다(쿼리 뒤에 붙으면 URL 이 깨진다).
    parts = urlsplit(u)
    path = parts.path.rstrip("/")
    tail = path.rsplit("/", 1)[-1].lower()
    if tail in ("videos", "streams", "shorts", "featured", "live"):
        return u
    return urlunsplit((parts.scheme, parts.netloc, path + "/videos", parts.query, parts.fragment))


def expand_source(url: str, limit: int | None, *, log=print) -> list[VideoEntry]:
    """채널/재생목록 URL 을 개별 영상 :class:`VideoEntry` 목록으로 확장한다(최근순).

    개별 영상 URL 은 그대로 ``[VideoEntry(url=url)]`` 로 돌려준다. ``extract_flat`` 로
    메타 조회 없이 빠르게 항목만 나열하고, ``playlistend`` 로 최근 N개만 가져온다. flat
    항목이 실어 주는 ``availability`` 를 그대로 보존해, 재생 불가 영상(멤버 전용 등)을
    영상별 처리에서 다시 조회하지 않고 곧바로 스킵할 수 있게 한다.
    """
    from ..utils import is_channel_or_playlist_url

    if not is_channel_or_playlist_url(url):
        return [VideoEntry(url=url)]

    target = _normalize_channel_url(url)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
    }
    if limit and limit > 0:
        opts["playlistend"] = limit
    log(f"채널/재생목록 분석 중… {target}")
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)
    entries = info.get("entries") or []
    if limit and limit > 0:
        entries = entries[:limit]
    out: list[VideoEntry] = []
    for e in entries:
        if not e:
            continue
        vid_id = e.get("id")
        vid_url = e.get("url") or e.get("webpage_url")
        if not vid_url and vid_id:
            vid_url = f"https://www.youtube.com/watch?v={vid_id}"
        if not vid_url:
            continue
        out.append(
            VideoEntry(
                url=vid_url,
                video_id=vid_id,
                title=e.get("title"),
                availability=e.get("availability"),
            )
        )
    return out


def probe_video_id(url: str) -> str:
    return probe(url)["id"]


def _pick_lang(table: dict | None, language: str) -> str | None:
    """자막 언어 테이블에서 대상 언어에 해당하는 키를 고른다 (ko / ko-KR 등)."""
    if not table:
        return None
    if language in table:
        return language
    cands = [k for k in table if k.split("-")[0] == language]
    return sorted(cands, key=len)[0] if cands else None


def build_meta(info: dict, vpaths: VideoPaths, language: str, subtitles_cfg) -> dict:
    """probe 결과(info)에서 meta.json 을 만들어 저장한다 (다운로드 없음).

    자막 가용성(수동/자동 언어)만 기록한다. 실제 자막·오디오는 build_transcript 가
    필요할 때 lazy 하게 내려받는다(오디오는 STT 로 갈 때만).
    """
    manual_lang = _pick_lang(info.get("subtitles"), language) if subtitles_cfg.use_manual else None
    auto_lang = (
        _pick_lang(info.get("automatic_captions"), language)
        if subtitles_cfg.use_auto_fallback
        else None
    )
    meta = {
        "id": info["id"],
        "title": info.get("title"),
        "description": info.get("description"),
        "upload_date": info.get("upload_date"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "chapters": info.get("chapters"),
        "webpage_url": info.get("webpage_url"),
        "availability": info.get("availability"),
        "manual_sub_lang": manual_lang,  # 가용한 수동 자막 (있으면 lazy 다운로드)
        "auto_sub_lang": auto_lang,  # 가용한 자동자막 (STT 폴백 전에 lazy 다운로드)
    }
    vpaths.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def download_manual_subtitle(
    url: str, vpaths: VideoPaths, lang: str, *, log: Callable[[str], None] = print
) -> Path | None:
    """업로더 제공 수동 자막(.vtt)만 내려받는다 (오디오 다운로드 없음)."""
    exact = Path(vpaths.root) / f"audio.{lang}.vtt"
    if exact.exists():
        return exact
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
        "outtmpl": str(vpaths.root / "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    _download(opts, url, log=log)
    if exact.exists():
        return exact
    hits = sorted(Path(vpaths.root).glob(f"audio.{lang}*.vtt")) or sorted(
        Path(vpaths.root).glob("audio.*.vtt")
    )
    return hits[0] if hits else None


def download_auto_subtitle(
    url: str, vpaths: VideoPaths, lang: str, *, log: Callable[[str], None] = print
) -> Path | None:
    """STT 폴백 전 최후 자막 시도: 유튜브 자동자막만 내려받는다 (오디오 다운로드 없음)."""
    opts = {
        "skip_download": True,
        "writeautomaticsub": True,
        "writesubtitles": False,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
        "outtmpl": str(vpaths.root / "audio.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    _download(opts, url, log=log)
    exact = Path(vpaths.root) / f"audio.{lang}.vtt"
    if exact.exists():
        return exact
    hits = sorted(Path(vpaths.root).glob("audio.*.vtt"))
    return hits[0] if hits else None


def download_audio(
    url: str, vpaths: VideoPaths, *, force: bool = False, log: Callable[[str], None] = print
) -> Path:
    """bestaudio 만 내려받는다 (자막으로 처리 불가 → STT 폴백일 때만 호출).

    이미 받아둔 오디오가 있으면(force 아니면) 재다운로드 없이 그대로 쓴다. 다운로드
    후에도 오디오를 못 찾으면 :class:`RuntimeError` 를 올린다.
    """
    existing = vpaths.audio()
    if existing and not force:
        return existing
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(vpaths.root / "audio.%(ext)s"),
        "writesubtitles": False,
        "writeautomaticsub": False,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if _FFMPEG:
        opts["ffmpeg_location"] = _FFMPEG
    _download(opts, url, log=log)
    audio = vpaths.audio()
    if audio is None:
        raise RuntimeError(f"[{vpaths.video_id}] 오디오 다운로드 후에도 오디오 파일을 찾을 수 없습니다.")
    return audio
