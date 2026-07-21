"""LLM 프로바이더 추상화.

파이프라인의 LLM 단계(자막 보정·지식 추출·통합)는 어떤 프로바이더를 쓰든 동일한
``complete(system, user, *, model)`` 인터페이스만 사용한다. 실제 구현은 프로바이더별
모듈(:mod:`.claude_client` / :mod:`.gemini_client`)에 두고, :func:`make_client` 가
설정(:class:`~yke.config.LLMConfig`)의 ``provider`` 에 따라 알맞은 것을 고른다.

프로바이더별 무거운 SDK(예: ``google-genai``)는 해당 프로바이더를 실제로 쓸 때만
지연 import 한다 — Claude 만 쓰는 사용자는 Gemini SDK 를 불러오지 않는다.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """모든 LLM 프로바이더가 만족해야 하는 최소 인터페이스."""

    def complete(self, system: str, user: str, *, model: str) -> str:
        """단일 턴 completion — 시스템/유저 프롬프트로 응답 텍스트를 반환한다."""
        ...


def make_client(llm_cfg) -> LLMClient:
    """설정에 맞는 LLM 클라이언트를 생성한다.

    Args:
        llm_cfg: :class:`~yke.config.LLMConfig` (``provider`` 필드 사용).

    Raises:
        RuntimeError: 알 수 없는 프로바이더거나, 선택한 프로바이더의 준비(설치/인증)가
            안 됐을 때(각 클라이언트 생성자가 던진다).
    """
    provider = (getattr(llm_cfg, "provider", None) or "claude").strip().lower()
    if provider == "gemini":
        from .gemini_client import GeminiClient

        return GeminiClient()
    if provider == "claude":
        from .claude_client import ClaudeClient

        return ClaudeClient()
    raise RuntimeError(f"알 수 없는 LLM 프로바이더: {provider!r} (claude | gemini)")


def provider_available(provider: str) -> bool:
    """해당 프로바이더를 지금 쓸 수 있는지(설치/인증) — GUI 자격증명 상태 표시용.

    호출만으로 무거운 SDK 를 강제로 끌어오지 않도록, 각 모듈의 가벼운 ``is_available``
    검사만 사용한다.
    """
    provider = (provider or "claude").strip().lower()
    if provider == "gemini":
        from .gemini_client import is_available as _gemini_available

        return _gemini_available()
    if provider == "claude":
        from .claude_client import is_available as _claude_available

        return _claude_available()
    return False
