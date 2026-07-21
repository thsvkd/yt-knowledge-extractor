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
