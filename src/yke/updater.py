"""GitHub Releases 기반 자체 업데이트(커스텀 사이드카 패턴).

흐름: 최신 릴리스 감지 → 에셋(zip) 다운로드 → SHA256 검증(GitHub digest 대조) →
staging 에 압축 해제 → 사이드카 스크립트로 실행 중 폴더 교체 후 재실행.

Windows 에서 실행 중인 exe/DLL 은 잠기므로 in-place 덮어쓰기가 안 된다. 그래서 번들 밖
(temp)의 사이드카 프로세스가 "앱 종료 대기 → 폴더 rename 스왑 → 재실행 → 롤백"을 수행한다.
배포 폴더명(``yke-<variant>-<target>``)이 변형·플랫폼을 인코딩하므로 런타임에 그대로 읽어
맞는 릴리스 에셋을 고른다.

주의(배포 전 설정 필요): ``REPO_OWNER``/``REPO_NAME`` 을 실제 public 레포로 지정해야 한다.
end-to-end 검증은 public 레포 + 릴리스 2개 이상이 있어야 가능하다. 순수 로직(버전 비교,
에셋 선택, SHA256, 응답 파싱)은 단위 테스트로 검증한다.

적용/재실행 경로는 **Windows 우선** 검증했다. macOS(.app 번들)·Linux 는 install_root/
app_exe 계산 규칙이 달라 현재 :func:`is_bundle` 가드에서 no-op 이 된다(향후 플랫폼별 처리 필요).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 배포 전 실제 public 레포로 바꾼다.
REPO_OWNER = "thsvkd"
REPO_NAME = "yt-knowledge-extractor"

_API_LATEST = "https://api.github.com/repos/{owner}/{repo}/releases/latest"
# GitHub 은 User-Agent 없는 요청을 403 으로 거부한다.
_USER_AGENT = "yke-updater"


@dataclass
class Release:
    """업데이트 대상 릴리스."""

    tag: str
    version: str
    asset_name: str
    asset_url: str
    sha256: str | None  # GitHub 이 노출하는 에셋 digest (없을 수도 있음)
    notes: str


# -- 버전 / 에셋 이름 --------------------------------------------------------
def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' → (1, 2, 3). 비교용 정수 튜플.

    prerelease/build 접미사(-rc1, +build)는 무시하고 숫자 코어만 본다. 그렇지 않으면
    '1.2.3-rc1' → (1,2,3,1) 이 되어 최종 '1.2.3' → (1,2,3) 보다 크게 정렬되는 버그가 난다.
    """
    core = re.split(r"[-+]", (tag or "").lstrip("vV"), maxsplit=1)[0]
    nums = re.findall(r"\d+", core)
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(candidate_tag: str, current_version: str) -> bool:
    """candidate_tag 가 current_version 보다 높은 버전인지."""
    return parse_version(candidate_tag) > parse_version(current_version)


def current_target() -> str:
    """현재 OS 의 배포 타깃 이름."""
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(
        platform.system(), "windows"
    )


def asset_name(variant: str, target: str) -> str:
    """릴리스 에셋 파일명 규칙(build.py 산출물과 동일)."""
    return f"yke-{variant}-{target}.zip"


def install_root() -> Path:
    """설치 루트(실행 파일이 있는 폴더). 개발 실행 시엔 의미가 다를 수 있다."""
    return Path(sys.executable).resolve().parent


def is_bundle(root: Path | None = None) -> bool:
    """설치 폴더명이 배포 번들 규칙(yke-<variant>-<target>)에 맞는지.

    개발 실행에서 실수로 업데이트를 적용하지 않도록 가드에 쓴다.
    """
    name = (root or install_root()).name.lower()
    return bool(re.match(r"yke-(cpu|gpu)-(windows|macos|linux)$", name))


def detect_variant_target(root: Path | None = None) -> tuple[str, str]:
    """설치 폴더명(yke-<variant>-<target>)에서 변형·타깃을 읽는다.

    폴더명이 규칙과 다르면(개발 실행 등) 변형은 'cpu', 타깃은 현재 OS 로 폴백한다.
    """
    name = (root or install_root()).name.lower()
    m = re.match(r"yke-(cpu|gpu)-(windows|macos|linux)$", name)
    if m:
        return m.group(1), m.group(2)
    return "cpu", current_target()


