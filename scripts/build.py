#!/usr/bin/env python3
"""flet 네이티브 앱 빌드 스크립트. 실행한 OS 를 감지해 데스크톱 앱을 빌드한다.

배포 모델: CPU 설치기 하나(Velopack) + GPU 온디맨드. NVIDIA 사용자는 앱에서 cuBLAS
런타임(gpu-runtime-cu12 릴리스)을 필요할 때 받는다(GPU 번들을 따로 배포하지 않는다).

사용:
    python scripts/build.py                 # Velopack 설치기(dist/velopack/)만 빌드
    python scripts/build.py --gpu-runtime   # 설치기 빌드 + cuBLAS 온디맨드 에셋 zip(dist/yke-gpu-runtime.zip)

결과물(기본):
    dist/yke-base-<platform>/  # flet 번들 폴더(설치기의 원본). macOS 는 이 안의 *.app 이 원본.
    dist/velopack/             # Velopack 산출물. 채널 접미사 덕에 win/osx 파일명은 겹치지
                               # 않지만, 이 폴더 자체는 **OS 별 로컬 폴더**여야 한다(두 OS 가
                               # 공유하면 vpk 가 상대 채널 인덱스를 덮어쓴다 —
                               # scripts/platform_spec.py 의 VELOPACK_OUT 주석 참고).
        - Windows: *-Setup.exe + *-<버전>-full/delta.nupkg + releases.win.json
        - macOS:   *-Setup.pkg + *-<버전>-osx-full/delta.nupkg + releases.osx.json
                               #   → 이 파일들을 GitHub 릴리스에 올리면 자동 업데이트 동작
    서명: (Windows 만) YKE_SIGN_THUMBPRINT/PFX 설정 시 Velopack 이 전 파일을 signtool 로
    서명한다. macOS 는 이번 범위에서 **미서명 배포**이며, 설치 시 Gatekeeper 를 우회하는
    방법은 README 에 안내한다.

GPU 가속: STT(faster-whisper→ctranslate2)의 CUDA 가속에는 cuBLAS 런타임(nvidia-cublas-cu12)이
    필요하지만 설치기에는 넣지 않는다. NVIDIA 사용자가 앱 안에서 온디맨드 에셋을 받으면 그때부터
    GPU 로 동작하고, 받지 않았거나 GPU 가 없으면 자동으로 CPU(int8)로 폴백한다(macOS 는 해당 없음).

사전 준비:
    - 공통: Velopack CLI — `dotnet tool install -g vpk`
      (앱이 쓰는 velopack 파이썬 패키지와 같은 1.2.0 계열이어야 한다).
    - Windows: Visual Studio "Desktop development with C++" 워크로드(없으면 안내).
    - macOS: **전체 Xcode**(App Store) + CocoaPods(`brew install cocoapods`).
      Command Line Tools 만으로는 안 된다 — 그것으로도 사전 점검이 통과하는 것처럼
      보이지만(ensure_macos_toolchain 주석 참고) flet build 가 Flutter 컴파일 직전에
      죽는다. vpk 가 부르는 pkgbuild/productbuild/ditto/codesign/plutil 은 CLT 에 있다.
    - Flutter SDK 는 flet build 가 필요 시 자동으로 내려받는다.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
from pathlib import Path

import flet_template
import platform_spec
from _common import REPO_ROOT, check, fail, info, require_uv, sync_version
from sign import find_signtool, maybe_sign_bundle, velopack_sign_params

# flet build 메타데이터. 제품명·packId·레포 URL 등 "빌드와 업로드가 반드시 같은 값을
# 써야 하는" 것들은 scripts/platform_spec.py 가 단일 소스다(여기서 다시 정의하면
# deploy.py 와 어긋나 자동 업데이트가 조용히 깨진다).
_ORG = "com.thsvkd"

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

# serious_python_windows 플러그인이 `%WINDIR%/System32` 에서 번들로 복사하는 VC 런타임.
# 이 경로가 32비트로 오염되는 문제와 우회는 prepare_vcruntime_shim 참고.
_VC_RUNTIME_DLLS = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
_PE_MACHINE_AMD64 = 0x8664

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


def flet_version() -> str:
    """빌드에 쓰이는 flet 버전. 패치용 빌드 템플릿을 같은 버전으로 받으려고 확인한다.

    ``flet build`` 가 템플릿 태그로 쓰는 값(``flet.version.flet_version``)과 정확히 같은
    값을 써야 하므로, pyproject 의 핀을 파싱하지 않고 동기화된 환경에서 직접 읽는다.
    """
    result = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "python",
            "-c",
            "import flet.version as v; print(v.flet_version)",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
    if result.returncode != 0 or not version:
        fail(f"flet 버전을 확인하지 못했습니다: {result.stderr.strip() or result.stdout.strip()}")
    return version


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
            [
                str(vswhere),
                "-products",
                "*",
                "-requires",
                _VC_TOOLS_COMPONENT,
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
        )
        if result.stdout.strip():
            info("Visual Studio C++ 빌드 도구 확인됨")
            return
    fail(
        "Visual Studio C++ 빌드 도구('Desktop development with C++')가 필요합니다.\n"
        "  https://visualstudio.microsoft.com/downloads/ 에서 Build Tools 를 설치하거나\n"
        "  winget install --id Microsoft.VisualStudio.2022.BuildTools \\\n"
        '    --override "--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --includeRecommended --passive"'
    )


# -- macOS 사전 점검 ----------------------------------------------------------
# Velopack 의 OsxBuildTools 는 .pkg 를 만들면서 아래 다섯 명령을 직접 호출한다. 하나라도
# 없으면 flet build 를 몇 분(Flutter 컴파일) 돌린 **뒤에야** vpk 단계에서 죽으므로,
# 빌드를 시작하기 전에 확인한다.
_MACOS_TOOLS = ("pkgbuild", "productbuild", "ditto", "codesign", "plutil")
_XCODE_CLT_HINT = "Xcode Command Line Tools 가 필요합니다: xcode-select --install"
# Flutter 의 macOS 데스크톱 빌드는 **전체 Xcode** 를 요구한다(CLT 로는 안 된다).
# 실측으로 겪은 함정: CLT 만 깔린 머신에서도 `xcode-select -p` 는 성공하고
# (/Library/Developer/CommandLineTools 를 출력) pkgbuild·ditto·codesign 도 전부 CLT 에
# 들어 있어 존재 확인을 통과한다. 그래서 예전 점검은 초록불을 준 뒤 Flutter SDK 를 다
# 내려받고 몇 분 지나서야 `flet build macos` 가 "Xcode installation is incomplete" 로
# 죽었다. 그 구분이 되는 명령이 xcodebuild 다 — CLT 전용 환경에서는 실행 자체가
# 실패한다("tool 'xcodebuild' requires Xcode, but active developer directory ... is a
# command line tools instance"). 그래서 경로 문자열이 아니라 이 명령으로 판정한다
# (Xcode 를 /Applications 밖에 두거나 여러 버전을 xcode-select 로 전환하는 경우까지 맞다).
_XCODE_FULL_HINT = (
    "전체 Xcode 가 필요합니다(Command Line Tools 만으로는 macOS 앱을 빌드할 수 없습니다).\n"
    "  1) App Store 에서 Xcode 를 설치한 뒤\n"
    "  2) sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer\n"
    "  3) sudo xcodebuild -runFirstLaunch"
)
# flet 은 flet_desktop 등 Flutter 플러그인을 쓰고, macOS 플러그인은 CocoaPods 로 엮인다.
# 없으면 Flutter 컴파일 단계까지 간 뒤에 죽으므로 미리 잡는다.
_COCOAPODS_HINT = (
    "CocoaPods 가 필요합니다(Flutter 플러그인을 macOS 에서 엮는 데 씁니다).\n"
    "  brew install cocoapods   (또는 sudo gem install cocoapods)"
)


def ensure_macos_toolchain() -> None:
    """macOS 네이티브 빌드/패키징에 필요한 도구를 확인한다(없으면 안내 후 중단).

    빌드를 시작하기 **전에** 다 확인한다 — 여기서 놓치면 Flutter SDK 다운로드와 컴파일에
    수 분을 쓴 뒤에야 실패해서, 원인이 환경 문제였다는 게 한참 뒤에 드러난다.
    """
    try:
        result = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True)
    except OSError:  # xcode-select 자체가 없는(=CLT 미설치) 경우
        fail(_XCODE_CLT_HINT)
    if result.returncode != 0:
        fail(_XCODE_CLT_HINT)
    developer_dir = result.stdout.strip()

    # Velopack 의 OsxBuildTools 가 .pkg 를 만들며 직접 호출하는 명령들(위 _MACOS_TOOLS 주석).
    missing = [tool for tool in _MACOS_TOOLS if shutil.which(tool) is None]
    if missing:
        fail(f"{_XCODE_CLT_HINT}\n  (없는 명령: {', '.join(missing)} — vpk 가 직접 호출한다)")

    # 전체 Xcode 판정. CLT 전용이면 여기서 걸린다.
    try:
        xcodebuild = subprocess.run(["xcodebuild", "-version"], capture_output=True, text=True)
    except OSError:
        fail(f"{_XCODE_FULL_HINT}\n  (현재 활성 개발자 디렉터리: {developer_dir})")
    if xcodebuild.returncode != 0:
        fail(
            f"{_XCODE_FULL_HINT}\n"
            f"  (현재 활성 개발자 디렉터리: {developer_dir})\n"
            f"  xcodebuild: {xcodebuild.stderr.strip() or xcodebuild.stdout.strip()}"
        )

    if shutil.which("pod") is None:
        fail(_COCOAPODS_HINT)

    xcode_version = xcodebuild.stdout.strip().splitlines()[0] if xcodebuild.stdout.strip() else "?"
    info(f"macOS 빌드 도구 확인됨: {xcode_version} ({developer_dir})")


# -- VC 런타임(x64) 확보 -----------------------------------------------------
def _pe_machine(path: Path) -> int:
    """PE 헤더의 machine 값을 읽는다(0x8664=x64, 0x14C=x86)."""
    with path.open("rb") as f:
        f.seek(0x3C)
        pe_offset = int.from_bytes(f.read(4), "little")
        f.seek(pe_offset + 4)
        return int.from_bytes(f.read(2), "little")


def _vc_redist_x64_dir() -> Path:
    """VS 설치에서 가장 최신인 x64 VC 런타임 재배포 폴더를 찾는다."""
    vswhere = _vswhere_path()
    result = subprocess.run(
        [
            str(vswhere),
            "-products",
            "*",
            "-requires",
            _VC_TOOLS_COMPONENT,
            "-property",
            "installationPath",
        ],
        capture_output=True,
        text=True,
    )
    paths = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not paths:
        fail("VC 빌드 도구가 설치된 Visual Studio 인스턴스를 찾지 못했습니다.")
    redist_root = Path(paths[0]) / "VC" / "Redist" / "MSVC"
    # 구조는 <버전>/x64/Microsoft.VC<툴셋>.CRT. 버전 폴더는 숫자로 비교한다
    # (문자열 정렬은 14.9 를 14.10 보다 뒤로 놓아 최신을 잘못 고른다).
    candidates = sorted(
        redist_root.glob("*/x64/Microsoft.VC*.CRT"),
        key=lambda p: [int(x) for x in p.parents[1].name.split(".") if x.isdigit()],
    )
    if not candidates:
        fail(f"{redist_root} 에서 x64 VC 런타임 재배포 폴더를 찾지 못했습니다.")
    return candidates[-1]


def prepare_vcruntime_shim() -> Path:
    """x64 VC 런타임만 담은 가짜 WINDIR 을 만들어 그 경로를 돌려준다.

    serious_python_windows 플러그인은 번들에 넣을 VC 런타임을 ``$ENV{WINDIR}/System32``
    에서 가져간다. 그런데 Flutter 가 INSTALL 단계를 실행하는 VS 번들 cmake.exe 는 32비트라
    그 경로 접근이 WOW64 리다이렉션으로 SysWOW64(32비트 런타임)를 향한다. 그 결과 x64 앱
    번들에 32비트 msvcp140/vcruntime140 이 들어가고(앱이 python312.dll 을 못 읽는다),
    애초에 x64 전용인 vcruntime140_1.dll 은 SysWOW64 에 아예 없어 빌드가 INSTALL 단계에서
    실패한다 — x86 재배포를 설치해도 그 파일은 생기지 않는다(x86 재배포에 없는 파일이다).

    그래서 빌드 동안만 WINDIR 을 이 shim 으로 바꾼다. 리다이렉션 대상이 아닌 평범한
    디렉터리라 32비트 cmake 도 x64 DLL 을 그대로 복사한다. 시스템 경로를 찾는 도구들이
    쓰는 SystemRoot 는 건드리지 않으므로 빌드 도구 동작에는 영향이 없다.
    """
    redist = _vc_redist_x64_dir()
    shim = REPO_ROOT / "build" / "_vcruntime_shim"
    system32 = shim / "System32"
    system32.mkdir(parents=True, exist_ok=True)
    for name in _VC_RUNTIME_DLLS:
        src = redist / name
        if not src.exists():
            fail(f"{src} 를 찾지 못했습니다(VS C++ 빌드 도구 설치를 확인하세요).")
        shutil.copy2(src, system32 / name)
    info(f"VC 런타임(x64) shim: {system32}  (원본: {redist})")
    return shim


def reset_cmake_cache_if_stale(shim: Path) -> None:
    """생성된 CMake install 스크립트가 shim 을 가리키지 않으면 구성 캐시를 지운다.

    ``$ENV{WINDIR}`` 은 CMake 구성 시점에만 읽히므로, 예전 WINDIR 로 구성해 둔 빌드 트리가
    남아 있으면 환경 변수를 바꿔도 옛 경로가 그대로 쓰인다. 캐시만 지우면 CMake 가 다시
    구성하고 컴파일 산출물은 재사용한다(빌드 트리를 통째로 지우는 것보다 훨씬 싸다).
    """
    build_dir = REPO_ROOT / "build" / "flutter" / "build" / "windows" / "x64"
    install_script = build_dir / "cmake_install.cmake"
    if not install_script.exists():
        return
    if str(shim).replace("\\", "/") in install_script.read_text(encoding="utf-8", errors="replace"):
        return
    cache = build_dir / "CMakeCache.txt"
    if cache.exists():
        cache.unlink()
        info("CMake 구성 캐시 삭제(VC 런타임 경로 갱신 필요)")


def verify_vc_runtime_arch(bundle_dir: Path) -> None:
    """번들에 들어간 VC 런타임이 정말 x64 인지 확인한다(32비트면 앱이 뜨지 않는다)."""
    wrong = [
        f"{name}({'없음' if not (bundle_dir / name).exists() else '32비트'})"
        for name in _VC_RUNTIME_DLLS
        if not (bundle_dir / name).exists() or _pe_machine(bundle_dir / name) != _PE_MACHINE_AMD64
    ]
    if wrong:
        fail(f"번들의 VC 런타임이 x64 가 아닙니다: {', '.join(wrong)}")
    info("번들 VC 런타임 x64 확인됨")


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
    elif target == "macos":
        # vpk --mainExe 는 <App>.app/Contents/MacOS/<이름> 을 **이름 완전 일치**로 찾는다.
        # 이름이 어긋나면 패키징 단계에서야 죽으므로 여기서 실제 내용을 보여주며 끊는다.
        app = macos_app_bundle(dst)
        exe = app / "Contents" / "MacOS" / platform_spec.spec_for("macos").main_exe
        if not exe.is_file():
            listing = (
                ", ".join(sorted(p.name for p in (app / "Contents" / "MacOS").glob("*")))
                or "(비어 있음)"
            )
            fail(
                f"{exe} 가 없습니다. Contents/MacOS 실제 내용: {listing}\n"
                "vpk --mainExe 는 이 이름을 그대로 찾으므로 이대로는 패키징이 실패합니다."
            )
        if not os.access(exe, os.X_OK):
            fail(
                f"{exe} 에 실행 권한이 없습니다(번들이 깨졌거나 압축/복사 과정에서 권한이 날아갔습니다)."
            )
        info(f"완료(앱 번들): {app}")
    else:
        if not any(dst.iterdir()):
            fail(f"빌드가 끝났지만 {dst} 가 비어 있습니다.")
        info(f"완료: {dst}/ 를 확인하세요.")


def prune_external_symlinks(bundle: Path) -> list[Path]:
    """``bundle`` 밖을 가리키는 심볼릭 링크를 지우고, 지운 목록을 돌려준다.

    실측으로 겪은 함정: flet(serious_python)이 만든 ``.app`` 안에는 빌드 머신의 pub 캐시를
    가리키는 링크가 남는다.

        …/python.bundle/Contents/Resources/site-packages/.pod
            -> ~/.pub-cache/hosted/pub.dev/serious_python_darwin-1.0.1/darwin

    그 대상 폴더 안에 다시 ``dist_macos/site-packages/.pod`` 가 있어 **자기 자신으로 되돌아오는
    무한 루프**가 된다. ``vpk pack`` 은 번들을 복사하며 이 링크를 따라 들어가다가 경로가
    한계를 넘어 죽는다(``PathTooLongException`` — 실측: 같은 조각이 20여 번 반복된 경로).

    루프가 아니더라도 이 링크는 애초에 배포되면 안 된다. 가리키는 경로는 **빌드한 사람의
    홈 디렉터리**라 사용자 맥에는 존재하지 않는다(끊어진 링크가 그대로 나간다).

    ``.pod`` 만 이름으로 집어 지우지 않고 "번들 밖을 가리키는가"라는 불변식으로 판정한다 —
    배포 가능한 ``.app`` 은 자기 바깥을 참조하면 안 된다는 게 본질이고, flet/serious_python 이
    나중에 이름이나 위치를 바꿔도 그대로 잡힌다. 프레임워크 내부의 정상적인 상대 링크
    (``Versions/Current`` 등, 실측 59개)는 번들 안을 가리키므로 건드리지 않는다.

    경로 판정에 ``realpath`` 를 쓰지 않는 것도 의도적이다 — 위의 순환 링크에서 그대로 루프에
    빠진다. 파일시스템을 건드리지 않는 ``normpath`` 로만 계산한다.
    """
    # **반드시 절대 경로로 순회한다.** 상대 경로로 walk 하면 dirpath 가 상대라 아래에서
    # 계산한 링크 대상도 상대가 되고, 절대 경로인 bundle_root 와는 어떤 내부 링크도 일치하지
    # 않아 **번들 안의 정상 링크까지 전부 지워진다**(실제로 겪음: 60개 중 60개 삭제, 프레임워크
    # 구조가 통째로 깨졌다). 두 경로의 절대/상대 표현을 반드시 맞춰야 한다.
    bundle = bundle.resolve()
    bundle_root = os.path.normpath(str(bundle))
    removed: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(bundle, followlinks=False):
        for name in (*dirnames, *filenames):
            path = Path(dirpath) / name
            if not path.is_symlink():
                continue
            target = os.readlink(path)
            if not os.path.isabs(target):
                target = os.path.join(dirpath, target)
            target = os.path.normpath(target)
            if target == bundle_root or target.startswith(bundle_root + os.sep):
                continue
            path.unlink()
            removed.append(path)
    return removed


def macos_app_bundle(dst: Path) -> Path:
    """flet build macos 결과 폴더에서 앱 번들(``*.app``)을 찾아 그 경로를 돌려준다.

    Velopack 에는 **이 ``.app`` 경로 자체**를 ``--packDir`` 로 넘겨야 한다. 상위 폴더를
    넘기면 Velopack 이 폴더 내용으로 새 번들을 만들려 하면서 ``.icns`` 아이콘을 요구하는
    조용한 오작동이 난다(이미 완성된 번들을 다시 감싸는 셈).

    주의: Velopack 은 이 번들을 **이름 그대로 쓰지 않는다**. 패키징 중 ``--packTitle`` 이름의
    ``.app`` 으로 복사해 담으므로, 여기서 넘기는 ``yt-knowledge-extractor.app`` 은 설치본에서
    ``YouTube Knowledge Extractor.app`` 이 된다(실측 확인: 만들어진 .pkg 의 페이로드 최상위가
    ``YouTube Knowledge Extractor.app`` 이고 postinstall 스크립트도 그 이름으로 앱을 연다).
    README·docs/SPEC.md 의 설치 경로 안내가 이 이름에 걸려 있다.
    """
    apps = sorted(p for p in dst.glob("*.app") if p.is_dir())
    if not apps:
        fail(f"flet build macos 결과에 .app 이 없습니다: {dst}")
    if len(apps) > 1:
        fail(f"{dst} 에 .app 이 여러 개입니다: {', '.join(p.name for p in apps)}")
    return apps[0]


def compress_bundle(dst: Path) -> Path:
    """배포 폴더를 zip 으로 압축한다(**linux 전용 폴백**).

    macOS 에서는 절대 쓰지 않는다: ``shutil.make_archive`` 는 ``.app`` 안
    ``FlutterMacOS.framework`` 의 심링크 5개를 실제 파일/디렉터리로 복제해 번들을 깨뜨린다
    (실측 확인). macOS 의 portable zip 은 Velopack 이 ``ditto`` 로 만들어 준다.
    """
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


def velopack_pack(pack_dir: Path, version: str, spec: platform_spec.PlatformSpec) -> Path:
    """앱 번들을 Velopack 설치기 + 업데이트 패키지로 만든다.

    ``pack_dir`` 는 Windows 면 번들 폴더, macOS 면 ``*.app`` 번들 **자체**다
    (:func:`macos_app_bundle` 참고).

    기존 GitHub 릴리스를 먼저 받아(vpk download github) 있으면 그 위에 델타를 만든다(첫
    릴리스면 없음 → 전체 릴리스). Windows 는 인증서(YKE_SIGN_THUMBPRINT/PFX)가 있으면
    --signParams 로 전 파일을 서명한다. macOS 는 이번 범위에서 미서명 배포라
    --signAppIdentity/--signInstallIdentity/--notaryProfile 을 넘기지 않는다.

    산출물: dist/velopack/ (Setup.exe|Setup.pkg, *-full.nupkg, *-delta.nupkg,
    releases.<channel>.json …). 자세한 파일명 규칙은 scripts/platform_spec.py 참고.
    """
    vpk = _find_vpk()
    out = platform_spec.VELOPACK_OUT
    out.mkdir(parents=True, exist_ok=True)

    # 서명 요청 시 vpk 가 signtool 을 PATH 에서 찾도록 signtool 디렉터리를 앞에 붙인다.
    # signtool 은 Windows 전용이라 macOS 에서는 아예 조회하지 않는다.
    env = dict(os.environ)
    sign_params = velopack_sign_params() if spec.target == "windows" else None
    if sign_params:
        signtool = find_signtool()
        if signtool is None:
            fail("서명이 요청됐지만 signtool.exe 를 찾지 못했습니다. Windows SDK 를 설치하세요.")
        env["PATH"] = str(signtool.parent) + os.pathsep + env.get("PATH", "")

    # 1) 기존 릴리스를 받아 델타 기준으로 삼는다. 첫 릴리스/네트워크 실패면 건너뛴다.
    info("기존 Velopack 릴리스 조회(델타 기준)…")
    dl = subprocess.run(
        [
            vpk,
            "download",
            "github",
            "--repoUrl",
            platform_spec.REPO_URL,
            "--outputDir",
            str(out),
            "--channel",
            spec.channel,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
    )
    if dl.returncode != 0:
        info("  기존 릴리스 없음/조회 실패 → 전체 릴리스로 진행(델타 없음).")

    # 2) 패키징(+서명)
    cmd = [
        vpk,
        "pack",
        "--packId",
        platform_spec.PACK_ID,
        "--packVersion",
        version,
        "--packDir",
        str(pack_dir),
        "--mainExe",
        spec.main_exe,
        "--packTitle",
        platform_spec.PRODUCT,
        "--packAuthors",
        platform_spec.PACK_AUTHORS,
        "--channel",
        spec.channel,
        "--outputDir",
        str(out),
    ]
    if sign_params:
        cmd += ["--signParams", sign_params]
        info("Velopack 패키징(서명 포함)…")
    elif spec.target == "macos":
        # Apple Silicon 탈출구(기본 꺼짐). Velopack 이 .app 안에 UpdateMac 과 sq.version
        # 심링크를 넣어 번들 seal 이 깨지면 첫 실행이 '손상되었습니다' 로 죽을 수 있다.
        # 그때만 켠다(codesign -s - 로 ad-hoc 재서명되지만 --options runtime 이 함께 붙는
        # 부작용은 미검증). --signAppIdentity 를 생략하면 Velopack 은 codesign 을 아예
        # 돌리지 않아 재봉인도 하지 않는다.
        if os.environ.get("YKE_MACOS_ADHOC_SIGN") == "1":
            cmd += ["--signAppIdentity", "-"]
            info("Velopack 패키징(macOS ad-hoc 재서명 — YKE_MACOS_ADHOC_SIGN=1)…")
        else:
            info("Velopack 패키징(미서명 — macOS 는 이번 범위에서 코드 서명/공증 제외)…")
    else:
        info("Velopack 패키징(미서명 — YKE_SIGN_THUMBPRINT/PFX 미설정)…")
    check(cmd, env=env)
    return out


def verify_velopack_output(out: Path, spec: platform_spec.PlatformSpec, version: str) -> None:
    """vpk 가 **기대한 이름 그대로** 산출물을 냈는지 확인한다.

    이 저장소의 macOS 파일명 규칙(-Setup.pkg / releases.osx.json / *-osx-full.nupkg)은
    Velopack 소스를 읽고 도출한 것이지 실측이 아니다. 이름이 조금이라도 다르면 빌드는
    성공한 것처럼 보이는데 deploy.py 가 파일을 못 찾거나(업로드 누락) 앱이 피드를 못 읽어
    "자동 업데이트만 조용히 안 되는" 상태가 된다. 그래서 여기서 끊고, 실패 시 실제 파일
    목록을 그대로 출력해 규칙을 눈으로 고칠 수 있게 한다.

    델타(*-delta.nupkg)는 첫 릴리스에 없으므로 검사하지 않는다.
    """

    def listing() -> str:
        names = sorted(p.name for p in out.glob("*"))
        return "\n  ".join(names) if names else "(비어 있음)"

    if not list(out.glob(spec.setup_glob)):
        fail(
            f"Velopack 설치기({spec.setup_glob})를 찾지 못했습니다.\n"
            f"{out} 실제 내용:\n  {listing()}"
        )
    if not (out / spec.releases_json).is_file():
        fail(
            f"업데이트 피드 {spec.releases_json} 이 없습니다(채널={spec.channel}).\n"
            f"{out} 실제 내용:\n  {listing()}"
        )
    full_glob = spec.nupkg_globs(version)[0]
    if not list(out.glob(full_glob)):
        fail(
            f"전체 업데이트 패키지({full_glob})를 찾지 못했습니다.\n{out} 실제 내용:\n  {listing()}"
        )
    info(f"Velopack 산출물 검증 통과(채널={spec.channel}, 버전={version}).")


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
        help="(Windows 전용) 설치기 빌드에 더해 cuBLAS 런타임 에셋 zip 도 만든다"
        "(gpu-runtime-cu12 릴리스에 올려 온디맨드로 배포).",
    )
    args = parser.parse_args()

    require_uv()
    target = _target()

    # build_gpu_runtime_asset() 은 main() 의 맨 마지막에 돈다. 여기서 끊지 않으면 macOS 에서
    # flet build(수 분) + vpk pack 까지 다 성공한 뒤 마지막에 uv pip install 이 실패해
    # (nvidia-cublas-cu12 는 macOS 휠이 없다) 설치기는 멀쩡한데 종료 코드만 1 이 된다.
    if args.gpu_runtime and target != "windows":
        fail(
            "--gpu-runtime 은 Windows 전용입니다 — cuBLAS(nvidia-cublas-cu12)는 "
            f"Windows DLL 이라 {target} 용 휠이 없습니다."
        )

    if target == "windows":
        ensure_windows_toolchain()
    elif target == "macos":
        ensure_macos_toolchain()

    # pyproject.toml(SSOT)의 버전을 src/yke/__init__.py 에 반영한 뒤(flet build 가 이 파일을
    # 그대로 복사해 번들에 담으므로 빌드 전에 최신 상태여야 한다) 빌드에 쓸 버전으로 쓴다.
    version = sync_version()

    # flet build 의 rich 진행표시가 이모지를 stdout 에 쓰는데 한국어 Windows 콘솔 기본
    # 코덱(cp949)으로는 인코딩 불가 → UnicodeEncodeError 로 죽는다. 자식 Python 을 UTF-8
    # 모드로 강제해 회피한다(다른 OS 엔 무해).
    build_env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    if target == "windows":
        # 번들에 x64 VC 런타임이 들어가도록 WINDIR 만 shim 으로 바꾼다(이유: prepare_vcruntime_shim).
        shim = prepare_vcruntime_shim()
        reset_cmake_cache_if_stale(shim)
        build_env["WINDIR"] = str(shim)

    info("의존성 동기화 (uv sync)")
    check(["uv", "sync"])

    build_cmd = [
        "uv",
        "run",
        "--no-sync",
        "flet",
        "build",
        target,
        "--product",
        platform_spec.PRODUCT,
        "--org",
        _ORG,
    ]
    if target in ("windows", "macos"):
        # 네이티브 러너(Windows/macOS) 진입점을 패치한 템플릿으로 빌드한다 — Velopack 설치
        # 훅 처리(Windows 만 해당)와 첫 창 크기(시작 시 크기가 바뀌는 깜빡임 제거, 양쪽 공통).
        # macOS 는 설치기가 인자 없이 앱을 띄우므로 훅 패치가 필요 없어 창 크기만 해당한다.
        # 자세한 이유는 flet_template 참고.
        build_cmd += ["--template", str(flet_template.prepare(flet_version()))]
    if target == "macos":
        # Apple Silicon 전용으로 빌드한다. flet 은 기본적으로 arm64 와 x86_64 site-packages 를
        # 각각 만들어 universal 앱을 내는데, x86_64 쪽이 이 앱의 의존성으로는 성립하지 않는다:
        # google-genai → google-auth → cryptography 이고, cryptography 는 49.0.0 부터 macOS
        # 휠을 **arm64 만** 낸다(48.0.0 까지는 universal2 가 있었다. PyPI 실측). 그러면 pip 가
        # sdist 로 떨어져 Rust(maturin)로 소스 빌드를 시도하다가, 빌드 환경에 깔린 x86_64
        # _cffi_backend 를 arm64 프로세스가 dlopen 하면서 죽는다("mach-o file, but is an
        # incompatible architecture"). arm64 레그는 그 전에 이미 성공하므로, x86_64 를 빼는
        # 것만으로 빌드가 통과하고 시간도 절반이 된다.
        # Intel Mac 을 지원하려면 cryptography<49 로 되돌려야 하는데, 보안 라이브러리를
        # 뒤로 묶는 대가가 커서 Apple Silicon 전용으로 간다(README 에 명시).
        build_cmd += ["--arch", "arm64"]

    info(f"flet build {target} (base)")
    check(build_cmd, env=build_env)
    dst = stash_output(target, "base")
    verify_artifact(dst, target)
    # 앱 exe 서명(YKE_SIGN_THUMBPRINT/YKE_SIGN_PFX 설정 시). 인증서 미지정이면 미서명.
    if target == "windows":
        verify_vc_runtime_arch(dst)  # 서명·패키징 전에 잡아야 한다.
        maybe_sign_bundle(dst)

    # Velopack 설치기(Windows/macOS). linux 만 폴더 zip 으로 폴백.
    if target in ("windows", "macos"):
        spec = platform_spec.spec_for(target)
        # macOS 는 완성된 .app 자체를 넘긴다(상위 폴더를 넘기면 Velopack 이 번들을 새로
        # 만들려 하며 .icns 를 요구한다 — macos_app_bundle 주석 참고).
        pack_dir = dst if target == "windows" else macos_app_bundle(dst)
        if target == "macos":
            # 번들 밖(빌드 머신의 pub 캐시)을 가리키는 링크를 먼저 걷어낸다. 두면 vpk 가
            # 순환 링크를 따라가다 PathTooLongException 으로 죽는다(prune_external_symlinks 참고).
            for link in prune_external_symlinks(pack_dir):
                info(f"번들 밖을 가리키는 링크 제거: {link.relative_to(pack_dir)}")
        out = velopack_pack(pack_dir, version, spec)
        verify_velopack_output(out, spec, version)
        sep = "\\" if target == "windows" else "/"
        info(f"Velopack 산출물: {out}{sep}")
        # 자동업데이트·설치에 필요한 것만 올린다: 설치기 + *.nupkg(full/delta 업데이트
        # 페이로드) + releases.<channel>.json(피드). Portable.zip(대용량)·RELEASES(레거시)·
        # assets.<channel>.json(로컬 인덱스)은 GithubSource 가 쓰지 않으므로 올리지 않는다.
        # 델타 글롭은 **실제로 델타가 생겼을 때만** 안내에 넣는다. 델타는 직전 릴리스가
        # 있을 때만 만들어지는데(첫 릴리스엔 없다), 없는 글롭을 그대로 복붙하면 PowerShell 은
        # 확장 실패로 "Cannot find path" 를 내고 명령 전체가 실행되지 않는다.
        full_glob, delta_glob = spec.nupkg_globs(version)
        globs = [spec.setup_glob, full_glob]
        if list(out.glob(delta_glob)):
            globs.append(delta_glob)
        globs.append(spec.releases_json)
        assets = " ".join(f"{out}{sep}{g}" for g in globs)
        info("업로드는 python scripts/deploy.py 가 대신 합니다(버전·커밋 검사 포함).")
        info(f"  수동으로 한다면: gh release create v{version} {assets} --title v{version}")
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
