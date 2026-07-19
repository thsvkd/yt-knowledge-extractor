"""3단계 STT 대체 엔진: Vosk (경량 오프라인, non-AI 옵션).

`stt.engine: vosk` 일 때 stage3_stt.transcribe() 가 여기로 위임한다. Vosk 는 Kaldi 기반
경량 엔진으로, faster-whisper(대형 Transformer 기반 AI 모델)와 달리 초경량 모델(수십~
수백 MB)로 완전 오프라인 동작한다. 다만 정확도는 faster-whisper 보다 뚜렷이 낮다(예:
한국어 모델은 Zeroth Test 기준 WER 28.1% — alphacephei.com/vosk/models 공개 수치).
GPU 도 필요 없고 모델이 작아 CPU 에서 매우 빠르므로, 정확도보다 속도·완전 오프라인이
중요한 경우의 대안으로 둔다.

설치: `uv sync --extra vosk` (기본 설치에는 포함하지 않는다 — GPU extra 와 같은 이유로,
대부분의 사용자가 쓰지 않는 대안 엔진에 네이티브 의존성을 강제하지 않기 위함).

Vosk 는 16kHz mono 16-bit PCM WAV 만 입력으로 받는다. 이 파이프라인은 오디오를 원본
컨테이너(.m4a/.webm 등)로만 갖고 있으므로(faster-whisper 는 PyAV 로 직접 디코딩해 변환이
불필요했다), 여기서는 ffmpeg(imageio-ffmpeg 번들 바이너리)로 임시 WAV 로 변환한다.

모델은 pip 패키지에 포함되지 않아 최초 사용 시 alphacephei.com 에서 내려받아
``~/.cache/yke/vosk-models/`` 에 캐싱한다(faster-whisper 가 HuggingFace 캐시를 쓰는 것과
같은 패턴).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

from ..models import Segment
from ..utils import StoppedError

ProgressCB = Callable[[float, float], None]

# 언어(ISO 639-1) -> {"small": 모델명, "large": 모델명(있으면)}.
# alphacephei.com/vosk/models 공개 카탈로그에서 확인된 항목만 등록한다(2026-07 기준).
# 대부분 언어는 small 만 있고, large 는 일부 언어에만 존재한다(예: 한국어는 small 뿐).
_MODEL_CATALOG: dict[str, dict[str, str]] = {
    "ko": {"small": "vosk-model-small-ko-0.22"},
    "en": {"small": "vosk-model-small-en-us-0.15", "large": "vosk-model-en-us-0.22"},
    "ja": {"small": "vosk-model-small-ja-0.22", "large": "vosk-model-ja-0.22"},
    "zh": {"small": "vosk-model-small-cn-0.22", "large": "vosk-model-cn-0.22"},
    "ru": {"small": "vosk-model-small-ru-0.22", "large": "vosk-model-ru-0.22"},
    "fr": {"small": "vosk-model-small-fr-0.22", "large": "vosk-model-fr-0.22"},
    "de": {"small": "vosk-model-small-de-0.15", "large": "vosk-model-de-0.21"},
    "es": {"small": "vosk-model-small-es-0.42", "large": "vosk-model-es-0.42"},
    "pt": {"small": "vosk-model-small-pt-0.3"},
    "it": {"small": "vosk-model-small-it-0.22", "large": "vosk-model-it-0.22"},
    "nl": {"small": "vosk-model-small-nl-0.22"},
    "vi": {"small": "vosk-model-small-vn-0.4", "large": "vosk-model-vn-0.4"},
    "hi": {"small": "vosk-model-small-hi-0.22", "large": "vosk-model-hi-0.22"},
    "uk": {"small": "vosk-model-small-uk-v3-nano", "large": "vosk-model-uk-v3"},
    "tr": {"small": "vosk-model-small-tr-0.3"},
    "ar": {"small": "vosk-model-ar-mgb2-0.4"},
    "fa": {"small": "vosk-model-small-fa-0.42", "large": "vosk-model-fa-0.42"},
    "pl": {"small": "vosk-model-small-pl-0.22"},
    "cs": {"small": "vosk-model-small-cs-0.4-rhasspy"},
    "sv": {"small": "vosk-model-small-sv-rhasspy-0.15"},
    "ca": {"small": "vosk-model-small-ca-0.4"},
    "eo": {"small": "vosk-model-small-eo-0.42"},
    "kk": {"small": "vosk-model-small-kz-0.42", "large": "vosk-model-kz-0.42"},
    "ka": {"small": "vosk-model-small-ka-0.42", "large": "vosk-model-ka-0.42"},
}

_MODEL_BASE_URL = "https://alphacephei.com/vosk/models/"
_DECODE_CHUNK_FRAMES = 4000  # KaldiRecognizer 스트리밍 청크(faster-whisper 세그먼트 콜백과 유사한 빈도)

_model_cache: dict[str, object] = {}
_vosk_logging_silenced = False


def _cache_root() -> Path:
    return Path.home() / ".cache" / "yke" / "vosk-models"


def _resolve_model_name(language: str, size: str, *, log: Callable[[str], None]) -> str:
    """언어·크기로 Vosk 모델명을 확정한다. 미지원 언어는 명확한 예외로 알린다."""
    lang_key = language.split("-")[0].lower()
    catalog = _MODEL_CATALOG.get(lang_key)
    if not catalog:
        supported = ", ".join(sorted(_MODEL_CATALOG))
        raise ValueError(
            f"Vosk 는 언어 '{language}' 를 지원하지 않습니다. 지원 언어: {supported} "
            "(engine: faster-whisper 를 쓰거나 config 의 language 를 바꾸세요)."
        )
    if size in catalog:
        return catalog[size]
    log(f"  Vosk: 언어 '{language}' 에는 '{size}' 모델이 없어 small 로 대체합니다.")
    return catalog["small"]


def _ensure_model(model_name: str, *, log: Callable[[str], None]) -> Path:
    """모델이 로컬 캐시에 없으면 내려받아 압축을 푼다. 이미 있으면 그대로 재사용한다."""
    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)
    model_dir = root / model_name
    if model_dir.exists():
        return model_dir

    import urllib.request
    import zipfile

    url = _MODEL_BASE_URL + model_name + ".zip"
    zip_path = root / f"{model_name}.zip"
    log(f"  Vosk 모델 다운로드 중: {model_name} ({url}) — 최초 1회만 필요합니다...")
    try:
        urllib.request.urlretrieve(url, zip_path)  # noqa: S310 (고정 공식 카탈로그 URL)
        log(f"  Vosk 모델 압축 해제 중: {model_name}...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(root)
    except Exception as exc:
        raise RuntimeError(f"Vosk 모델 다운로드/압축 해제 실패({model_name}): {exc}") from exc
    finally:
        zip_path.unlink(missing_ok=True)

    if not model_dir.exists():
        raise RuntimeError(f"Vosk 모델 압축 해제 후 디렉터리를 찾을 수 없습니다: {model_dir}")
    return model_dir


def _silence_vosk_logging() -> None:
    """Vosk/Kaldi 가 stderr 에 쏟아내는 로그를 끈다(faster-whisper 는 조용하므로 맞춘다)."""
    global _vosk_logging_silenced
    if _vosk_logging_silenced:
        return
    try:
        from vosk import SetLogLevel

        SetLogLevel(-1)
    except Exception:
        pass
    _vosk_logging_silenced = True


def _get_vosk_model(model_name: str, *, log: Callable[[str], None]):
    if model_name not in _model_cache:
        from vosk import Model

        model_dir = _ensure_model(model_name, log=log)
        _model_cache[model_name] = Model(str(model_dir))
    return _model_cache[model_name]


def _convert_to_wav(audio_path: Path, out_path: Path) -> None:
    """오디오를 Vosk 가 요구하는 16kHz mono 16-bit PCM WAV 로 변환한다."""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-f", "wav", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 변환 실패: {result.stderr[-500:]}")


def _segment_from_result(result_json: str, *, fallback_start: float, fallback_end: float) -> Segment | None:
    """Vosk Result()/FinalResult() JSON 한 덩어리를 Segment 로 변환한다.

    ``result``(단어별 start/end 목록)가 있으면 그 첫/끝 단어 시각을 쓰고, 없으면(드묾)
    직전 세그먼트 끝~현재 처리 시각으로 근사한다.
    """
    data = json.loads(result_json)
    text = (data.get("text") or "").strip()
    if not text:
        return None
    words = data.get("result")
    start = words[0]["start"] if words else fallback_start
    end = words[-1]["end"] if words else fallback_end
    return Segment(start=start, end=end, text=text)


def _decode(
    wav_path: Path,
    model,
    on_progress: ProgressCB | None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Segment]:
    """청크(``_DECODE_CHUNK_FRAMES``) 단위로 스트리밍 디코딩한다.

    ``should_stop`` 은 청크마다 확인해, True 면 오디오 전체를 다 읽을 때까지 기다리지
    않고 그 자리에서 :class:`StoppedError` 를 던진다(faster-whisper 쪽과 동일한 협조적
    취소 granularity).
    """
    from vosk import KaldiRecognizer

    wf = wave.open(str(wav_path), "rb")
    try:
        rate = wf.getframerate()
        total_frames = wf.getnframes()
        duration = total_frames / rate if rate else 0.0
        frame_bytes = wf.getsampwidth() * wf.getnchannels()

        rec = KaldiRecognizer(model, rate)
        rec.SetWords(True)

        segments: list[Segment] = []
        last_end = 0.0
        processed_frames = 0
        while True:
            if should_stop is not None and should_stop():
                raise StoppedError("STT 중단 요청됨")
            data = wf.readframes(_DECODE_CHUNK_FRAMES)
            if not data:
                break
            processed_frames += len(data) // frame_bytes if frame_bytes else 0
            now = min(processed_frames / rate, duration) if rate else 0.0
            if rec.AcceptWaveform(data):
                seg = _segment_from_result(rec.Result(), fallback_start=last_end, fallback_end=now)
                if seg is not None:
                    segments.append(seg)
                    last_end = seg.end
            if on_progress is not None and duration:
                on_progress(now, duration)

        seg = _segment_from_result(rec.FinalResult(), fallback_start=last_end, fallback_end=duration)
        if seg is not None:
            segments.append(seg)
        if on_progress is not None and duration:
            on_progress(duration, duration)
        return segments
    finally:
        wf.close()


def transcribe(
    audio_path: Path,
    language: str,
    cfg,
    *,
    log: Callable[[str], None] = print,
    on_progress: ProgressCB | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Segment]:
    """오디오를 Vosk 로 트랜스크립트 변환한다. faster-whisper stage3_stt.transcribe() 와
    동일한 시그니처·반환 타입(list[Segment])을 지켜 stage3_stt 가 투명하게 위임할 수 있다.
    """
    size = getattr(cfg, "vosk_model_size", "small")
    model_name = _resolve_model_name(language, size, log=log)
    log(f"  Vosk STT 실행 ({model_name})...")

    _silence_vosk_logging()
    model = _get_vosk_model(model_name, log=log)

    tmp_dir = Path(tempfile.mkdtemp(prefix="yke-vosk-"))
    try:
        wav_path = tmp_dir / "audio16k.wav"
        _convert_to_wav(audio_path, wav_path)
        return _decode(wav_path, model, on_progress, should_stop)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
