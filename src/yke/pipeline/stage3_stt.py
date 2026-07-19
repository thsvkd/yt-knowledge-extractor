"""3단계: 로컬 STT 베이스라인 (faster-whisper).

`device: auto`가 GPU를 선택해도, 이 환경에 CUDA 런타임(cuBLAS/cuDNN) DLL이 없으면
추론(encode) 단계에서 실패한다. 로드 시점뿐 아니라 추론 실패까지 잡아 CPU(int8)로
자동 폴백한다. 화자 분리(diarization)는 이번 PoC 범위 밖 — 필요 시 이 단계 뒤에
WhisperX 파이프라인을 끼우면 된다.

배치 추론(BatchedInferencePipeline)은 GPU 전용이 아니다 — CPU(small, int8)에서도
실측 RTF 0.109 → 0.041(~2.6배, 기본 batch_size=4 기준)로 유의미하게 빠르다(VAD 청크를
배치로 묶어 CPU 코어를 더 잘 채우기 때문). cpu_threads 를 물리 코어 수로 명시하면
추가로 더 빨라지지만(실측 0.041 → 0.037), 하이퍼스레딩 논리 코어 수(os.cpu_count())로
그대로 올리면 스레드 경합으로 오히려 느려진다(실측 확인). 물리 코어 수를 이식성 있게
감지할 표준 방법이 없어 여기서는 cpu_threads 를 건드리지 않고 faster-whisper 기본값에
맡긴다 — 배치만으로 이미 대부분의 이득을 얻는다.

CPU 의 batch_size 는 (처리량이 더 좋은 16 이 아니라) 4 로 상한을 둔다 — 배치가 통째로
끝나야 그 안의 세그먼트들이 한꺼번에 yield 되므로, batch_size=16 이면 중간 길이 영상도
배치 1개로 처리돼 STT 끝까지 진행률이 0%로 멈춰 있다가 끝에 확 차버린다(진행바 세분화
요구사항과 충돌). batch_size 4 는 16 대비 RTF 손해가 ~12%뿐이라(0.033 → 0.037) 진행바
반응성과 속도를 함께 챙긴다(_CPU_BATCH_SIZE_CAP 참고).

cpu_threads 를 물리 코어 수로 명시하면 위 배치 최적화 위에 추가로 더 빨라진다(실측
0.041 → 0.037). 하이퍼스레딩 논리 코어 수(os.cpu_count())를 그대로 올리면 스레드 경합
으로 오히려 느려진다(실측 확인). 물리 코어 수는 psutil 로 감지한다(cfg.cpu_threads=0
일 때 자동 적용, _effective_cpu_threads 참고).

이 모듈은 faster-whisper(AI) 전용이다. cfg.engine="vosk" 면 transcribe() 가 경량
오프라인 엔진(stage3_stt_vosk, non-AI 옵션)으로 위임한다.
"""

from __future__ import annotations

import glob
import os
import sys
import sysconfig
from collections.abc import Callable
from pathlib import Path

from ..models import Segment
from ..utils import StoppedError

# 세그먼트가 도착할 때마다 (완료된 오디오 초, 전체 오디오 초)를 알리는 콜백.
# 배치 모드에서도 faster-whisper 는 배치 단위로 세그먼트를 점진적으로 yield 하므로
# (전체를 다 처리한 뒤 한 번에 반환하지 않음) 이 콜백으로 세부 진행률을 낼 수 있다.
ProgressCB = Callable[[float, float], None]

_model_cache: dict[tuple, object] = {}
_cuda_dlls_registered = False
_physical_cpu_threads_cache: int | None = None

# model="auto" 일 때 장치별 기본 모델.
#   cuda: large-v3. 최고 품질(고유명사·전문용어 정확). 큰 모델이라 VRAM 안전 배치(아래)로 처리.
#   cpu:  small. large-v3 는 CPU 에서 실시간(RTF~1.0)이라 실용 불가하므로 균형점 small.
_AUTO_MODEL = {"cuda": "large-v3", "cpu": "small"}

