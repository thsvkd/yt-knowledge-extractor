"""3단계 STT 대체 엔진: sherpa-onnx (경량 오프라인).

`stt.engine: sherpa` 일 때 stage3_stt.transcribe() 가 여기로 위임한다. sherpa-onnx 는
Next-gen Kaldi(k2-fsa)의 onnxruntime 추론 엔진으로, faster-whisper(대형 Transformer)와
달리 수십 MB 짜리 모델로 완전 오프라인 동작한다. GPU 없이 CPU 만으로 실시간의 수십 배
속도가 나오므로(아래 실측), 속도·경량·완전 오프라인이 중요한 경우의 대안이다.

한국어 기본 모델은 Moonshine tiny(49MB, 2026-02 판)다. 실측(macOS arm64, num_threads=4,
zipformer-korean 배포본에 포함된 한국어 테스트 음성 4개):

    모델                              크기     RTF     결과
    moonshine-tiny-ko(기본)           49MB    0.018   정답 전사와 거의 일치, 띄어쓰기 정상
    zipformer-korean(비교용)         330MB    0.012   띄어쓰기 없이 출력('그는괜찮은척하려고…')

크기가 6배인 zipformer 쪽이 오히려 띄어쓰기가 없어 다운스트림(LLM 추출)에 불리하므로
한국어는 Moonshine 만 카탈로그에 둔다. 그래도 대형 AI 모델(faster-whisper large-v3)보다는
정확도가 낮으므로, 기본 엔진은 여전히 faster-whisper 다.

이전에 이 자리에 있던 Vosk 는 macOS 휠 배포가 0.3.44(2022-09)에서 끊기고 PyPI 릴리스도
0.3.45(2022-12)에서 멈춰 `uv sync --extra vosk` 가 macOS 에서 실패했다. sherpa-onnx 는
같은 Kaldi 계보의 후속으로 Windows·macOS(Intel/Apple Silicon)·Linux 휠을 모두 제공한다.

설치: `uv sync --extra sherpa` (기본 설치에는 포함하지 않는다 — GPU extra 와 같은 이유로,
대부분의 사용자가 쓰지 않는 대안 엔진에 네이티브 의존성을 강제하지 않기 위함).

sherpa-onnx 는 16kHz mono 샘플을 받는다. 이 파이프라인은 오디오를 원본 컨테이너
(.m4a/.webm 등)로만 갖고 있으므로(faster-whisper 는 PyAV 로 직접 디코딩해 변환이
불필요했다) ffmpeg(imageio-ffmpeg 번들 바이너리)로 임시 WAV 로 변환한다.

Moonshine 모델은 발화 전체를 한 번에 인식하는 비스트리밍 모델이라 타임스탬프를 스스로
내지 않는다. 그래서 Silero VAD(0.6MB)로 발화 구간을 먼저 나누고 구간마다 인식해,
faster-whisper 와 같은 ``Segment(start, end, text)`` 목록을 만든다. 1시간짜리 오디오도
샘플을 통째로 메모리에 올리지 않도록 WAV 를 청크 단위로 읽어 VAD 에 흘려보낸다.

모델은 pip 패키지에 포함되지 않아 최초 사용 시 sherpa-onnx 릴리스에서 내려받아
``~/.cache/yke/sherpa-models/`` 에 캐싱한다(faster-whisper 가 HuggingFace 캐시를 쓰는 것과
같은 패턴).
"""

from __future__ import annotations

import array
import shutil
import subprocess
import tarfile
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path

from ..models import Segment
from ..utils import StoppedError

ProgressCB = Callable[[float, float], None]

# 언어(ISO 639-1) -> {"small": 모델명, "large": 모델명(있으면)}.
# k2-fsa/sherpa-onnx 릴리스(asr-models)에서 확인된 Moonshine 계열만 등록한다(2026-07 기준).
# 모두 같은 파일 구성(encoder_model.ort + decoder_model_merged.ort + tokens.txt)이라
# 한 경로로 처리된다. 여기 없는 언어는 faster-whisper 를 쓰도록 안내한다(99개 언어 지원).
_MODEL_CATALOG: dict[str, dict[str, str]] = {
    # 한국어는 tiny 만 둔다 — 위 docstring 의 비교 참고(더 큰 zipformer 는 띄어쓰기 없음).
    "ko": {"small": "sherpa-onnx-moonshine-tiny-ko-quantized-2026-02-27"},
    "en": {
        "small": "sherpa-onnx-moonshine-tiny-en-quantized-2026-02-27",
        "large": "sherpa-onnx-moonshine-base-en-quantized-2026-02-27",
    },
    "ja": {
        "small": "sherpa-onnx-moonshine-tiny-ja-quantized-2026-02-27",
        "large": "sherpa-onnx-moonshine-base-ja-quantized-2026-02-27",
    },
    "zh": {"small": "sherpa-onnx-moonshine-base-zh-quantized-2026-02-27"},
    "es": {"small": "sherpa-onnx-moonshine-base-es-quantized-2026-02-27"},
    "vi": {"small": "sherpa-onnx-moonshine-base-vi-quantized-2026-02-27"},
    "uk": {"small": "sherpa-onnx-moonshine-base-uk-quantized-2026-02-27"},
    "ar": {"small": "sherpa-onnx-moonshine-base-ar-quantized-2026-02-27"},
}

