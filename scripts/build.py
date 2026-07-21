#!/usr/bin/env python3
"""flet 네이티브 앱 빌드 스크립트. 실행한 OS 를 감지해 데스크톱 앱을 빌드한다.

배포 모델: CPU 설치기 하나(Velopack) + GPU 온디맨드. NVIDIA 사용자는 앱에서 cuBLAS
런타임(gpu-runtime-cu12 릴리스)을 필요할 때 받는다(GPU 번들을 따로 배포하지 않는다).

사용:
    python scripts/build.py                 # Velopack 설치기(dist/velopack/)만 빌드
    python scripts/build.py --gpu-runtime   # 설치기 빌드 + cuBLAS 온디맨드 에셋 zip(dist/yke-gpu-runtime.zip)

결과물(기본):
    dist/yke-base-<platform>/  # flet 번들 폴더(설치기의 원본)
    dist/velopack/             # Setup.exe + *-full/delta.nupkg + releases.win.json
                               #   → 이 폴더 전체를 GitHub 릴리스에 올리면 자동 업데이트 동작
    서명: YKE_SIGN_THUMBPRINT/PFX 설정 시 Velopack 이 전 파일을 signtool 로 서명한다.

GPU 가속: STT(faster-whisper→ctranslate2)의 CUDA 가속에는 cuBLAS 런타임(nvidia-cublas-cu12)이
    필요하지만 설치기에는 넣지 않는다. NVIDIA 사용자가 앱 안에서 온디맨드 에셋을 받으면 그때부터
    GPU 로 동작하고, 받지 않았거나 GPU 가 없으면 자동으로 CPU(int8)로 폴백한다.

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

from _common import REPO_ROOT, check, fail, info, require_uv, sync_version
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
_GPU_RUNTIME_ASSET = "yke-gpu-runtime.zip"

# cuBLAS 온디맨드 에셋(build_gpu_runtime_asset)에 담을 CUDA 패키지. ctranslate2 4.8 은
# cuDNN 로더(cudnn64_9.dll)를 자체 번들하고 whisper 추론에 cuDNN 서브라이브러리를 쓰지
# 않으므로(RTX 2080 실측 확인: nvidia-cudnn 없이도 GPU 추론 성공), cuBLAS 만 있으면 된다.
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


# -- Velopack 설치기 / GPU 런타임 에셋 ---------------------------------------
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
        "--gpu-runtime",
        action="store_true",
        help="설치기 빌드에 더해 cuBLAS 런타임 에셋 zip 도 만든다"
        "(gpu-runtime-cu12 릴리스에 올려 온디맨드로 배포).",
    )
    args = parser.parse_args()

    require_uv()
    target = _target()

    if target == "windows":
        ensure_windows_toolchain()

    # pyproject.toml(SSOT)의 버전을 src/yke/__init__.py 에 반영한 뒤(flet build 가 이 파일을
    # 그대로 복사해 번들에 담으므로 빌드 전에 최신 상태여야 한다) 빌드에 쓸 버전으로 쓴다.
    version = sync_version()

    # flet build 의 rich 진행표시가 이모지를 stdout 에 쓰는데 한국어 Windows 콘솔 기본
    # 코덱(cp949)으로는 인코딩 불가 → UnicodeEncodeError 로 죽는다. 자식 Python 을 UTF-8
    # 모드로 강제해 회피한다(다른 OS 엔 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])
    info(f"flet build {target} (base)")
    check(
        ["uv", "run", "--no-sync", "flet", "build", target,
         "--product", _PRODUCT, "--org", _ORG],
        env=build_env,
    )
    dst = stash_output(target, "base")
    verify_artifact(dst, target)
    # 앱 exe 서명(YKE_SIGN_THUMBPRINT/YKE_SIGN_PFX 설정 시). 인증서 미지정이면 미서명.
    if target == "windows":
        maybe_sign_bundle(dst)

    # Velopack 설치기(Windows). 다른 OS 는 폴더 zip 으로 폴백.
    if target == "windows":
        out = velopack_pack(dst, version)
        info(f"Velopack 산출물: {out}\\")
        # 자동업데이트·설치에 필요한 것만 올린다: Setup.exe(설치) + *.nupkg(full/delta 업데이트
        # 페이로드) + releases.win.json(피드). Portable.zip(대용량)·RELEASES(레거시)·
        # assets.win.json(로컬 인덱스)은 GithubSource 가 쓰지 않으므로 올리지 않는다.
        info(
            f"업로드(필수만): gh release create v{version} "
            f"{out}\\*-Setup.exe {out}\\*.nupkg {out}\\releases.win.json --title v{version}"
        )
    else:
        archive = compress_bundle(dst)
        info(f"배포 폴더: {dst}  (폴더째 배포·실행하세요)")
        info(f"배포 압축본: {archive}  ({archive.stat().st_size / 1024 / 1024:.0f} MB)")

    if args.gpu_runtime:
        asset = build_gpu_runtime_asset()
        info(f"완료: GPU 런타임 에셋 {asset}  ({asset.stat().st_size / 1024 / 1024:.0f} MB)")
        info(
            f'업로드(한 번만): gh release create {_GPU_RUNTIME_TAG} "{asset}" '
            f'--title "GPU runtime (cuBLAS cu12)" --notes "온디맨드 GPU 런타임"'
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
