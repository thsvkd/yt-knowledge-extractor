#!/usr/bin/env python3
"""flet 네이티브 앱 빌드 스크립트. 실행한 OS 를 감지해 데스크톱 앱을 빌드한다.

사용:
    python scripts/build.py            # CPU 버전 빌드
    python scripts/build.py --gpu      # GPU(NVIDIA CUDA) 버전 빌드

결과물:
    dist/yke-<cpu|gpu>-<platform>/     # 실행파일 + DLL + data/ 한 세트(폴더째 배포)
    dist/yke-<cpu|gpu>-<platform>.zip  # 위 폴더 압축본(GitHub Releases 업로드용)

CPU / GPU 차이:
    STT(faster-whisper→ctranslate2)의 CUDA 가속에는 cuBLAS 런타임(nvidia-cublas-cu12)이
    필요하다. --gpu 를 주면 이 패키지를 번들에 포함하고, 안 주면 CPU 전용으로 더 가볍게
    빌드한다. GPU 번들이라도 GPU 가 없으면 앱이 자동으로 CPU(int8)로 폴백한다.
    (cuDNN 은 ctranslate2 가 자체 번들하므로 nvidia-cudnn-cu12 는 넣지 않는다 - 실측 확인.)

    구현: flet build 는 [project.dependencies] 만 번들 requirements 로 쓰므로(optional
    extra 무시), --gpu 일 때 빌드 동안만 pyproject 의 dependencies 에 cuBLAS 를 주입하고
    끝나면 원본으로 복원한다.

사전 준비:
    - Windows: Visual Studio "Desktop development with C++" 워크로드(없으면 안내).
    - Flutter SDK 는 flet build 가 필요 시 자동으로 내려받는다.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path

from _common import REPO_ROOT, check, fail, info, require_uv
from sign import maybe_sign_bundle

# flet build 메타데이터.
_PRODUCT = "YouTube Knowledge Extractor"
_ORG = "com.thsvkd"

_PYPROJECT = REPO_ROOT / "pyproject.toml"
# GPU 빌드에서 번들에 추가할 CUDA 런타임. ctranslate2 4.8 은 cuDNN 로더(cudnn64_9.dll)를
# 자체 번들하고 whisper 추론에 cuDNN 서브라이브러리를 쓰지 않으므로(RTX 2080 실측 확인:
# nvidia-cudnn 제거 후에도 GPU 추론 성공), cuBLAS 만 필요하다. nvidia-cudnn-cu12(약 1.1GB)는
# 넣지 않아 GPU 번들이 2.4GB→약 1.15GB 로 줄어든다.
_GPU_DEPS = ("nvidia-cublas-cu12",)

# Visual Studio C++ 빌드 도구 워크로드 식별자.
_VC_TOOLS_COMPONENT = "Microsoft.VisualStudio.Component.VC.Tools.x86.x64"

# flet 이 생성하는 네이티브 앱은 시작할 때마다(Python 이 뜨기도 전에) 내부적으로
# ``<문서 폴더>\flet\<패키지명>`` 을 만든다(``FLET_APP_STORAGE_DATA`` 용, 앱 코드가
# 쓰지 않아도 항상 실행됨 — flet 자체의 동작이라 우리 쪽에서 끌 방법이 없다). 이 시점은
# Python 코드가 실행되기 전이라 앱 안에서 고칠 수 없다. 실측 확인된 가장 흔한 실패 원인은
# Windows 보안의 "제어된 폴더 액세스"(랜섬웨어 방지)가 서명되지 않은 이 exe 의 "문서" 폴더
# 쓰기를 차단해 ``PathNotFoundException`` 으로 앱이 아예 뜨지 못하는 것이다(README FAQ 참고 —
# 근본 해결은 그 기능에서 이 앱을 허용하거나 꺼야 한다). 부차적으로 "문서" 가 OneDrive 로
# 리다이렉트된 경우 동기화가 아직 안 끝난 시점의 경합도 같은 증상을 낼 수 있으므로, exe 실행
# 전에 그 폴더를 미리(재시도하며) 만들어 그 경합만이라도 피하는 런처를 배포 폴더에 넣는다.
_LAUNCHER_PS1 = """$docs = [Environment]::GetFolderPath('MyDocuments')
$target = Join-Path $docs 'flet'
for ($i = 0; $i -lt 5; $i++) {
    try {
        New-Item -ItemType Directory -Force -Path $target -ErrorAction Stop | Out-Null
        exit 0
    } catch {
        Start-Sleep -Milliseconds 400
    }
}
exit 1
"""

_LAUNCHER_BAT = """@echo off
chcp 65001 >nul
setlocal
set "APP_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%prepare_storage.ps1" >nul 2>&1
if errorlevel 1 (
    echo [경고] 문서 폴더 준비에 실패했습니다. 앱이 PathNotFoundException 으로 뜨지 않을 수 있습니다.
    echo Windows 보안 -^> 바이러스 및 위협 방지 -^> 랜섬웨어 방지 관리 에서 "제어된 폴더 액세스"가
    echo 켜져 있다면 이 앱을 허용 목록에 추가하거나 꺼 보세요. ^(README 의 자주 묻는 질문 참고^)
)
for %%F in ("%APP_DIR%*.exe") do (
    start "" "%%~fF"
    goto :done
)
echo 실행 파일을 찾지 못했습니다.
pause
:done
endlocal
"""


def write_windows_launcher(dst: Path) -> None:
    """문서 폴더 준비 실패(제어된 폴더 액세스 차단 등) 시 안내를 보여주는 실행 런처를 넣는다."""
    (dst / "prepare_storage.ps1").write_text(_LAUNCHER_PS1, encoding="utf-8")
    (dst / "실행.bat").write_text(_LAUNCHER_BAT, encoding="utf-8")


def _target() -> str:
    system = platform.system()
    target = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(system)
    if target is None:
        fail(f"지원하지 않는 OS 입니다: {system}")
    return target


# -- Windows 사전 점검 --------------------------------------------------------
def _vswhere_path() -> Path:
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"


def ensure_windows_toolchain() -> None:
    """Windows 네이티브 빌드에 필요한 VS C++ 빌드 도구를 확인한다(없으면 안내 후 중단)."""
    vswhere = _vswhere_path()
    if vswhere.exists():
        result = subprocess.run(
            [str(vswhere), "-products", "*", "-requires", _VC_TOOLS_COMPONENT,
             "-property", "installationPath"],
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            info("Visual Studio C++ 빌드 도구 확인됨")
            return
    fail(
        "Visual Studio C++ 빌드 도구('Desktop development with C++')가 필요합니다.\n"
        "  https://visualstudio.microsoft.com/downloads/ 에서 Build Tools 를 설치하거나\n"
        "  winget install --id Microsoft.VisualStudio.2022.BuildTools \\\n"
        "    --override \"--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --includeRecommended --passive\""
    )


# -- pyproject 임시 편집(GPU deps 주입) --------------------------------------
def _inject_gpu_deps(original: str) -> str:
    """[project.dependencies] 배열 맨 앞에 GPU CUDA 런타임 패키지를 끼워 넣는다."""
    marker = "dependencies = [\n"
    if marker not in original:
        fail("pyproject.toml 의 dependencies 배열을 찾지 못했습니다.")
    inject = "".join(f'    "{d}",\n' for d in _GPU_DEPS)
    return original.replace(marker, marker + inject, 1)


# -- 결과물 정리/검증 --------------------------------------------------------
def stash_output(target: str, variant: str) -> Path:
    """flet build 결과(build/<target>)를 변형별 배포 폴더로 옮긴다."""
    src = REPO_ROOT / "build" / target
    if not src.exists() or not any(src.iterdir()):
        fail(f"빌드가 끝났지만 build/{target} 에 결과물이 없습니다.")
    dst = REPO_ROOT / "dist" / f"yke-{variant}-{target}"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return dst


def verify_artifact(dst: Path, target: str) -> None:
    """배포 폴더에 앱 실행 파일이 실제로 생겼는지 확인한다(flet 이 에러 후 0 종료하는 경우 대비).

    앱 실행 파일은 번들 루트에 있다(site-packages/bin 의 부수 콘솔 스크립트가 아니라).
    """
    if target == "windows":
        exes = sorted(dst.glob("*.exe"))  # 최상위만 — 번들 루트의 앱 exe
        if not exes:
            fail(f"빌드가 끝났지만 {dst} 최상위에서 앱 .exe 를 찾지 못했습니다.")
        write_windows_launcher(dst)
        info(f"완료(앱 실행파일): {exes[0]}, 실행 런처: {dst / '실행.bat'}")
    else:
        if not any(dst.iterdir()):
            fail(f"빌드가 끝났지만 {dst} 가 비어 있습니다.")
        info(f"완료: {dst}/ 를 확인하세요.")


def compress_bundle(dst: Path) -> Path:
    """배포 폴더를 zip 으로 압축한다(GitHub Releases 에 업로드할 단일 에셋)."""
    info(f"압축 중… {dst.name}.zip (수 분 걸릴 수 있음)")
    archive = shutil.make_archive(str(dst), "zip", root_dir=str(dst.parent), base_dir=dst.name)
    return Path(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="NVIDIA CUDA 런타임을 포함한 GPU 가속 버전을 빌드한다(기본은 CPU 전용).",
    )
    args = parser.parse_args()

    require_uv()
    target = _target()
    variant = "gpu" if args.gpu else "cpu"

    if target == "windows":
        ensure_windows_toolchain()

    # flet build 의 rich 진행표시가 이모지를 stdout 에 쓰는데 한국어 Windows 콘솔 기본
    # 코덱(cp949)으로는 인코딩 불가 → UnicodeEncodeError 로 죽는다. 자식 Python 을 UTF-8
    # 모드로 강제해 회피한다(다른 OS 엔 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])

    # 원본을 바이트로 보존한다. write_text 는 Windows 에서 \n→\r\n 으로 바꿔 원복 시
    # 줄바꿈만 달라지고 git 이 변경으로 오인한다. 바이트 라운드트립으로 정확히 되돌린다.
    original_bytes = _PYPROJECT.read_bytes()
    if args.gpu:
        info("GPU 빌드: pyproject 에 CUDA 런타임을 임시 주입")
        injected = _inject_gpu_deps(original_bytes.decode("utf-8"))
        _PYPROJECT.write_bytes(injected.encode("utf-8"))

    try:
        info(f"flet build {target} ({variant})")
        # --no-sync: 방금 주입한 pyproject 로 uv 가 dev venv 를 재동기화하지 않게 한다
        # (GPU 패키지는 번들에만 필요하고 개발 환경엔 넣지 않는다).
        check(
            ["uv", "run", "--no-sync", "flet", "build", target,
             "--product", _PRODUCT, "--org", _ORG],
            env=build_env,
        )
    finally:
        if args.gpu:
            _PYPROJECT.write_bytes(original_bytes)
            info("pyproject 원복 완료")

    dst = stash_output(target, variant)
    verify_artifact(dst, target)
    # 앱 exe 서명(YKE_SIGN_THUMBPRINT/YKE_SIGN_PFX 설정 시). 압축 전에 해야 zip 에 서명본이
    # 담긴다. 인증서 미지정이면 건너뛰고 미서명으로 진행한다.
    if target == "windows":
        maybe_sign_bundle(dst)
    archive = compress_bundle(dst)
    info(f"배포 폴더: {dst}  (폴더째 배포·실행하세요)")
    info(f"배포 압축본: {archive}  ({archive.stat().st_size / 1024 / 1024:.0f} MB, GitHub Releases 업로드용)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