_RELEASE_BASE_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
_VAD_MODEL_FILE = "silero_vad.onnx"  # 0.6MB

# Moonshine 계열 아카이브의 파일 구성(모두 동일).
_ENCODER_FILE = "encoder_model.ort"
_DECODER_FILE = "decoder_model_merged.ort"
_TOKENS_FILE = "tokens.txt"

_SAMPLE_RATE = 16000
# WAV 를 이 크기(초)만큼씩 읽어 VAD 에 흘린다. 1시간 오디오를 통째로 파이썬 실수 리스트로
# 올리면 수 GB 가 되므로 스트리밍으로 처리하고, 진행률·중단 확인도 이 주기로 한다.
_READ_CHUNK_SECONDS = 2.0
# 한 발화 구간의 최대 길이(초). 넘으면 VAD 가 끊어 준다 — 인용 타임스탬프가 너무 뭉뚱그려
# 지지 않게 하고, 비스트리밍 인식의 한 번 입력 길이도 제한한다.
_MAX_SPEECH_SECONDS = 20.0

_recognizer_cache: dict[tuple, object] = {}
_physical_cpu_threads_cache: int | None = None


def _cache_root() -> Path:
    return Path.home() / ".cache" / "yke" / "sherpa-models"


def _resolve_model_name(language: str, size: str, *, log: Callable[[str], None]) -> str:
    """언어·크기로 sherpa-onnx 모델명을 확정한다. 미지원 언어는 명확한 예외로 알린다."""
    lang_key = language.split("-")[0].lower()
    catalog = _MODEL_CATALOG.get(lang_key)
    if not catalog:
        supported = ", ".join(sorted(_MODEL_CATALOG))
        raise ValueError(
            f"sherpa-onnx 경량 엔진은 언어 '{language}' 를 지원하지 않습니다. "
            f"지원 언어: {supported} "
            "(engine: faster-whisper 를 쓰거나 config 의 language 를 바꾸세요)."
        )
    if size in catalog:
        return catalog[size]
    log(f"  sherpa-onnx: 언어 '{language}' 에는 '{size}' 모델이 없어 small 로 대체합니다.")
    return catalog["small"]


def _detect_physical_cpu_threads() -> int:
    """물리 코어 수를 감지한다(감지 실패 시 1). 프로세스 생애주기 동안 한 번만 조회한다.

    sherpa-onnx 의 num_threads 기본값은 1 이라, 명시하지 않으면 코어를 놀린다.
    """
    global _physical_cpu_threads_cache
    if _physical_cpu_threads_cache is not None:
        return _physical_cpu_threads_cache
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
    except Exception:
        n = None
    _physical_cpu_threads_cache = n or 1
    return _physical_cpu_threads_cache


def _download(url: str, dest: Path, *, log: Callable[[str], None]) -> None:
    import urllib.request

    log(f"  sherpa-onnx 모델 다운로드 중: {url} — 최초 1회만 필요합니다...")
    urllib.request.urlretrieve(url, dest)  # noqa: S310 (고정 공식 릴리스 URL)


def _extract(archive: Path, root: Path) -> None:
    """모델 아카이브(.tar.bz2)를 캐시 루트에 푼다(경로 탈출 방지 필터 적용)."""
    with tarfile.open(archive, "r:bz2") as tf:
        try:
            tf.extractall(root, filter="data")
        except TypeError:  # filter 인자가 없는 옛 파이썬(3.11 초기 패치)
            tf.extractall(root)  # noqa: S202


