"""LLM 자격증명(예: Gemini API 키) 보관.

키는 평문 설정 파일이 아니라 OS 자격증명 저장소(``keyring`` → Windows 자격증명 관리자)에
저장한다. 개발/CI 편의를 위해 환경변수(``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``)가 있으면
그것을 우선한다(google-genai SDK 의 관례와 동일).

``keyring`` 은 지연 import 한다 — 백엔드가 없거나(헤드리스 CI 등) import 가 실패해도
앱 전체가 죽지 않고 '키 없음'으로 안전하게 폴백한다.
"""

from __future__ import annotations

import os
import sys

# keyring 저장소 식별자. (service, account) 쌍으로 항목을 구분한다.
# **이 두 값은 Windows 제거 훅이 지울 항목 이름의 SSOT이기도 하다** —
# scripts/flet_template.py 의 credential_targets() 가 이 파일에서 읽어 간다.
_SERVICE = "yt-knowledge-extractor"
_GEMINI_ACCOUNT = "gemini_api_key"

# Windows 자격 증명 관리자의 blob 한도는 2560바이트이고 UTF-16으로 저장되므로 실질 절반이다.
# 넘으면 **조용히 실패**해 "저장했다는데 다시 열면 없는" 증상이 된다. Gemini API 키는 40자
# 안팎이라 실제로 걸릴 일은 없지만, 검사가 한 줄이라 넣어 둔다(넘으면 명확히 실패시킨다).
_WINDOWS_CRED_MAX_BYTES = 2560

_backend_pinned = False


def _pin_backend(keyring) -> None:
    """플랫폼 백엔드를 **명시 지정**한다(프로세스당 한 번).

    keyring 의 엔트리포인트 자동 탐색에 맡기지 않는 이유: 번들에 dist-info 가 없으면 탐색이
    조용히 빈 백엔드를 골라, 개발 환경에서는 통과하고 배포본에서만 죽는다.

    실측으로는 우리 번들(flet/serious_python)이 서드파티 dist-info 를 보존해서 자동 탐색도
    macOS.Keyring 을 제대로 고른다(PyInstaller 처럼 dist-info 를 벗기는 번들러와 다르다).
    그래도 번들러의 동작에 기대는 대신 못박아 둔다 — 번들 방식이 바뀌면 조용히 깨지는 종류의
    의존이라 사후에 알아채기 어렵다.

    지정에 실패하면 자동 탐색에 맡긴다(리눅스·헤드리스 등). 지금까지의 동작 그대로라
    회귀가 아니다.
    """
    global _backend_pinned
    if _backend_pinned:
        return
    _backend_pinned = True
    try:
        if sys.platform == "darwin":
            from keyring.backends import macOS

            keyring.set_keyring(macOS.Keyring())
        elif sys.platform == "win32":
            from keyring.backends import Windows

            keyring.set_keyring(Windows.WinVaultKeyring())
    except Exception:  # noqa: BLE001 - 지정 실패는 자동 탐색으로 폴백한다.
        pass


def _env_gemini_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return key.strip() if key and key.strip() else None


def get_gemini_api_key() -> str | None:
    """저장된 Gemini API 키를 반환한다(환경변수 > keyring). 없으면 ``None``."""
    env = _env_gemini_key()
    if env:
        return env
    try:
        import keyring

        _pin_backend(keyring)
        val = keyring.get_password(_SERVICE, _GEMINI_ACCOUNT)
    except Exception:
        return None
    return val.strip() if val and val.strip() else None


def set_gemini_api_key(key: str) -> None:
    """Gemini API 키를 OS 자격증명 저장소에 저장한다.

    Raises:
        ValueError: 빈 키.
        RuntimeError: keyring 백엔드 사용 불가/저장 실패.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("빈 API 키는 저장할 수 없습니다.")
    # 저장 전에 한도를 넘는지 본다. 넘으면 OS 가 조용히 실패시키므로 여기서 명확히 끊는다.
    if sys.platform == "win32":
        size = len(key.encode("utf-16-le"))
        if size > _WINDOWS_CRED_MAX_BYTES:
            raise ValueError(
                f"API 키가 너무 깁니다({size}바이트). Windows 자격 증명 관리자 한도는 "
                f"{_WINDOWS_CRED_MAX_BYTES}바이트입니다."
            )
    try:
        import keyring

        _pin_backend(keyring)
        keyring.set_password(_SERVICE, _GEMINI_ACCOUNT, key)
    except Exception as exc:  # keyring 백엔드 없음/권한 문제 등
        raise RuntimeError(f"API 키 저장 실패(keyring): {type(exc).__name__}: {exc}") from exc


def delete_gemini_api_key() -> None:
    """저장된 Gemini API 키를 삭제한다(없거나 실패해도 조용히 무시)."""
    try:
        import keyring

        _pin_backend(keyring)
        keyring.delete_password(_SERVICE, _GEMINI_ACCOUNT)
    except Exception:
        pass


def has_gemini_api_key() -> bool:
    """사용 가능한 Gemini API 키(환경변수 또는 keyring)가 있는지."""
    return bool(get_gemini_api_key())
