#!/usr/bin/env python3
"""flet 네이티브 앱 빌드 스크립트. 실행한 OS 를 감지해 데스크톱 앱을 빌드한다.

배포 모델: CPU 설치기 하나(Velopack) + GPU 온디맨드. NVIDIA 사용자는 앱에서 cuBLAS
런타임(gpu-runtime-cu12 릴리스)을 필요할 때 받는다(GPU 번들을 따로 배포하지 않는다).

사용:
    python scripts/build.py                 # CPU 번들 → Velopack 설치기(dist/velopack/)
    python scripts/build.py --gpu-runtime   # cuBLAS 온디맨드 에셋 zip(dist/…-cublas-cu12.zip)
    python scripts/build.py --no-installer  # CPU 번들 폴더/zip 만(설치기 생략)
    python scripts/build.py --gpu           # (로컬/수동) nvidia 포함 GPU 번들 폴더+zip

결과물(기본):
    dist/yke-cpu-<platform>/    # flet 번들 폴더(설치기의 원본)
    dist/velopack/             # Setup.exe + *-full/delta.nupkg + releases.win.json
                               #   → 이 폴더 전체를 GitHub 릴리스에 올리면 자동 업데이트 동작
    서명: YKE_SIGN_THUMBPRINT/PFX 설정 시 Velopack 이 전 파일을 signtool 로 서명한다.

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
import re
import shutil
import subprocess
from pathlib import Path

from _common import REPO_ROOT, check, fail, info, require_uv
from sign import find_signtool, maybe_sign_bundle, velopack_sign_params

# flet build 메타데이터.
_PRODUCT = "YouTube Knowledge Extractor"
_ORG = "com.thsvkd"

# Velopack 패키징 메타. packId(설치 폴더/자동업데이트 식별자)와 레포 URL 은
# src/yke/velopack_update.py·gpu_runtime.py 의 값과 반드시 일치해야 한다(앱이 이 packId 로
# %LocalAppData% 에 설치되고 이 레포 릴리스에서 업데이트를 받는다).
_PACK_ID = "YtKnowledgeExtractor"
_VELOPACK_CHANNEL = "win"
_REPO_URL = "https://github.com/thsvkd/yt-knowledge-extractor"
_APP_EXE = "yt-knowledge-extractor.exe"
# cuBLAS 런타임 온디맨드 에셋(gpu_runtime.GPU_RUNTIME_TAG/ASSET 과 일치). 앱 버전과 무관해
# 이 전용 태그에 한 번만 올려 두면 CPU 설치본이 필요 시 받아 쓴다.
_GPU_RUNTIME_TAG = "gpu-runtime-cu12"
_GPU_RUNTIME_ASSET = "yke-gpu-runtime-cublas-cu12.zip"

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


# -- Velopack 설치기 / GPU 런타임 에셋 ---------------------------------------
def _app_version() -> str:
    """src/yke/__init__.py 의 __version__ 을 읽는다(Velopack packVersion 용)."""
    init = REPO_ROOT / "src" / "yke" / "__init__.py"
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', init.read_text(encoding="utf-8"))
    if not m:
        fail("src/yke/__init__.py 에서 __version__ 을 찾지 못했습니다.")
    return m.group(1)


def _find_vpk() -> str:
    """Velopack CLI(vpk) 경로. PATH 또는 dotnet 글로벌 툴 기본 위치에서 찾는다."""
    exe = shutil.which("vpk")
    if exe:
        return exe
    cand = Path.home() / ".dotnet" / "tools" / ("vpk.exe" if os.name == "nt" else "vpk")
    if cand.exists():
        return str(cand)
    fail("vpk(Velopack CLI)를 찾지 못했습니다. 설치: dotnet tool install -g vpk")


def velopack_pack(bundle_dir: Path, version: str) -> Path:
    """CPU 번들을 Velopack 설치기(Setup.exe) + 업데이트 패키지로 만든다.

    기존 GitHub 릴리스를 먼저 받아(vpk download github) 있으면 그 위에 델타를 만든다(첫
    릴리스면 없음 → 전체 릴리스). 인증서(YKE_SIGN_THUMBPRINT/PFX)가 있으면 --signParams 로
    전 파일을 서명한다. 산출물: dist/velopack/ (Setup.exe, *-full.nupkg, *-delta.nupkg,
    releases.win.json …). 이 폴더 전체를 GitHub 릴리스에 올리면 앱이 자동 업데이트에 쓴다.
    """
    vpk = _find_vpk()
    out = REPO_ROOT / "dist" / "velopack"
    out.mkdir(parents=True, exist_ok=True)

    # 서명 요청 시 vpk 가 signtool 을 PATH 에서 찾도록 signtool 디렉터리를 앞에 붙인다.
    env = dict(os.environ)
    sign_params = velopack_sign_params()
    if sign_params:
        signtool = find_signtool()
        if signtool is None:
            fail("서명이 요청됐지만 signtool.exe 를 찾지 못했습니다. Windows SDK 를 설치하세요.")
        env["PATH"] = str(signtool.parent) + os.pathsep + env.get("PATH", "")

    # 1) 기존 릴리스를 받아 델타 기준으로 삼는다. 첫 릴리스/네트워크 실패면 건너뛴다.
    info("기존 Velopack 릴리스 조회(델타 기준)…")
    dl = subprocess.run(
        [vpk, "download", "github", "--repoUrl", _REPO_URL,
         "--outputDir", str(out), "--channel", _VELOPACK_CHANNEL],
        cwd=REPO_ROOT, capture_output=True, text=True, env=env,
    )
    if dl.returncode != 0:
        info("  기존 릴리스 없음/조회 실패 → 전체 릴리스로 진행(델타 없음).")

    # 2) 패키징(+서명)
    cmd = [
        vpk, "pack",
        "--packId", _PACK_ID,
        "--packVersion", version,
        "--packDir", str(bundle_dir),
        "--mainExe", _APP_EXE,
        "--packTitle", _PRODUCT,
        "--packAuthors", "thsvkd",
        "--channel", _VELOPACK_CHANNEL,
        "--outputDir", str(out),
    ]
    if sign_params:
        cmd += ["--signParams", sign_params]
        info("Velopack 패키징(서명 포함)…")
    else:
        info("Velopack 패키징(미서명 — YKE_SIGN_THUMBPRINT/PFX 미설정)…")
    check(cmd, env=env)
    return out


def build_gpu_runtime_asset() -> Path:
    """cuBLAS 런타임(nvidia/*)만 담은 zip 을 만든다(gpu_runtime 온디맨드 다운로드용).

    앱 버전과 무관하므로 gpu-runtime-cu12 릴리스에 한 번 올려 두면 CPU 설치본이 필요 시
    받아 쓴다. zip 최상위는 nvidia/ 트리다(gpu_runtime.download 가 그대로 푼다).
    """
    staging = REPO_ROOT / "build" / "_gpu_runtime"
    if staging.exists():
        shutil.rmtree(staging)
    info(f"cuBLAS 런타임 설치(임시): {', '.join(_GPU_DEPS)}")
    check(["uv", "pip", "install", "--target", str(staging), *_GPU_DEPS])
    if not (staging / "nvidia").is_dir():
        fail("nvidia 런타임 디렉터리를 찾지 못했습니다(설치 실패?).")
    dist = REPO_ROOT / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    base = dist / _GPU_RUNTIME_ASSET[: -len(".zip")]
    info(f"압축 중… {_GPU_RUNTIME_ASSET} (수 분 걸릴 수 있음)")
    archive = shutil.make_archive(str(base), "zip", root_dir=str(staging), base_dir="nvidia")
    shutil.rmtree(staging)
    return Path(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="(로컬/수동용) NVIDIA CUDA 런타임을 포함한 GPU 번들 폴더+zip. "
        "배포 기본은 CPU 설치기 + GPU 온디맨드다.",
    )
    parser.add_argument(
        "--gpu-runtime",
        action="store_true",
        help="cuBLAS 런타임 에셋 zip 만 만든다(gpu-runtime-cu12 릴리스에 올려 온디맨드로 배포).",
    )
    parser.add_argument(
        "--no-installer",
        action="store_true",
        help="Velopack 설치기 생성을 건너뛰고 CPU 번들 폴더/zip 만 만든다.",
    )
    args = parser.parse_args()

    require_uv()
    target = _target()

    if target == "windows":
        ensure_windows_toolchain()

    # cuBLAS 온디맨드 에셋만 만들고 끝낸다(앱 빌드와 독립).
    if args.gpu_runtime:
        asset = build_gpu_runtime_asset()
        info(f"완료: GPU 런타임 에셋 {asset}  ({asset.stat().st_size / 1024 / 1024:.0f} MB)")
        info(
            f'업로드(한 번만): gh release create {_GPU_RUNTIME_TAG} "{asset}" '
            f'--title "GPU runtime (cuBLAS cu12)" --notes "온디맨드 GPU 런타임"'
        )
        return 0

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
        # (로컬/수동용) GPU 번들 = CPU 번들 + nvidia 런타임. GPU 전용 flet build 를 하지 않고
        # CPU 산출물을 재사용한다(두 변형의 유일한 차이가 site-packages/nvidia 뿐). Velopack
        # 설치기는 CPU 기준(온디맨드 GPU)이므로 여기선 폴더+zip 만 만든다.
        cpu_dst = REPO_ROOT / "dist" / f"yke-cpu-{target}"
        if cpu_dst.exists() and any(cpu_dst.iterdir()):
            info(f"기존 CPU 번들 재사용: {cpu_dst}  (nvidia 런타임만 추가)")
        else:
            info("GPU 빌드에 쓸 CPU 번들이 없어 먼저 CPU 를 빌드합니다.")
            cpu_dst = build_cpu()
        dst = make_gpu_from_cpu(cpu_dst, target)
        verify_artifact(dst, target)
        archive = compress_bundle(dst)
        info(f"배포 폴더: {dst}  (폴더째 배포·실행하세요)")
        info(f"배포 압축본: {archive}  ({archive.stat().st_size / 1024 / 1024:.0f} MB)")
        return 0

    # 기본: CPU 번들 → Velopack 설치기(Windows). 다른 OS 는 폴더 zip 으로 폴백.
    dst = build_cpu()
    if target == "windows" and not args.no_installer:
        version = _app_version()
        out = velopack_pack(dst, version)
        info(f"Velopack 산출물: {out}\\  (Setup.exe + *.nupkg + releases.win.json)")
        info(
            f"업로드: gh release create v{version} {out}\\*  --title v{version}  "
            f"(또는 gh release upload v{version} {out}\\*)"
        )
    else:
        archive = compress_bundle(dst)
        info(f"배포 폴더: {dst}  (폴더째 배포·실행하세요)")
        info(f"배포 압축본: {archive}  ({archive.stat().st_size / 1024 / 1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