# -- 릴리스 조회 / 다운로드 --------------------------------------------------
def _open(url: str, timeout: int) -> "urllib.request.addinfourl":
    req = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    )
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (https only)


def parse_latest(data: dict, current_version: str, variant: str, target: str) -> Release | None:
    """GitHub /releases/latest 응답을 파싱해 업데이트가 있으면 Release 를 돌려준다.

    더 높은 버전이 아니거나 이 변형/타깃에 맞는 에셋이 없으면 None.
    """
    tag = data.get("tag_name") or ""
    if not is_newer(tag, current_version):
        return None
    want = asset_name(variant, target)
    for asset in data.get("assets", []):
        if asset.get("name") == want:
            digest = asset.get("digest") or ""  # 예: "sha256:abcd..."
            sha = digest.split(":", 1)[1] if digest.startswith("sha256:") else None
            return Release(
                tag=tag,
                version=tag.lstrip("vV"),
                asset_name=want,
                asset_url=asset["browser_download_url"],
                sha256=sha,
                notes=data.get("body") or "",
            )
    return None


def check_latest(
    current_version: str,
    variant: str,
    target: str,
    *,
    owner: str = REPO_OWNER,
    repo: str = REPO_NAME,
    timeout: int = 10,
) -> Release | None:
    """GitHub 에서 최신 릴리스를 조회한다. 네트워크 오류는 호출자가 처리한다."""
    with _open(_API_LATEST.format(owner=owner, repo=repo), timeout) as resp:
        data = json.load(resp)
    return parse_latest(data, current_version, variant, target)


