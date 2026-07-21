"""LLM 자격증명(예: Gemini API 키) 보관.

키는 평문 설정 파일이 아니라 OS 자격증명 저장소(``keyring`` → Windows 자격증명 관리자)에
저장한다. 개발/CI 편의를 위해 환경변수(``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``)가 있으면
그것을 우선한다(google-genai SDK 의 관례와 동일).

``keyring`` 은 지연 import 한다 — 백엔드가 없거나(헤드리스 CI 등) import 가 실패해도
앱 전체가 죽지 않고 '키 없음'으로 안전하게 폴백한다.
"""

from __future__ import annotations

import os

# keyring 저장소 식별자. (service, account) 쌍으로 항목을 구분한다.
_SERVICE = "yt-knowledge-extractor"
_GEMINI_ACCOUNT = "gemini_api_key"


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
    try:
        import keyring

        keyring.set_password(_SERVICE, _GEMINI_ACCOUNT, key)
    except Exception as exc:  # keyring 백엔드 없음/권한 문제 등
        raise RuntimeError(f"API 키 저장 실패(keyring): {type(exc).__name__}: {exc}") from exc


def delete_gemini_api_key() -> None:
    """저장된 Gemini API 키를 삭제한다(없거나 실패해도 조용히 무시)."""
    try:
        import keyring

        keyring.delete_password(_SERVICE, _GEMINI_ACCOUNT)
    except Exception:
        pass


def has_gemini_api_key() -> bool:
    """사용 가능한 Gemini API 키(환경변수 또는 keyring)가 있는지."""
    return bool(get_gemini_api_key())