def _ensure_model(model_name: str, *, log: Callable[[str], None]) -> Path:
    """모델이 로컬 캐시에 없으면 내려받아 압축을 푼다. 이미 있으면 그대로 재사용한다."""
    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)
    model_dir = root / model_name
    if model_dir.exists():
        return model_dir

    archive = root / f"{model_name}.tar.bz2"
    try:
        _download(f"{_RELEASE_BASE_URL}{model_name}.tar.bz2", archive, log=log)
        log(f"  sherpa-onnx 모델 압축 해제 중: {model_name}...")
        _extract(archive, root)
    except Exception as exc:
        raise RuntimeError(f"sherpa-onnx 모델 다운로드/압축 해제 실패({model_name}): {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)

    if not model_dir.exists():
        raise RuntimeError(f"sherpa-onnx 모델 압축 해제 후 디렉터리를 찾을 수 없습니다: {model_dir}")
    return model_dir


def _ensure_vad_model(*, log: Callable[[str], None]) -> Path:
    """Silero VAD 모델(0.6MB)을 확보한다 — 발화 구간을 나눠 세그먼트 시각을 얻는 데 쓴다."""
    root = _cache_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / _VAD_MODEL_FILE
    if path.exists():
        return path
    tmp = path.with_suffix(".part")
    try:
        _download(f"{_RELEASE_BASE_URL}{_VAD_MODEL_FILE}", tmp, log=log)
        tmp.replace(path)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Silero VAD 모델 다운로드 실패: {exc}") from exc
    return path


def _model_files(model_dir: Path) -> tuple[str, str, str]:
    """Moonshine 모델 디렉터리에서 (encoder, decoder, tokens) 경로를 확정한다."""
    files = [model_dir / _ENCODER_FILE, model_dir / _DECODER_FILE, model_dir / _TOKENS_FILE]
    missing = [f.name for f in files if not f.exists()]
    if missing:
        raise RuntimeError(
            f"sherpa-onnx 모델 구성이 예상과 다릅니다({model_dir.name}): {', '.join(missing)} 없음. "
            f"캐시를 지우고 다시 시도하세요: {model_dir}"
        )
    return tuple(str(f) for f in files)  # type: ignore[return-value]


def _import_sherpa():
    """sherpa_onnx 를 임포트한다. 미설치면 설치 방법을 알려 주는 예외로 바꾼다.

    이 엔진은 optional extra 라, GUI 에서 '경량 모델'을 골랐는데 설치가 안 돼 있을 수
    있다. 그때 원본 ImportError(네이티브 라이브러리 경로 나열)를 그대로 보여주는 대신
    무엇을 해야 하는지 알려 준다.
    """
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise RuntimeError(
            "경량 STT 엔진(sherpa-onnx)이 설치되어 있지 않습니다. "
            "`uv sync --extra sherpa` 로 설치하거나, STT 엔진을 'AI 모델(faster-whisper)'로 "
            f"바꾸세요. (원인: {exc})"
        ) from exc
    return sherpa_onnx


def _get_recognizer(model_name: str, num_threads: int, *, log: Callable[[str], None]):
    """모델을 로드해(캐시) 비스트리밍 인식기를 만든다."""
    key = (model_name, num_threads)
    if key not in _recognizer_cache:
        sherpa_onnx = _import_sherpa()

        encoder, decoder, tokens = _model_files(_ensure_model(model_name, log=log))
        _recognizer_cache[key] = sherpa_onnx.OfflineRecognizer.from_moonshine_v2(
            encoder=encoder, decoder=decoder, tokens=tokens, num_threads=num_threads
        )
    return _recognizer_cache[key]


def _make_vad(num_threads: int, *, log: Callable[[str], None]):
    """발화 구간 검출기를 만든다.

    VAD 는 내부 버퍼를 갖는 상태 객체라 인식기와 달리 캐시하지 않고 영상마다 새로 만든다
    (앞 영상의 잔여 버퍼가 다음 영상으로 새는 것을 막는다).
    """
    sherpa_onnx = _import_sherpa()

    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = str(_ensure_vad_model(log=log))
    config.silero_vad.max_speech_duration = _MAX_SPEECH_SECONDS
    config.sample_rate = _SAMPLE_RATE
    config.num_threads = num_threads
    return sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=_MAX_SPEECH_SECONDS * 2)


def _convert_to_wav(audio_path: Path, out_path: Path) -> None:
    """오디오를 sherpa-onnx 가 요구하는 16kHz mono 16-bit PCM WAV 로 변환한다."""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y", "-i", str(audio_path),
        "-ac", "1", "-ar", str(_SAMPLE_RATE), "-f", "wav", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 오디오 변환 실패: {result.stderr[-500:]}")


