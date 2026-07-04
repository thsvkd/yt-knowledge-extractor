"""Claude(Anthropic) 클라이언트 래퍼.

인증 우선순위:
  1) CLAUDE_CODE_OAUTH_TOKEN  (Claude Code 구독 토큰, `claude setup-token`)
  2) ANTHROPIC_AUTH_TOKEN     (Bearer 토큰)
  3) ANTHROPIC_API_KEY        (일반 API 키)

OAuth(Claude Code) 토큰을 쓸 때는 Bearer 인증 + `anthropic-beta: oauth-2025-04-20`
헤더가 필요하고, 시스템 프롬프트 첫 블록이 Claude Code 정체성 문자열이어야 한다.
"""

from __future__ import annotations

import os

import anthropic

# OAuth(Claude Code) 토큰 사용 시 요구되는 첫 시스템 블록.
_CLAUDE_CODE_IDENTITY = "You are Claude Code, Anthropic's official CLI for Claude."
_OAUTH_BETA_HEADER = "oauth-2025-04-20"


def _build_client() -> tuple[anthropic.Anthropic, bool]:
    """(client, is_oauth) 반환. 자격증명이 없으면 예외."""
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if oauth:
        client = anthropic.Anthropic(
            auth_token=oauth,
            default_headers={"anthropic-beta": _OAUTH_BETA_HEADER},
        )
        return client, True
    if api_key:
        return anthropic.Anthropic(api_key=api_key), False

    raise RuntimeError(
        "LLM 자격증명이 없습니다. .env 에 CLAUDE_CODE_OAUTH_TOKEN 또는 "
        "ANTHROPIC_API_KEY 를 설정하세요."
    )


class ClaudeClient:
    def __init__(self) -> None:
        self.client, self.is_oauth = _build_client()

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        max_tokens: int = 8000,
        stream: bool = False,
    ) -> str:
        """단일 턴 completion. 응답의 텍스트 블록들을 이어붙여 반환한다."""
        system_blocks: list[dict] = []
        if self.is_oauth:
            system_blocks.append({"type": "text", "text": _CLAUDE_CODE_IDENTITY})
        system_blocks.append({"type": "text", "text": system})

        kwargs = dict(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
        )

        if stream:
            parts: list[str] = []
            with self.client.messages.stream(**kwargs) as s:
                for chunk in s.text_stream:
                    parts.append(chunk)
            return "".join(parts)

        msg = self.client.messages.create(**kwargs)
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
