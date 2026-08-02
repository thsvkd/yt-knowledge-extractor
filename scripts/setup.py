#!/usr/bin/env python3
"""개발 환경 구성 스크립트. 의존성을 설치한다.

사용:
    python scripts/setup.py            # base + sherpa 구성 (기본)
    python scripts/setup.py --gpu      # base + sherpa + GPU(NVIDIA CUDA) STT 가속 런타임까지 설치

하는 일:
    1. uv 설치 여부 확인(없으면 안내 후 중단).
    2. 의존성 동기화 - `uv sync --extra sherpa` (--gpu 면 `--extra gpu` 도 추가해 cuBLAS 포함).
    3. 패키지 임포트 확인으로 구성이 실제로 됐는지 검증.

CPU / GPU 차이:
    STT(faster-whisper→CTranslate2)의 CUDA 가속에는 cuBLAS 런타임(nvidia-cublas-cu12)이
    필요하다. --gpu 를 주면 이 optional extra 를 추가로 설치하고, 안 주면 CPU 전용으로 더
    가볍게 구성한다(sherpa extra 는 두 경우 모두 기본 포함). GPU 가 없거나 설치하지 않아도
    앱이 자동으로 CPU(int8)로 폴백하므로, 확실하지 않으면 CPU(기본)로 구성하면 된다.

다음 단계:
    - `전체(위키)` 실행에는 Claude Code CLI 설치 + 로그인이 필요하다(전사 단계까진 불필요).
      https://claude.com/claude-code 참고, 설치 후 `claude login`.
    - `python scripts/run.py` 로 앱을 실행한다.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
from pathlib import Path

from _common import REPO_ROOT, check, info, require_uv, sync_version

# 커밋 전에 scripts/test.py(린트·포맷·테스트)를 돌리는 훅. 훅 자체는 얇게 두고 검사 내용은
# 전부 test.py 에 둔다 — 검사를 고칠 때 각자의 .git/hooks 를 다시 설치할 필요가 없다.
_PRE_COMMIT_HOOK = """#!/usr/bin/env bash
set -euo pipefail
exec uv run python "$(git rev-parse --show-toplevel)/scripts/test.py"
"""


def sync_dependencies(gpu: bool) -> None:
    """uv 로 의존성을 동기화한다. sherpa extra 는 항상 포함하고, gpu 면 CUDA STT 가속
    extra(cuBLAS)까지 추가한다."""
    command = ["uv", "sync", "--extra", "sherpa"]
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


def install_pre_commit_hook() -> None:
    """커밋 전 검사 훅을 ``.git/hooks/pre-commit`` 에 설치한다.

    훅은 git 으로 공유되지 않으므로(``.git/`` 는 추적 대상이 아니다) 클론마다 한 번은 깔아야
    한다. 그 한 번을 사람이 기억하게 두면 결국 누군가의 로컬에서만 게이트가 도는데, 그러면
    게이트가 없는 것과 같다. 그래서 환경 구성에 붙였다.

    이미 다른 내용의 훅이 있으면 덮어쓰지 않는다 — 각자 쓰던 훅을 말없이 날리면 안 된다.
    git 저장소가 아니면 조용히 건너뛴다(소스 tarball 로 받은 경우).
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return
    hooks_dir = (REPO_ROOT / proc.stdout.strip()).resolve() / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-commit"
    if hook.exists() and hook.read_text(encoding="utf-8") != _PRE_COMMIT_HOOK:
        info(f"pre-commit 훅이 이미 있어 그대로 둡니다: {hook}")
        return
    # newline="\n" 이 없으면 Windows 에서 기본 개행 변환이 걸려 CRLF 로 기록된다. 그러면
    # 셔뱅이 `#!/usr/bin/env bash\r` 이 되어 sh 가 "bash\r: not found" 로 죽는다(git 은
    # Windows 에서도 번들 sh 로 훅을 실행하므로 이 경로를 반드시 탄다).
    hook.write_text(_PRE_COMMIT_HOOK, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    info(f"pre-commit 훅 설치: {hook}")


def check_release_tooling() -> None:
    """릴리스 빌드에 필요한 도구를 확인한다(없어도 개발은 되므로 **안내만** 한다).

    ``vpk`` 는 설치기·업데이트 패키지를 만드는 Velopack CLI다. 개발·테스트에는 필요 없고
    ``scripts/build.py`` 의 마지막 단계에서만 쓰므로 여기서 막지 않는다 — 막으면 앱을
    고치기만 할 사람에게 릴리스 도구를 강요하게 된다.

    그래도 알려는 준다. 안 그러면 빌드를 수 분 돌린 뒤 마지막 패키징에서야 없다는 걸 안다.

    PATH 뿐 아니라 dotnet 글로벌 툴 기본 위치도 본다 — ``dotnet tool install -g`` 로 깔면
    그쪽에 들어가는데 셸을 다시 열기 전까지 PATH 에 안 잡히는 경우가 흔하다.
    """
    if shutil.which("vpk") is None and not (Path.home() / ".dotnet" / "tools" / "vpk").exists():
        info(
            "참고: Velopack CLI(vpk)가 없습니다. 릴리스 빌드를 하려면 설치하세요 — "
            "dotnet tool install -g vpk"
        )


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
    sync_version()  # pyproject.toml(SSOT) 버전을 src/yke/__init__.py 에 반영.
    verify_import()
    install_pre_commit_hook()
    check_release_tooling()

    info("환경 구성 완료. `python scripts/run.py` 로 앱을 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