# 큰 모델은 VRAM 을 많이 써서 batch_size 가 크면 8GB급 GPU 에서 스래싱한다(large-v3 는
# batch16 이면 오히려 ~9배 느려짐 — 실측). 안전한 상한으로 낮춰 auto 기본(large-v3)이
# 스래싱 없이 돌게 한다. VRAM 이 넉넉한 GPU 라면 이 상한을 올려 처리량을 더 얻을 수 있다.
_MAX_BATCH_BY_MODEL = {"large-v3": 4, "large-v2": 4, "large": 4}

# CPU 배치 추론은 배치 하나가 통째로 끝나야 그 안의 세그먼트들이 한꺼번에 yield 된다
# (faster-whisper 가 배치 단위로만 진행 상황을 내보냄). batch_size 를 그대로 16 으로 두면
# 중간 길이 영상까지도 VAD 청크가 통째로 배치 1개에 들어가버려, STT 가 끝날 때까지
# 진행률이 0%로 멈춰 있다가 끝에 한 번에 확 차버리는 문제가 실측 확인됐다(10분 오디오
# 전체가 배치 1개로 처리됨 — progress 콜백 14개가 전부 같은 순간에 발생). CPU 처리 속도
# 자체는 batch_size 4 나 16 이나 거의 같으므로(실측 RTF 0.037 vs 0.033, ~12% 차이) 작게
# 잡아 진행바가 자주(배치마다) 갱신되게 한다.
_CPU_BATCH_SIZE_CAP = 4


def _resolve_model(model: str, device: str) -> str:
    """model="auto" 를 장치별 기본 모델로 확정한다. 명시 모델명은 그대로 존중한다."""
    if model == "auto":
        return _AUTO_MODEL.get(device, "small")
    return model


def _effective_batch_size(model: str, device: str, batch_size: int) -> int:
    """모델·장치별 상한을 적용한 실효 batch_size.

    GPU 큰 모델은 VRAM 스래싱 방지 상한을, CPU 는 진행률 콜백이 자주 나오게 하는 상한을
    적용하고(둘 다 해당하면 더 낮은 쪽) 상한이 없으면 요청값 그대로 쓴다.
    """
    cap = _MAX_BATCH_BY_MODEL.get(model)
    if device == "cpu":
        cap = _CPU_BATCH_SIZE_CAP if cap is None else min(cap, _CPU_BATCH_SIZE_CAP)
    return min(batch_size, cap) if cap else batch_size


def _register_cuda_dll_dirs() -> list[str]:
    """Windows: pip 로 설치된 nvidia cuBLAS/cuDNN 휠의 DLL 디렉터리를 로더에 등록한다.

    CTranslate2 는 시스템 CUDA 툴킷이 아니라 이 DLL 들(cublas64_12.dll, cudnn64_9.dll)을
    필요로 하며, `uv sync --extra gpu` 로 venv 에 넣으면 여기서 경로를 잡아준다.
    """
    global _cuda_dlls_registered
    if _cuda_dlls_registered or not sys.platform.startswith("win"):
        return []
    added: list[str] = []
    site = sysconfig.get_paths().get("purelib")
    if site:
        for bindir in glob.glob(os.path.join(site, "nvidia", "*", "bin")):
            if os.path.isdir(bindir):
                # add_dll_directory: 파이썬이 로드하는 확장모듈의 의존성 해석용
                try:
                    os.add_dll_directory(bindir)
                except OSError:
                    pass
                # PATH: ctranslate2 가 LoadLibrary("cublas64_12.dll") 를 표준 검색순서로
                # 호출하므로, 그 검색에 걸리도록 PATH 앞에 추가한다 (핵심).
                if bindir not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
                added.append(bindir)
    if added:
        _cuda_dlls_registered = True
    return added


