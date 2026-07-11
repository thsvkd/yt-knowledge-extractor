"""3단계: 로컬 STT 베이스라인 (faster-whisper).

`device: auto`가 GPU를 선택해도, 이 환경에 CUDA 런타임(cuBLAS/cuDNN) DLL이 없으면
추론(encode) 단계에서 실패한다. 로드 시점뿐 아니라 추론 실패까지 잡아 CPU(int8)로
자동 폴백한다. 화자 분리(diarization)는 이번 PoC 범위 밖 — 필요 시 이 단계 뒤에
WhisperX 파이프라인을 끼우면 된다.
"""

from __future__ import annotations

import glob
import os
import sys
import sysconfig
from pathlib import Path

from ..models import Segment

_model_cache: dict[tuple, object] = {}
_cuda_dlls_registered = False

# model="auto" 일 때 장치별 기본 모델.
#   cuda: large-v3. 최고 품질(고유명사·전문용어 정확). 큰 모델이라 VRAM 안전 배치(아래)로 처리.
#   cpu:  small. large-v3 는 CPU 에서 실시간(RTF~1.0)이라 실용 불가하므로 균형점 small.
_AUTO_MODEL = {"cuda": "large-v3", "cpu": "small"}

# 큰 모델은 VRAM 을 많이 써서 batch_size 가 크면 8GB급 GPU 에서 스래싱한다(large-v3 는
# batch16 이면 오히려 ~9배 느려짐 — 실측). 안전한 상한으로 낮춰 auto 기본(large-v3)이
# 스래싱 없이 돌게 한다. VRAM 이 넉넉한 GPU 라면 이 상한을 올려 처리량을 더 얻을 수 있다.
_MAX_BATCH_BY_MODEL = {"large-v3": 4, "large-v2": 4, "large": 4}


def _resolve_model(model: str, device: str) -> str:
    """model="auto" 를 장치별 기본 모델로 확정한다. 명시 모델명은 그대로 존중한다."""
    if model == "auto":
        return _AUTO_MODEL.get(device, "small")
    return model


def _effective_batch_size(model: str, batch_size: int) -> int:
    """모델별 VRAM 안전 상한을 적용한 실효 batch_size. 상한이 없는 모델은 그대로 둔다."""
    cap = _MAX_BATCH_BY_MODEL.get(model)
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


def _get_model(model: str, device: str, compute_type: str):
    if device != "cpu":
        _register_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    key = (model, device, compute_type)
    if key not in _model_cache:
        _model_cache[key] = WhisperModel(model, device=device, compute_type=compute_type)
    return _model_cache[key]


def _run(
    model, audio_path: Path, language: str, cfg, *, batched: bool = False, batch_size: int = 16
) -> list[Segment]:
    """실제 추론. 세그먼트 제너레이터를 소비하며, 여기서 CUDA 오류가 표면화된다.

    batched 면 faster-whisper 의 BatchedInferencePipeline 로 감싸 GPU 처리량을 크게
    올린다(large-v3 순차 대비 실측 ~2배). 배치는 VAD 청크를 묶어 처리하므로 세그먼트가
    거칠어질 수 있으나(타임스탬프 정밀도↓), 텍스트 내용은 보존된다.
    """
    engine = model
    kwargs = dict(language=language, vad_filter=True, word_timestamps=cfg.word_timestamps)
    if batched:
        from faster_whisper import BatchedInferencePipeline

        engine = BatchedInferencePipeline(model)
        kwargs["batch_size"] = batch_size
    segments_iter, _info = engine.transcribe(str(audio_path), **kwargs)
    return [
        Segment(start=s.start, end=s.end, text=s.text.strip())
        for s in segments_iter
        if s.text.strip()
    ]


def transcribe(audio_path: Path, language: str, cfg, *, log=print) -> list[Segment]:
    """오디오를 트랜스크립트로 변환한다.

    model="auto" 는 장치별 기본 모델로 확정하고(GPU:large-v3 / CPU:small), GPU 에서는
    배치 추론으로 가속한다. GPU 추론이 실패하면(cuBLAS 미설치 등) 조용히 넘어가지 않고
    ``log`` 로 분명히 알린 뒤 CPU 로 폴백한다(그래야 사용자가 '왜 느린지'를 안다).
    """
    device, compute_type = _resolve(cfg.device, cfg.compute_type)
    model_name = _resolve_model(cfg.model, device)
    batched = getattr(cfg, "batched", False) and device == "cuda"
    # 모델별 VRAM 안전 상한 적용(large-v3 는 8GB 에서 batch16 이 스래싱 → 4 로 낮춤).
    batch_size = _effective_batch_size(model_name, getattr(cfg, "batch_size", 16))
    if cfg.model == "auto":
        log(
            f"  STT 모델 자동 선택: {model_name} (device={device}, batched={batched}, "
            f"batch_size={batch_size})"
        )

    # 1차: 확정된 device/compute_type
    try:
        model = _get_model(model_name, device, compute_type)
        return _run(model, audio_path, language, cfg, batched=batched, batch_size=batch_size)
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
        _model_cache.pop((model_name, device, compute_type), None)

    # 2차: CPU int8 폴백. model="auto" 면 CPU 기본(small)으로 다시 확정해
    # large-v3 를 CPU 로 돌리는 실사용 불가 상황을 피한다(명시 모델명은 그대로 존중).
    cpu_model = _resolve_model(cfg.model, "cpu")
    if cpu_model != model_name:
        log(f"  CPU 폴백 모델: {cpu_model}")
    model = _get_model(cpu_model, "cpu", "int8")
    return _run(model, audio_path, language, cfg, batched=False, batch_size=batch_size)
