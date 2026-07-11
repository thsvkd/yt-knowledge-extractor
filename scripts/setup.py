#!/usr/bin/env python3
"""개발 환경 구성 스크립트. 의존성을 설치한다.

사용:
    python scripts/setup.py            # CPU 환경 구성 (기본)
    python scripts/setup.py --gpu      # GPU(NVIDIA CUDA) STT 가속 런타임까지 설치

하는 일:
    1. uv 설치 여부 확인(없으면 안내 후 중단).
    2. 의존성 동기화 - `uv sync` (--gpu 면 `uv sync --extra gpu` 로 cuBLAS 포함).
    3. 패키지 임포트 확인으로 구성이 실제로 됐는지 검증.

CPU / GPU 차이:
    STT(faster-whisper→CTranslate2)의 CUDA 가속에는 cuBLAS 런타임(nvidia-cublas-cu12)이
    필요하다. --gpu 를 주면 이 optional extra 를 설치하고, 안 주면 CPU 전용으로 더 가볍게
    구성한다. GPU 가 없거나 설치하지 않아도 앱이 자동으로 CPU(int8)로 폴백하므로, 확실하지
    않으면 CPU(기본)로 구성하면 된다.

다음 단계:
    - `전체(위키)` 실행에는 Claude Code CLI 설치 + 로그인이 필요하다(전사 단계까진 불필요).
      https://claude.com/claude-code 참고, 설치 후 `claude login`.
    - `python scripts/run.py` 로 앱을 실행한다.
"""

from __future__ import annotations

import argparse

from _common import check, info, require_uv


def sync_dependencies(gpu: bool) -> None:
    """uv 로 의존성을 동기화한다. gpu 면 CUDA STT 가속 extra(cuBLAS)까지 포함한다."""
    command = ["uv", "sync"]
    if gpu:
        command += ["--extra", "gpu"]
    info(f"의존성 동기화 ({' '.join(command)})")
    check(command)


def verify_import() -> None:
    """방금 구성한 환경에서 패키지가 실제로 임포트되는지 확인한다.

    --no-sync: 바로 위에서 동기화했으므로 재동기화 없이 그대로 검증한다.
    """
    info("구성 확인 (yke 임포트)")
    check(["uv", "run", "--no-sync", "python", "-c", "import yke"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="NVIDIA CUDA STT 가속 런타임(nvidia-cublas-cu12)까지 설치한다(기본은 CPU 전용).",
    )
    args = parser.parse_args()

    require_uv()
    sync_dependencies(args.gpu)
    verify_import()

    info("환경 구성 완료. `python scripts/run.py` 로 앱을 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
