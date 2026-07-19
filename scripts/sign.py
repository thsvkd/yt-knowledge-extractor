#!/usr/bin/env python3
"""배포 번들 코드 서명(Windows Authenticode).

self-signed 인증서로도, 정식 CA 인증서로도 동작한다. 서명 자체는 인증서 종류와 무관하게
붙지만, **신뢰**는 다르다:

- self-signed: 그 인증서를 "신뢰할 수 있는 루트 인증 기관" + "신뢰할 수 있는 게시자"에
  설치한 PC 에서만 유효하게 보인다. SmartScreen 경고는 사라지지 않는다(평판 없음).
  본인 PC·인증서를 배포한 소수에게 "알 수 없는 게시자" 대신 게시자 이름을 보이는 용도.
- 정식 CA(OV/EV): 어느 PC 에서나 게시자 이름이 신뢰되고, EV/평판 축적으로 SmartScreen 도 통과.

어느 인증서로 서명할지는 환경 변수로 고른다(둘 다 없으면 서명을 건너뛰고 미서명 배포):
  YKE_SIGN_THUMBPRINT    인증서 저장소(CurrentUser\\My)의 인증서 지문(SHA1). 비밀번호 노출이
                         없어 self-signed 개인 서명에 권장. make_selfsigned_cert.ps1 이 출력.
  YKE_SIGN_PFX           서명 인증서 .pfx 경로(위와 택일). CI 등 저장소를 못 쓰는 환경용.
  YKE_SIGN_PFX_PASSWORD  .pfx 비밀번호(있으면).
  YKE_SIGN_TIMESTAMP_URL RFC3161 타임스탬프 서버(기본 digicert). 인증서 만료 후에도 서명 유지.

사용:
  python scripts/sign.py <배포폴더|exe경로>   # 직접 서명(build.py 없이 재서명할 때)
  build.py 가 빌드 직후 자동 호출(maybe_sign_bundle).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _common import REPO_ROOT, fail, info

# 서명 대상(번들 루트의 앱 실행 파일). 게시자 표시·실행 신뢰는 이 exe 의 서명을 본다.
_APP_EXE = "yt-knowledge-extractor.exe"
_DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"


def find_signtool() -> Path | None:
    """Windows SDK(Windows Kits 10)에서 최신 x64 signtool.exe 를 찾는다."""
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    base = Path(program_files_x86) / "Windows Kits" / "10" / "bin"
    if not base.exists():
        return None
    # bin/<sdk_ver>/x64/signtool.exe — 버전 내림차순으로 최신 우선.
    candidates = sorted(base.glob("*/x64/signtool.exe"), reverse=True)
    return candidates[0] if candidates else None


def _sign_args() -> list[str] | None:
    """환경 변수에서 인증서 지정 인자를 만든다. 둘 다 없으면 None(=서명 스킵)."""
    thumbprint = os.environ.get("YKE_SIGN_THUMBPRINT", "").strip()
    pfx = os.environ.get("YKE_SIGN_PFX", "").strip()
    if thumbprint:
        # 저장소 인증서. 비밀번호가 명령줄에 노출되지 않는다.
        return ["/sha1", thumbprint]
    if pfx:
        args = ["/f", pfx]
        password = os.environ.get("YKE_SIGN_PFX_PASSWORD", "")
        if password:
            args += ["/p", password]
        return args
    return None


def sign_file(signtool: Path, target: Path, cert_args: list[str]) -> None:
    """signtool 로 파일 하나를 SHA256 + RFC3161 타임스탬프로 서명한다(실패 시 종료)."""
    timestamp_url = os.environ.get("YKE_SIGN_TIMESTAMP_URL", _DEFAULT_TIMESTAMP)
    cmd = [
        str(signtool), "sign",
        *cert_args,
        "/fd", "SHA256",
        "/tr", timestamp_url,
        "/td", "SHA256",
        str(target),
    ]
    # 비밀번호(/p) 는 로그에 남기지 않는다.
    shown = " ".join(("***" if prev == "/p" else a) for prev, a in zip([""] + cmd, cmd))
    info(f"서명: {target.name}")
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        out = (result.stdout or "") + (result.stderr or "")
        fail(f"서명 실패(exit {result.returncode}): {shown}\n{out.strip()}")


def maybe_sign_bundle(dst: Path) -> bool:
    """배포 폴더의 앱 exe 를 서명한다. 인증서 미지정이면 건너뛴다.

    Returns:
        서명했으면 True, 인증서 미지정으로 건너뛰면 False.
    """
    cert_args = _sign_args()
    if cert_args is None:
        info("코드 서명 건너뜀 (YKE_SIGN_THUMBPRINT/YKE_SIGN_PFX 미설정 → 미서명 배포).")
        return False
    signtool = find_signtool()
    if signtool is None:
        fail("signtool.exe 를 찾지 못했습니다. Windows SDK(서명 도구)를 설치하세요.")
    exe = dst / _APP_EXE
    if not exe.exists():
        fail(f"서명 대상 앱 실행 파일이 없습니다: {exe}")
    sign_file(signtool, exe, cert_args)
    info(f"서명 완료: {exe}")
    return True


def _main(argv: list[str]) -> int:
    if len(argv) != 1:
        fail("사용법: python scripts/sign.py <배포폴더|exe경로>")
    target = Path(argv[0]).resolve()
    if target.is_dir():
        maybe_sign_bundle(target)
    elif target.is_file():
        cert_args = _sign_args()
        if cert_args is None:
            fail("서명할 인증서를 지정하세요(YKE_SIGN_THUMBPRINT 또는 YKE_SIGN_PFX).")
        signtool = find_signtool()
        if signtool is None:
            fail("signtool.exe 를 찾지 못했습니다. Windows SDK 를 설치하세요.")
        sign_file(signtool, target, cert_args)
    else:
        fail(f"경로를 찾을 수 없습니다: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
