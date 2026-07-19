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

    구현: GPU 전용 flet build 를 따로 하지 않는다. CPU 를 한 번 빌드한 뒤 그 산출물을 복사하고
    site-packages 에 cuBLAS 런타임만 얹어 GPU 번들을 만든다(두 변형의 유일한 차이가 nvidia
    뿐이라 안전하고, GPU 빌드가 수 분 → 수십 초로 준다). CPU 번들이 이미 있으면 재사용한다.

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


def make_gpu_from_cpu(cpu_dst: Path, target: str) -> Path:
    """CPU 배포 폴더를 복사한 뒤 NVIDIA CUDA 런타임(cuBLAS/nvrtc) DLL 만 얹어 GPU 번들을 만든다.

    두 변형의 유일한 차이는 ``site-packages/nvidia`` 뿐이므로 GPU 전용 flet build 를 생략하고
    CPU 산출물을 그대로 재사용한다(GPU 빌드가 수 분 → 수십 초로 줄고, CPU 와 앱 코드가 100%
    동일함이 보장된다). CPU exe 서명도 복사로 그대로 유지된다. nvidia 런타임은 임시 target 에
    설치해 nvidia 패키지 디렉터리만 골라 옮긴다.
    """
    gpu_dst = REPO_ROOT / "dist" / f"yke-gpu-{target}"
    if gpu_dst.exists():
        shutil.rmtree(gpu_dst)
    info(f"CPU 번들 복사 → {gpu_dst.name}")
    shutil.copytree(cpu_dst, gpu_dst)
    site = gpu_dst / "site-packages"
    if not site.exists():
        fail(f"CPU 번들에 site-packages 가 없습니다: {site}")

    staging = REPO_ROOT / "build" / "_gpu_deps"
    if staging.exists():
        shutil.rmtree(staging)
    info(f"NVIDIA CUDA 런타임 설치(임시): {', '.join(_GPU_DEPS)}")
    check(["uv", "pip", "install", "--target", str(staging), *_GPU_DEPS])
    # nvidia 패키지(런타임 폴더 + dist-info)만 번들 site-packages 로 옮긴다.
    moved = [
        item.name
        for item in sorted(staging.iterdir())
        if item.name == "nvidia" or item.name.startswith("nvidia_")
    ]
    for name in moved:
        src, dest = staging / name, site / name
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        shutil.move(str(src), str(dest))
    shutil.rmtree(staging)
    if not moved:
        fail("nvidia 런타임 패키지를 찾지 못했습니다(설치 실패?).")
    info(f"nvidia 런타임 추가 완료: {', '.join(moved)}")
    return gpu_dst


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

    if target == "windows":
        ensure_windows_toolchain()

    # flet build 의 rich 진행표시가 이모지를 stdout 에 쓰는데 한국어 Windows 콘솔 기본
    # 코덱(cp949)으로는 인코딩 불가 → UnicodeEncodeError 로 죽는다. 자식 Python 을 UTF-8
    # 모드로 강제해 회피한다(다른 OS 엔 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    def build_cpu() -> Path:
        """flet build 로 CPU 번들을 만들고(검증·서명 포함) 배포 폴더를 돌려준다."""
        info("의존성 동기화 (uv sync)")
        check(["uv", "sync"])
        info(f"flet build {target} (cpu)")
        check(
            ["uv", "run", "--no-sync", "flet", "build", target,
             "--product", _PRODUCT, "--org", _ORG],
            env=build_env,
        )
        d = stash_output(target, "cpu")
        verify_artifact(d, target)
        # 앱 exe 서명(YKE_SIGN_THUMBPRINT/YKE_SIGN_PFX 설정 시). 인증서 미지정이면 미서명.
        if target == "windows":
            maybe_sign_bundle(d)
        return d

    if args.gpu:
        # GPU 번들 = CPU 번들 + nvidia 런타임. GPU 전용 flet build 를 하지 않고 CPU 산출물을
        # 재사용한다(두 변형의 유일한 차이가 site-packages/nvidia 뿐). 이미 만든 CPU 번들이
        # 있으면 그대로 쓰고(권장 워크플로: CPU 빌드 → --gpu 로 nvidia 만 추가), 없을 때만
        # CPU 를 빌드한다. exe 서명은 CPU 복사로 그대로 유지된다.
        cpu_dst = REPO_ROOT / "dist" / f"yke-cpu-{target}"
        if cpu_dst.exists() and any(cpu_dst.iterdir()):
            info(f"기존 CPU 번들 재사용: {cpu_dst}  (nvidia 런타임만 추가)")
        else:
            info("GPU 빌드에 쓸 CPU 번들이 없어 먼저 CPU 를 빌드합니다.")
            cpu_dst = build_cpu()
        dst = make_gpu_from_cpu(cpu_dst, target)
        verify_artifact(dst, target)
    else:
        dst = build_cpu()

    archive = compress_bundle(dst)
    info(f"배포 폴더: {dst}  (폴더째 배포·실행하세요)")
    info(f"배포 압축본: {archive}  ({archive.stat().st_size / 1024 / 1024:.0f} MB, GitHub Releases 업로드용)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
