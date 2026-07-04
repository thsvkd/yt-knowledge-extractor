"""영상별 데이터 디렉터리 레이아웃 (중간 산출물 캐싱용)."""

from __future__ import annotations

from pathlib import Path


class VideoPaths:
    """data/<video_id>/ 아래의 산출물 경로를 관리한다."""

    def __init__(self, data_dir: Path, video_id: str):
        self.video_id = video_id
        self.root = Path(data_dir) / video_id
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def meta(self) -> Path:
        return self.root / "meta.json"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.json"

    @property
    def units(self) -> Path:
        return self.root / "units.json"

    def audio(self) -> Path | None:
        """다운로드된 오디오 파일 (확장자는 원본 컨테이너에 따라 다름)."""
        for p in sorted(self.root.glob("audio.*")):
            if p.suffix.lower() not in {".json", ".vtt", ".srt"}:
                return p
        return None
