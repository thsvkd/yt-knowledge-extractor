"""GPU(NVIDIA CUDA) 런타임 온디맨드 다운로드.

CPU 설치본은 용량을 위해 cuBLAS 런타임을 포함하지 않는다. NVIDIA GPU 사용자가 GPU 가속을
켜면 cuBLAS DLL 을 GitHub 릴리스에서 받아 ``current\\`` **밖** 영속 경로
(``%LocalAppData%\\YtKnowledgeExtractor\\gpu-runtime\\``)에 풀어 둔다. Velopack 업데이트가
``current\\`` 를 통째로 교체해도 이 경로는 유지되므로 한 번 받으면 계속 쓴다. STT 단계
(:func:`yke.pipeline.stage3_stt._register_cuda_dll_dirs`)가 이 경로와 번들 site-packages
양쪽의 ``nvidia/*/bin`` 을 DLL 로더에 등록해 CTranslate2 가 cuBLAS 를 찾게 한다.

cuBLAS 런타임은 앱 버전과 거의 무관하므로, 앱 릴리스마다 재첨부하지 않고 전용 릴리스 태그
(``gpu-runtime-cu12``)의 에셋 zip 하나를 공유한다(``build.py`` 가 이 zip 을 만든다).
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import sysconfig
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from .velopack_update import REPO_URL  # 같은 GitHub 레포를 재사용

logger = logging.getLogger(__name__)

# packId — Velopack 설치 루트(%LocalAppData%\YtKnowledgeExtractor)와 같다.
_APP_ID = "YtKnowledgeExtractor"
# cuBLAS 런타임 zip 을 올려 둔 전용 릴리스 태그/에셋(build.py 가 생성).
GPU_RUNTIME_TAG = "gpu-runtime-cu12"
GPU_RUNTIME_ASSET = "yke-gpu-runtime-cublas-cu12.zip"
_DOWNLOAD_URL = f"{REPO_URL}/releases/download/{GPU_RUNTIME_TAG}/{GPU_RUNTIME_ASSET}"
_USER_AGENT = "yke-gpu-runtime"


def runtime_dir() -> Path:
    """온디맨드 cuBLAS 런타임을 두는 영속 경로(``current\\`` 밖, 업데이트에도 유지)."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / _APP_ID / "gpu-runtime"


def _site_nvidia_bin_dirs() -> list[Path]:
    """번들/개발 venv 의 site-packages 에 있는 nvidia 휠 bin 디렉터리(GPU 번들·`--extra gpu`)."""
    site = sysconfig.get_paths().get("purelib")
    if not site:
        return []
    return [Path(p) for p in glob.glob(os.path.join(site, "nvidia", "*", "bin")) if os.path.isdir(p)]


def nvidia_bin_dirs() -> list[Path]:
    """온디맨드로 받은 런타임의 nvidia/*/bin 디렉터리들(없으면 빈 리스트)."""
    return [p for p in runtime_dir().glob("nvidia/*/bin") if p.is_dir()]


def all_nvidia_bin_dirs() -> list[Path]:
    """cuBLAS DLL 을 담을 수 있는 모든 경로(site-packages + 온디맨드 런타임)."""
    return _site_nvidia_bin_dirs() + nvidia_bin_dirs()


def is_installed() -> bool:
    """온디맨드 cuBLAS 런타임이 이미 받아져 있는지."""
    return any(runtime_dir().glob("nvidia/*/bin/cublas*.dll"))


def cublas_present() -> bool:
    """cuBLAS DLL 이 (번들 site-packages 든 온디맨드 런타임이든) 로드 가능한 위치에 있는지."""
    return any(list(d.glob("cublas*.dll")) for d in all_nvidia_bin_dirs())


def cuda_available() -> bool:
    """CTranslate2 가 볼 수 있는 CUDA 장치(=NVIDIA GPU + 드라이버)가 있는지.

    드라이버 API 만 조회하므로 cuBLAS DLL 없이도 안전하다(실패 시 없음으로 본다).
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def download(progress_cb: Callable[[float], None] | None = None, *, timeout: int = 120) -> None:
    """cuBLAS 런타임 zip 을 받아 :func:`runtime_dir` 에 푼다. ``progress_cb`` 는 0.0~1.0.

    반쯤 풀린 상태를 남기지 않도록 임시 폴더에 받아 추출한 뒤 원자적으로 교체한다.
    zip 은 최상위에 ``nvidia/`` 트리를 담는다(build.py 규칙).
    """
    if not _DOWNLOAD_URL.lower().startswith("https://"):
        raise RuntimeError(f"안전하지 않은 다운로드 URL(https 아님): {_DOWNLOAD_URL}")
    dest = runtime_dir()
    tmp_root = Path(tempfile.mkdtemp(prefix="yke-gpu-"))
    zip_path = tmp_root / GPU_RUNTIME_ASSET
    try:
        req = urllib.request.Request(_DOWNLOAD_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(zip_path, "wb") as f:  # noqa: S310
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(done / total)
        extract_tmp = tmp_root / "extract"
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_tmp)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(extract_tmp), str(dest))
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