def _pcm16_to_float(raw: bytes) -> list[float]:
    """16-bit PCM 바이트를 sherpa-onnx 가 받는 [-1, 1] 실수 목록으로 바꾼다."""
    pcm = array.array("h")
    pcm.frombytes(raw)
    return [s / 32768.0 for s in pcm]


def _clean_text(text: str) -> str:
    """인식 결과 텍스트를 정리한다(SentencePiece 단어 경계 기호 제거, 공백 정돈)."""
    return " ".join(text.replace("▁", " ").split())


def _drain(vad, recognizer, segments: list[Segment], *, flush: bool = False) -> None:
    """VAD 가 확정한 발화 구간을 인식해 세그먼트로 쌓는다.

    ``flush`` 면 오디오가 끝났다는 뜻이라, 아직 말이 이어지는 중으로 보고 붙들고 있던
    마지막 구간까지 밀어낸다.
    """
    if flush:
        vad.flush()
    while not vad.empty():
        speech = vad.front
        stream = recognizer.create_stream()
        stream.accept_waveform(_SAMPLE_RATE, speech.samples)
        recognizer.decode_stream(stream)
        text = _clean_text(stream.result.text)
        if text:
            start = speech.start / _SAMPLE_RATE
            segments.append(
                Segment(start=start, end=start + len(speech.samples) / _SAMPLE_RATE, text=text)
            )
        vad.pop()


def _decode(
    wav_path: Path,
    recognizer,
    vad,
    on_progress: ProgressCB | None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Segment]:
    """WAV 를 청크 단위로 읽어 VAD 로 나누고, 발화 구간마다 인식한다.

    ``should_stop`` 은 청크마다 확인해, True 면 오디오 전체를 다 읽을 때까지 기다리지
    않고 그 자리에서 :class:`StoppedError` 를 던진다(faster-whisper 쪽과 동일한 협조적
    취소 granularity).
    """
    window = vad.config.silero_vad.window_size
    chunk_frames = int(_SAMPLE_RATE * _READ_CHUNK_SECONDS)

    wf = wave.open(str(wav_path), "rb")
    try:
        rate = wf.getframerate()
        duration = wf.getnframes() / rate if rate else 0.0
        segments: list[Segment] = []
        pending: list[float] = []  # window 크기로 못 채운 나머지 샘플
        processed = 0
        while True:
            if should_stop is not None and should_stop():
                raise StoppedError("STT 중단 요청됨")
            raw = wf.readframes(chunk_frames)
            if not raw:
                break
            pending.extend(_pcm16_to_float(raw))
            # VAD 는 정해진 window 크기 단위로 먹인다(남는 꼬리는 다음 청크와 합쳐 처리).
            offset = 0
            while offset + window <= len(pending):
                vad.accept_waveform(pending[offset : offset + window])
                offset += window
            del pending[:offset]
            processed += len(raw) // (wf.getsampwidth() * wf.getnchannels())
            _drain(vad, recognizer, segments)
            if on_progress is not None and duration:
                on_progress(min(processed / rate, duration), duration)

        if pending:  # 마지막 자투리는 0 으로 채워 한 window 로 만들어 넣는다
            vad.accept_waveform(pending + [0.0] * (window - len(pending)))
        _drain(vad, recognizer, segments, flush=True)
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
    """오디오를 sherpa-onnx 로 트랜스크립트 변환한다.

    faster-whisper stage3_stt.transcribe() 와 동일한 시그니처·반환 타입(list[Segment])을
    지켜 stage3_stt 가 투명하게 위임할 수 있다.
    """
    size = getattr(cfg, "sherpa_model_size", "small")
    model_name = _resolve_model_name(language, size, log=log)
    requested_threads = getattr(cfg, "cpu_threads", 0)
    num_threads = requested_threads if requested_threads > 0 else _detect_physical_cpu_threads()
    log(f"  sherpa-onnx STT 실행 ({model_name}, num_threads={num_threads})...")

    recognizer = _get_recognizer(model_name, num_threads, log=log)
    vad = _make_vad(num_threads, log=log)

    tmp_dir = Path(tempfile.mkdtemp(prefix="yke-sherpa-"))
    try:
        wav_path = tmp_dir / "audio16k.wav"
        _convert_to_wav(audio_path, wav_path)
        return _decode(wav_path, recognizer, vad, on_progress, should_stop)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
