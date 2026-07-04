"""1단계: 오디오 + 메타데이터 (+ 선택적 자막) 다운로드 (yt-dlp).

말 중심 콘텐츠이므로 영상 전체가 아닌 오디오만 받는다(bestaudio). 포맷 변환을
하지 않으므로 시스템 ffmpeg 없이도 동작하며, faster-whisper 가 PyAV 로 원본
컨테이너(.m4a/.webm)를 직접 디코딩한다.

트랜스크립트 우선순위(합의): 수동 자막 > faster-whisper STT > 유튜브 자동자막.
따라서 여기서는 수동 자막만 즉시 내려받고, 자동자막은 '가용성'만 기록해 둔다.
자동자막은 STT 가 실패했을 때만 download_auto_subtitle() 로 lazy 하게 받는다.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit, urlunsplit

import yt_dlp

from ..paths import VideoPaths

try:  # ffmpeg 가 있으면 후처리 견고성 향상 (필수는 아님)
    import imageio_ffmpeg

    _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # pragma: no cover
    _FFMPEG = None


def probe(url: str) -> dict:
    """다운로드 없이 메타데이터/자막 가용성 정보를 조회한다."""
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
        return ydl.extract_info(url, download=False)


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


def expand_source(url: str, limit: int | None, *, log=print) -> list[str]:
    """채널/재생목록 URL 을 개별 영상 watch URL 목록으로 확장한다(최근순, 최대 limit 개).

    개별 영상 URL 은 그대로 ``[url]`` 로 돌려준다. ``extract_flat`` 로 메타 조회 없이
    빠르게 항목만 나열하고, ``playlistend`` 로 최근 N개만 가져온다.
    """
    from ..utils import is_channel_or_playlist_url

    if not is_channel_or_playlist_url(url):
        return [url]

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
    urls: list[str] = []
    for e in entries:
        if not e:
            continue
        vid_url = e.get("url") or e.get("webpage_url")
        if not vid_url and e.get("id"):
            vid_url = f"https://www.youtube.com/watch?v={e['id']}"
        if vid_url:
            urls.append(vid_url)
    return urls


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


def ingest(url, info: dict, vpaths: VideoPaths, language: str, subtitles_cfg, force: bool = False) -> dict:
    """오디오와 (있으면) 수동 자막을 내려받고 meta.json 을 반환한다.

    자동자막은 여기서 받지 않는다 — STT 실패 시의 최후 폴백이므로 가용성만 기록한다.
    """
    if vpaths.meta.exists() and vpaths.audio() and not force:
        return json.loads(vpaths.meta.read_text(encoding="utf-8"))

    manual_lang = _pick_lang(info.get("subtitles"), language) if subtitles_cfg.use_manual else None
    auto_lang = (
        _pick_lang(info.get("automatic_captions"), language)
        if subtitles_cfg.use_auto_fallback
        else None
    )

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(vpaths.root / "audio.%(ext)s"),
        "writesubtitles": bool(manual_lang),  # 수동 자막만 즉시 다운로드
        "writeautomaticsub": False,
        "subtitleslangs": [manual_lang] if manual_lang else [],
        "subtitlesformat": "vtt",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if _FFMPEG:
        opts["ffmpeg_location"] = _FFMPEG

    with yt_dlp.YoutubeDL(opts) as ydl:
        dl = ydl.extract_info(url, download=True)

    meta = {
        "id": dl["id"],
        "title": dl.get("title"),
        "description": dl.get("description"),
        "upload_date": dl.get("upload_date"),
        "channel": dl.get("channel") or dl.get("uploader"),
        "duration": dl.get("duration"),
        "chapters": dl.get("chapters"),
        "webpage_url": dl.get("webpage_url"),
        "manual_sub_lang": manual_lang,  # 즉시 받은 수동 자막 (있으면)
        "auto_sub_lang": auto_lang,  # 가용한 자동자막 (STT 실패 시 lazy 다운로드용)
    }
    vpaths.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def download_auto_subtitle(url: str, vpaths: VideoPaths, lang: str) -> "object | None":
    """STT 실패 시 최후 폴백: 유튜브 자동자막만 내려받는다 (오디오 재다운로드 없음)."""
    from pathlib import Path

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
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    exact = Path(vpaths.root) / f"audio.{lang}.vtt"
    if exact.exists():
        return exact
    hits = sorted(Path(vpaths.root).glob("audio.*.vtt"))
    return hits[0] if hits else None