def sha256_of(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download(
    release: Release,
    dest_dir: str | Path,
    *,
    progress_cb=None,
    timeout: int = 120,
) -> Path:
    """에셋 zip 을 dest_dir 에 스트리밍 저장하고 SHA256 을 검증한다.

    digest 가 있으면 불일치 시 파일을 지우고 RuntimeError. progress_cb(0.0~1.0) 로 진행률 보고.
    """
    if not release.asset_url.lower().startswith("https://"):
        raise RuntimeError(f"안전하지 않은 다운로드 URL(https 아님): {release.asset_url}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / release.asset_name
    req = urllib.request.Request(release.asset_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(out, "wb") as f:  # noqa: S310
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress_cb and total:
                progress_cb(done / total)
    if release.sha256:
        actual = sha256_of(out)
        if actual.lower() != release.sha256.lower():
            out.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA256 불일치 — 손상/변조 가능. 기대 {release.sha256[:12]}… 실제 {actual[:12]}…"
            )
    else:
        logger.warning("릴리스 에셋에 SHA256 digest 가 없어 무결성 검증을 건너뜁니다(TLS 만 신뢰).")
    return out


def extract(zip_path: str | Path, staging_dir: str | Path) -> Path:
    """zip 을 staging_dir 아래에 풀고, 새 번들 폴더(yke-<variant>-<target>) 경로를 돌려준다.

    zip 은 최상위에 그 폴더 하나를 담는다(build.py 의 make_archive base_dir).
    """
    zip_path = Path(zip_path)
    staging_dir = Path(staging_dir)
    if staging_dir.exists():
        import shutil

        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging_dir)
    subdirs = [p for p in staging_dir.iterdir() if p.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0]
    return staging_dir  # 최상위 폴더가 없으면 staging 자체가 번들


# -- 사이드카 교체 스크립트 --------------------------------------------------
_WIN_SIDECAR = r"""@echo off
setlocal
:: 메인 앱(PID {pid}) 종료 대기. ping 으로 ~1초 슬립한다(timeout 은 콘솔이 없으면
:: 즉시 끝나 바쁜 루프가 되므로 쓰지 않는다).
:waitloop
tasklist /fi "PID eq {pid}" 2>nul | find "{pid}" >nul
if not errorlevel 1 (
  ping -n 2 127.0.0.1 >nul
  goto waitloop
)
:: 폴더 스왑(같은 볼륨이면 원자적 rename). 실패 시 부분 install 을 지운 뒤 백업을 되돌린다
:: (지우지 않으면 move 가 백업을 install 안으로 넣어 버려 복구가 깨진다).
move "{install}" "{backup}" >nul || goto fail
move "{new}" "{install}" >nul || (rmdir /s /q "{install}" 2>nul & move "{backup}" "{install}" >nul & goto fail)
:: 재실행 후 백업·자기 자신 정리
start "" "{install}\{app_exe}"
rmdir /s /q "{backup}" 2>nul
del "%~f0"
exit /b 0
:fail
start "" "{install}\{app_exe}"
del "%~f0"
exit /b 1
"""

_POSIX_SIDECAR = r"""#!/bin/sh
# 메인 앱(PID {pid}) 종료 대기
while kill -0 {pid} 2>/dev/null; do sleep 1; done
if mv "{install}" "{backup}" && mv "{new}" "{install}"; then
  rm -rf "{backup}"
else
  # 부분 install 을 지운 뒤 백업을 되돌린다(지우지 않으면 백업이 install 안으로 들어간다).
  rm -rf "{install}"
  [ -d "{backup}" ] && mv "{backup}" "{install}"
fi
"{install}/{app_exe}" &
rm -- "$0"
"""


def _sidecar_path(temp_dir: Path) -> Path:
    return temp_dir / ("yke_update.bat" if sys.platform == "win32" else "yke_update.sh")


def write_sidecar(
    temp_dir: str | Path,
    *,
    pid: int,
    install_dir: str | Path,
    new_bundle_dir: str | Path,
    app_exe: str,
) -> Path:
    """번들 밖(temp)에 교체 스크립트를 쓴다(번들 안에 두면 그것도 잠긴다)."""
    temp_dir = Path(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    install_dir = Path(install_dir)
    template = _WIN_SIDECAR if sys.platform == "win32" else _POSIX_SIDECAR
    script = template.format(
        pid=pid,
        install=str(install_dir),
        backup=str(install_dir.parent / (install_dir.name + "_backup")),
        new=str(new_bundle_dir),
        app_exe=app_exe,
    )
    path = _sidecar_path(temp_dir)
    path.write_text(script, encoding="utf-8")
    if sys.platform != "win32":
        path.chmod(0o755)
    return path


def apply_and_restart(new_bundle_dir: str | Path, *, app_exe: str, install_dir: Path | None = None) -> None:
    """사이드카를 temp 에 만들어 detached 로 실행하고 현재 프로세스를 종료한다.

    install_dir 미지정 시 :func:`install_root` 를 쓴다. 호출 즉시 앱이 종료되므로
    호출 전 사용자에게 재시작 안내를 마쳐야 한다.
    """
    import tempfile

    install_dir = Path(install_dir) if install_dir else install_root()
    temp_dir = Path(tempfile.gettempdir()) / "yke_update"
    script = write_sidecar(
        temp_dir,
        pid=os.getpid(),
        install_dir=install_dir,
        new_bundle_dir=new_bundle_dir,
        app_exe=app_exe,
    )
    if sys.platform == "win32":
        # CREATE_NO_WINDOW(0x08000000): 숨은 콘솔이 있어 사이드카의 대기 타이머가 동작하고
        # (DETACHED_PROCESS 는 콘솔이 없어 timeout/일부 명령이 즉시 끝나 버린다) 부모가
        # 종료돼도 산다. CREATE_NEW_PROCESS_GROUP(0x200): Ctrl+C 등에서 독립.
        subprocess.Popen(  # noqa: S603
            ["cmd", "/c", str(script)],
            creationflags=0x08000000 | 0x00000200,
            close_fds=True,
        )
    else:
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, close_fds=True)  # noqa: S603
    # run_thread 워커 스레드에서 호출되므로 sys.exit(SystemExit)는 executor 가 삼켜 프로세스가
    # 죽지 않는다(→ 사이드카가 죽지 않는 PID 를 영원히 기다린다). os._exit 로 즉시 하드 종료해
    # 파일 잠금을 풀어야 사이드카가 폴더를 교체할 수 있다.
    os._exit(0)
