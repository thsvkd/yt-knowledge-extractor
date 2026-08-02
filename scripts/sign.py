#!/usr/bin/env python3
"""배포 번들 코드 서명(Windows Authenticode + macOS codesign 인자 조립).

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

**signtool 경로는 Windows 전용**이다(:func:`maybe_sign_bundle`, :func:`sign_file`).
macOS 는 signtool 을 쓰지 않고 vpk 가 pack 단계에서 codesign 을 부르므로, 이 모듈은 그
**인자만** 만들어 준다(:func:`velopack_sign_args_macos`). 두 OS 의 서명 지정 방식이 달라서
그렇다 — Windows 는 signtool 인자 **문자열 하나**(``--signParams``)를, macOS 는
``--signAppIdentity``/``--signInstallIdentity``/``--notaryProfile`` 같은 **개별 인자**를 받는다.

macOS 서명 환경 변수(전부 선택. 미설정이면 ad-hoc 재서명만 하고 공증은 하지 않는다):
  YKE_SIGN_APP_IDENTITY      .app 서명 신원("Developer ID Application: …"). 미설정 시 ``-``
                             (ad-hoc)이 기본으로 들어간다 — 아래 velopack_sign_args_macos 참고.
  YKE_SIGN_INSTALL_IDENTITY  .pkg 설치기 서명 신원("Developer ID Installer: …").
  YKE_SIGN_NOTARY_PROFILE    notarytool 키체인 프로파일 이름(공증까지 할 때).

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
# **Windows 전용 경로**다 — macOS 번들은 .app 디렉터리라 이 이름으로 찾지 않으며,
# 애초에 macOS 서명은 이번 범위 밖이다(모듈 docstring 참고).
_APP_EXE = "yt-knowledge-extractor.exe"
_DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"


def _env(name: str) -> str:
    """환경 변수 값(앞뒤 공백 제거). 미설정이면 빈 문자열."""
    return os.environ.get(name, "").strip()


def mask_sign_params(params: str) -> str:
    """서명 인자 문자열에서 .pfx 비밀번호(``/p <값>``)만 가린다.

    나머지 인자(인증서 경로·타임스탬프 URL)는 실패 원인을 짚는 데 필요하므로 그대로 둔다.
    로그·오류 메시지에 이 문자열을 넣기 전에 반드시 통과시킨다.
    """
    masked: list[str] = []
    hide_next = False
    for token in params.split(" "):
        if hide_next:
            masked.append("***")
            hide_next = False
            continue
        masked.append(token)
        hide_next = token == "/p"
    return " ".join(masked)


def velopack_sign_args_macos() -> list[str]:
    """macOS ``vpk pack`` 에 넘길 서명 인자. 설정된 항목만 담아 돌려준다.

    세 항목은 서로 독립이다 — ad-hoc 재서명처럼 앱 신원만 주는 경우도 그대로 성립해야 하므로
    있는 것만 넣는다.

    ``--signAppIdentity`` 를 **비워 두면 안 된다**. 생략하면 Velopack 은 codesign 을 아예
    돌리지 않는데, vpk 는 pack 중에 ``UpdateMac`` 과 ``sq.version`` 을 ``Contents/MacOS`` 에
    끼워 넣으므로 우리가 pack 전에 재서명해 두어도(build.resign_adhoc) **그 시점에 앱 봉인이
    다시 깨진다**. 그래서 기본값을 ``-``(ad-hoc)으로 두어 vpk 자신이 마지막에 다시 봉인하게
    한다. 이건 Developer ID 서명이 아니라 재봉인이므로 공증되지 않는다는 사실은 그대로다.
    """
    args: list[str] = []
    for flag, name in (
        ("--signAppIdentity", "YKE_SIGN_APP_IDENTITY"),
        ("--signInstallIdentity", "YKE_SIGN_INSTALL_IDENTITY"),
        ("--notaryProfile", "YKE_SIGN_NOTARY_PROFILE"),
    ):
        value = _env(name)
        if value:
            args += [flag, value]
    if "--signAppIdentity" not in args:
        args = ["--signAppIdentity", "-", *args]
    return args


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


def velopack_sign_params() -> str | None:
    """Velopack ``vpk pack --signParams`` 로 넘길 signtool 인자 문자열.

    vpk 는 서명 대상 파일마다 ``signtool sign <signParams> <file>`` 을 호출하므로, 인증서
    지정 인자(:func:`_sign_args`)에 해시/타임스탬프 옵션을 붙인 문자열을 돌려준다. 인증서
    미지정(YKE_SIGN_THUMBPRINT/PFX 없음)이면 None(=미서명). 비밀번호(/p)가 들어갈 수 있으니
    호출 측은 이 문자열을 로그에 남기지 않는다.
    """
    # signtool 은 Windows 전용이다. 개발자 환경에 YKE_SIGN_THUMBPRINT 가 남아 있으면
    # macOS 빌드가 velopack_pack 안에서 signtool 을 못 찾아 죽는다(실제로 걸리는 함정).
    if sys.platform != "win32":
        return None
    cert_args = _sign_args()
    if cert_args is None:
        return None
    timestamp_url = os.environ.get("YKE_SIGN_TIMESTAMP_URL", _DEFAULT_TIMESTAMP)
    return " ".join([*cert_args, "/fd", "SHA256", "/tr", timestamp_url, "/td", "SHA256"])


def sign_file(signtool: Path, target: Path, cert_args: list[str]) -> None:
    """signtool 로 파일 하나를 SHA256 + RFC3161 타임스탬프로 서명한다(실패 시 종료)."""
    timestamp_url = os.environ.get("YKE_SIGN_TIMESTAMP_URL", _DEFAULT_TIMESTAMP)
    cmd = [
        str(signtool),
        "sign",
        *cert_args,
        "/fd",
        "SHA256",
        "/tr",
        timestamp_url,
        "/td",
        "SHA256",
        str(target),
    ]
    # 비밀번호(/p) 는 로그에 남기지 않는다.
    shown = mask_sign_params(" ".join(cmd))
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
    # signtool 은 Windows 전용이다. 개발자 환경에 YKE_SIGN_THUMBPRINT 가 남아 있으면
    # macOS 빌드가 signtool 을 못 찾아 죽는다(실제로 걸리는 함정). macOS 서명은 범위 밖.
    # 조용히 False 를 돌려주면 "서명됐다"고 오해한 채 배포하게 되므로 이유를 반드시 남긴다.
    if sys.platform != "win32":
        info(
            f"코드 서명 건너뜀 ({sys.platform} — 이 스크립트는 Windows 전용입니다. "
            "macOS 는 미서명 배포이며 README 의 Gatekeeper 우회 안내로 대응합니다)."
        )
        return False
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
    # 직접 실행 경로는 여기서 끊는다. 예전에는 디렉터리 인자일 때만 maybe_sign_bundle 이
    # 조용히 False 를 돌려주고 _main 이 그대로 0 을 반환해, macOS 에서 "출력 0줄 + 종료 0"
    # 이라는 성공처럼 보이는 무동작이 됐다(서명됐다고 믿고 배포하게 된다).
    if sys.platform != "win32":
        fail(
            f"이 스크립트는 Windows 전용입니다(현재 {sys.platform}). "
            "macOS 코드 서명/공증은 이번 범위 밖이며, 미서명 배포 + README 의 "
            "Gatekeeper 우회 안내로 대응합니다."
        )
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
