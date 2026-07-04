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
    _cuda_dlls_registered = True
    return added


def _get_model(model: str, device: str, compute_type: str):
    if device != "cpu":
        _register_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    key = (model, device, compute_type)
    if key not in _model_cache:
        _model_cache[key] = WhisperModel(model, device=device, compute_type=compute_type)
    return _model_cache[key]


def _run(model, audio_path: Path, language: str, cfg) -> list[Segment]:
    """실제 추론. 세그먼트 제너레이터를 소비하며, 여기서 CUDA 오류가 표면화된다."""
    segments_iter, _info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        word_timestamps=cfg.word_timestamps,
    )
    return [
        Segment(start=s.start, end=s.end, text=s.text.strip())
        for s in segments_iter
        if s.text.strip()
    ]


def transcribe(audio_path: Path, language: str, cfg) -> list[Segment]:
    # 1차: 설정된 device/compute_type
    try:
        model = _get_model(cfg.model, cfg.device, cfg.compute_type)
        return _run(model, audio_path, language, cfg)
    except Exception as exc:
        if cfg.device == "cpu":
            raise
        print(
            f"  (STT: '{cfg.device}/{cfg.compute_type}' 실패 -> cpu/int8 재시도: "
            f"{type(exc).__name__}: {exc})"
        )
        _model_cache.pop((cfg.model, cfg.device, cfg.compute_type), None)

    # 2차: CPU int8 폴백
    model = _get_model(cfg.model, "cpu", "int8")
    return _run(model, audio_path, language, cfg)
