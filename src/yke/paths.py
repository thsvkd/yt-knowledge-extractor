"""영상별 데이터 디렉터리 레이아웃 (중간 산출물 캐싱용).

폴더 이름은 사람이 알아볼 수 있도록 ``<영상 제목> [<영상ID>]`` 형식을 쓴다. 제목만
쓰면 같은 제목의 영상끼리 충돌하고 캐시(재실행 이어하기)를 폴더 이름만으로 찾을 수
없으므로, 뒤에 영상 ID 를 대괄호로 붙여 유일성과 조회 가능성을 함께 확보한다.
제목을 모를 때(캐시 조회처럼 네트워크 조회 전)는 :func:`find_video_dir` 로 ID 접미사를
보고 기존 폴더를 찾는다. 예전 버전이 만든 ``<영상ID>`` 폴더도 그대로 인식하며, 제목을
알게 되면 새 이름으로 옮겨 준다(실패해도 기존 폴더를 그대로 쓴다).
"""

from __future__ import annotations

import re
from pathlib import Path

# 윈도우에서 파일/폴더 이름에 쓸 수 없는 문자(제어문자 포함). macOS/리눅스는 더
# 관대하지만, 배포 대상이 윈도우이므로 가장 좁은 규칙에 맞춘다.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 윈도우 예약 장치 이름(대소문자 무관). 이 이름의 폴더는 만들 수 없다.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# 제목 부분의 최대 길이. 뒤에 " [11자 ID]" 와 파일명이 더 붙으므로, 윈도우의 260자
# 경로 제한에 여유를 두고 넉넉히 잘라 둔다.
_MAX_TITLE_LEN = 80


def sanitize_folder_name(title: str | None, *, max_len: int = _MAX_TITLE_LEN) -> str:
    """영상 제목을 폴더 이름으로 쓸 수 있게 다듬는다(못 쓰면 빈 문자열).

    금지 문자는 공백으로 바꾸고 연속 공백을 하나로 접은 뒤, 길이를 잘라 낸다. 윈도우는
    이름 끝의 점·공백을 저장하지 못하므로 제거하고, 예약 장치 이름(CON, NUL 등)은 앞에
    밑줄을 붙여 피한다.
    """
    if not title:
        return ""
    name = _INVALID_CHARS.sub(" ", title)
    name = re.sub(r"\s+", " ", name).strip()
    name = name[:max_len].strip().rstrip(". ")
    if not name:
        return ""
    if name.upper().split(".")[0] in _RESERVED_NAMES:
        name = f"_{name}"
    return name


def video_dir_name(video_id: str, title: str | None = None) -> str:
    """영상 폴더 이름을 만든다: ``<제목> [<영상ID>]`` (제목을 못 쓰면 ``<영상ID>``)."""
    slug = sanitize_folder_name(title)
    return f"{slug} [{video_id}]" if slug else video_id


def _has_outputs(p: Path) -> bool:
    """이 폴더에 이 영상의 산출물(메타/트랜스크립트)이 이미 들어 있는지."""
    return any((p / name).exists() for name in ("meta.json", "transcript.raw.json", "transcript.json"))


def find_video_dir(data_dir: Path | str, video_id: str, *, prefer: Path | None = None) -> Path | None:
    """``data_dir`` 아래에서 이 영상의 기존 폴더를 찾는다(없으면 None).

    제목이 바뀌어도 ``[<영상ID>]`` 접미사로 찾을 수 있고, 예전 버전이 만든 ``<영상ID>``
    폴더도 인식한다(제목 폴더가 있으면 그쪽을 우선). 어쩌다 같은 영상의 폴더가 여럿이면
    (제목이 바뀐 뒤 이동이 실패한 경우 등) 산출물이 들어 있는 쪽을 골라 캐시를 잃지 않게
    하고, 그중 ``prefer`` (지금 쓰려는 이름)가 있으면 그것을 쓴다.
    """
    root = Path(data_dir)
    if not root.is_dir():
        return None
    suffix = f"[{video_id}]"
    candidates = [p for p in sorted(root.iterdir()) if p.is_dir() and p.name.endswith(suffix)]
    legacy = root / video_id
    if legacy.is_dir() and legacy not in candidates:
        candidates.append(legacy)
    if not candidates:
        return None
    pool = [p for p in candidates if _has_outputs(p)] or candidates
    if prefer is not None and prefer in pool:
        return prefer
    return pool[0]


def _relocate(existing: Path, desired: Path) -> Path:
    """기존 폴더를 새 이름으로 옮긴다. 실패하면(사용 중 등) 기존 폴더를 그대로 쓴다."""
    if existing == desired or desired.exists():
        return existing
    try:
        existing.rename(desired)
    except OSError:
        return existing
    return desired


class VideoPaths:
    """``data/<영상 제목> [<영상ID>]/`` 아래의 산출물 경로를 관리한다."""

    def __init__(
        self,
        data_dir: Path | str,
        video_id: str,
        title: str | None = None,
        *,
        create: bool = True,
    ):
        """
        Args:
            title: 알고 있으면 폴더 이름에 쓴다. 없으면 기존 폴더를 찾아 쓰고, 그것도
                없으면 영상 ID 를 폴더 이름으로 쓴다(예전 레이아웃과 동일).
            create: False 면 폴더를 만들지 않는다(캐시 존재 확인처럼 읽기만 할 때).
        """
        self.video_id = video_id
        data_dir = Path(data_dir)
        desired = data_dir / video_dir_name(video_id, title)
        existing = find_video_dir(data_dir, video_id, prefer=desired)
        if existing is not None and title:
            # 제목을 알게 됐다 — 예전 ID 폴더(또는 제목이 바뀐 폴더)를 새 이름으로 옮긴다.
            existing = _relocate(existing, desired)
        self.root = existing if existing is not None else desired
        if create:
            self.root.mkdir(parents=True, exist_ok=True)

    @property
    def meta(self) -> Path:
        return self.root / "meta.json"

    @property
    def transcript_raw(self) -> Path:
        """원본 트랜스크립트(자막/STT 결과, 규칙 정제까지). 항상 생성된다."""
        return self.root / "transcript.raw.json"

    @property
    def transcript_raw_txt(self) -> Path:
        """원본 트랜스크립트의 사람이 읽는 txt 판(항상 생성)."""
        return self.root / "transcript.raw.txt"

    @property
    def transcript(self) -> Path:
        """LLM 보정본 트랜스크립트(JSON). 자막 보정을 켰을 때만 생성되며 다운스트림의 정본."""
        return self.root / "transcript.json"

    @property
    def transcript_txt(self) -> Path:
        """LLM 보정본의 사람이 읽는 txt 판(보정을 켰을 때만 생성)."""
        return self.root / "transcript.txt"

    @property
    def units(self) -> Path:
        return self.root / "units.json"

    def audio(self) -> Path | None:
        """다운로드된 오디오 파일. 알려진 오디오 컨테이너만 채택하고
        .part/.ytdl 같은 미완성 다운로드나 부수 파일은 제외한다."""
        audio_exts = {".m4a", ".webm", ".mp3", ".opus", ".mp4", ".wav", ".ogg", ".aac"}
        for p in sorted(self.root.glob("audio.*")):
            if p.suffix.lower() in audio_exts:
                return p
        return None