def _cuda_available() -> bool:
    """CTranslate2 가 볼 수 있는 CUDA 장치가 하나라도 있는지. 실패하면 없음으로 본다.

    (드라이버 API 만 조회하므로 번들 cuBLAS/cuDNN DLL 없이도 안전하게 호출된다.)
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _resolve(device: str, compute_type: str) -> tuple[str, str]:
    """device=auto 를 실제 장치로 확정하고, compute_type=auto 를 장치별 기본값으로 편다.

    - device  auto → CUDA 가용 시 'cuda', 아니면 'cpu'.
    - compute auto → GPU 는 'float16'(정밀도↑), CPU 는 'int8'(가볍고 빠름).
      명시값(int8/float16/…)은 그대로 존중한다. auto 를 여기서 미리 확정해야
      'CPU 인데 float16' 같은 조합(CTranslate2 가 float32 로 승격 → 오히려 느림)을 피한다.
    """
    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def _detect_physical_cpu_threads() -> int:
    """물리 코어 수를 감지한다. 실패/미설치 시 0(= ctranslate2 기본값에 위임)을 반환한다.

    프로세스 생애주기 동안 한 번만 감지해 캐시한다(psutil 조회 비용 절감).
    """
    global _physical_cpu_threads_cache
    if _physical_cpu_threads_cache is not None:
        return _physical_cpu_threads_cache
    try:
        import psutil

        n = psutil.cpu_count(logical=False)
    except Exception:
        n = None
    _physical_cpu_threads_cache = n or 0
    return _physical_cpu_threads_cache


def _effective_cpu_threads(device: str, requested: int) -> int:
    """cfg.cpu_threads 를 실제 WhisperModel(cpu_threads=...) 인자로 확정한다.

    GPU 에서는 무의미하므로 0. CPU 에서 요청값이 명시(>0)되면 그대로 존중하고,
    0(자동)이면 물리 코어 수를 감지해 적용한다(감지 실패 시 0 → ctranslate2 기본값).
    """
    if device != "cpu":
        return 0
    return requested if requested > 0 else _detect_physical_cpu_threads()


def _get_model(model: str, device: str, compute_type: str, cpu_threads: int = 0):
    if device != "cpu":
        _register_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    key = (model, device, compute_type, cpu_threads)
    if key not in _model_cache:
        kwargs = {"cpu_threads": cpu_threads} if device == "cpu" and cpu_threads else {}
        _model_cache[key] = WhisperModel(model, device=device, compute_type=compute_type, **kwargs)
    return _model_cache[key]


def _run(
    model,
    audio_path: Path,
    language: str,
    cfg,
    *,
    batched: bool = False,
    batch_size: int = 16,
    on_progress: ProgressCB | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Segment]:
    """실제 추론. 세그먼트 제너레이터를 소비하며, 여기서 CUDA 오류가 표면화된다.

    batched 면 faster-whisper 의 BatchedInferencePipeline 로 감싸 처리량을 크게 올린다
    (GPU large-v3 순차 대비 ~2배, CPU small 순차 대비 실측 ~2.6배 — RTF 0.109 → 0.041,
    8코어/16스레드 CPU·5분 클립 기준). 배치는 VAD 청크를 묶어 처리하므로 세그먼트가
    거칠어질 수 있으나(타임스탬프 정밀도↓), 텍스트 내용은 보존된다.

    ``on_progress`` 는 세그먼트가 도착할 때마다(배치 모드면 배치 단위로) 호출되어
    (완료 초, 전체 초)를 알린다 — GUI 프로그레스바를 세밀하게 갱신하는 데 쓴다.

    ``should_stop`` 은 세그먼트(배치 모드면 배치)가 도착할 때마다 확인한다 — 세그먼트
    제너레이터가 지연 평가라 True 를 보면 그 이상은 추론을 이어가지 않고 바로
    :class:`StoppedError` 를 던져, 영상 하나가 끝날 때까지 기다리지 않고 중단할 수 있다.
    """
    engine = model
    kwargs = dict(language=language, vad_filter=True, word_timestamps=cfg.word_timestamps)
    if batched:
        from faster_whisper import BatchedInferencePipeline

        engine = BatchedInferencePipeline(model)
        kwargs["batch_size"] = batch_size
    segments_iter, info = engine.transcribe(str(audio_path), **kwargs)
    duration = info.duration
    result: list[Segment] = []
    for s in segments_iter:
        if should_stop is not None and should_stop():
            raise StoppedError("STT 중단 요청됨")
        if on_progress is not None and duration:
            on_progress(min(s.end, duration), duration)
        if s.text.strip():
            result.append(Segment(start=s.start, end=s.end, text=s.text.strip()))
    return result


def transcribe(
    audio_path: Path,
    language: str,
    cfg,
    *,
    log=print,
    on_progress: ProgressCB | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Segment]:
    """오디오를 트랜스크립트로 변환한다.

    cfg.engine="vosk" 면 경량 오프라인 엔진(non-AI 옵션)으로 위임한다(stage3_stt_vosk).
    기본 엔진(faster-whisper)에서 model="auto" 는 장치별 기본 모델로 확정하고
    (GPU:large-v3 / CPU:small), GPU·CPU 모두 배치 추론으로 가속한다(cfg.batched).
    GPU 추론이 실패하면(cuBLAS 미설치 등) 조용히 넘어가지 않고 ``log`` 로 분명히 알린 뒤
    CPU 로 폴백한다(그래야 사용자가 '왜 느린지'를 안다). ``on_progress`` 는 있으면 그대로
    실제 추론까지 흘려보낸다.

    ``should_stop`` 이 True 를 반환하면 세그먼트/배치 경계에서 :class:`StoppedError` 를
    던져 즉시 중단한다(영상 전체 STT 가 끝날 때까지 기다리지 않는다).
    """
    if getattr(cfg, "engine", "faster-whisper") == "vosk":
        from . import stage3_stt_vosk

        return stage3_stt_vosk.transcribe(
            audio_path, language, cfg, log=log, on_progress=on_progress, should_stop=should_stop
        )

    device, compute_type = _resolve(cfg.device, cfg.compute_type)
    model_name = _resolve_model(cfg.model, device)
    batched = getattr(cfg, "batched", False)
    requested_batch_size = getattr(cfg, "batch_size", 16)
    requested_cpu_threads = getattr(cfg, "cpu_threads", 0)
    # 모델·장치별 상한 적용(large-v3 는 8GB 에서 batch16 이 스래싱 → 4 로, CPU 는 진행률
    # 콜백이 자주 나오도록 4 로 낮춤 — _effective_batch_size 주석 참고).
    batch_size = _effective_batch_size(model_name, device, requested_batch_size)
    cpu_threads = _effective_cpu_threads(device, requested_cpu_threads)
    if cfg.model == "auto":
        log(
            f"  STT 모델 자동 선택: {model_name} (device={device}, batched={batched}, "
            f"batch_size={batch_size})"
        )

    # 1차: 확정된 device/compute_type
    try:
        model = _get_model(model_name, device, compute_type, cpu_threads)
        return _run(
            model,
            audio_path,
            language,
            cfg,
            batched=batched,
            batch_size=batch_size,
            on_progress=on_progress,
            should_stop=should_stop,
        )
    except StoppedError:
        # 중단 요청은 GPU 실패가 아니므로 아래 CPU 폴백으로 흘리지 않고 그대로 전파한다.
        raise
    except Exception as exc:
        if device == "cpu":
            raise
        # 로그 실패(예: cp949 콘솔로 리다이렉트된 CLI 에서 인코딩 불가 문자)가 이 아래의
        # CPU 폴백을 무산시키지 않도록 보호한다. 메시지도 cp949 안전 문자만 쓴다(이모지 금지).
        try:
            log(
                f"  주의: GPU STT 실패 → CPU 로 폴백합니다. 큰 모델은 CPU 에서 매우 느릴 수 있습니다 "
                f"({type(exc).__name__}: {exc})"
            )
        except Exception:
            pass
        _model_cache.pop((model_name, device, compute_type, cpu_threads), None)

    # 2차: CPU int8 폴백. model="auto" 면 CPU 기본(small)으로 다시 확정해
    # large-v3 를 CPU 로 돌리는 실사용 불가 상황을 피한다(명시 모델명은 그대로 존중).
    cpu_model = _resolve_model(cfg.model, "cpu")
    if cpu_model != model_name:
        log(f"  CPU 폴백 모델: {cpu_model}")
    # batch_size/cpu_threads 도 (GPU 모델 기준이 아니라) 실제로 돌릴 cpu_model 기준으로
    # 다시 계산한다 — 안 그러면 큰 모델의 VRAM 상한이 폴백 후에도 그대로 남는 등 엉뚱한
    # 상한이 적용된다.
    cpu_batch_size = _effective_batch_size(cpu_model, "cpu", requested_batch_size)
    cpu_threads = _effective_cpu_threads("cpu", requested_cpu_threads)
    model = _get_model(cpu_model, "cpu", "int8", cpu_threads)
    return _run(
        model,
        audio_path,
        language,
        cfg,
        batched=batched,
        batch_size=cpu_batch_size,
        on_progress=on_progress,
        should_stop=should_stop,
    )
