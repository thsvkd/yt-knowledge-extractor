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
        # api_key=None: 환경에 ANTHROPIC_API_KEY 도 있어도 x-api-key 를 함께 보내지 않도록
        # 명시(anthropic 0.116 에선 auth_token 사용 시 Authorization 헤더만 나가는 것을 확인).
        client = anthropic.Anthropic(
            auth_token=oauth,
            api_key=None,
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
                final = s.get_final_message()
            self._warn_if_truncated(final, max_tokens)
            return "".join(parts)

        msg = self.client.messages.create(**kwargs)
        self._warn_if_truncated(msg, max_tokens)
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

    @staticmethod
    def _warn_if_truncated(msg, max_tokens: int) -> None:
        if getattr(msg, "stop_reason", None) == "max_tokens":
            print(
                f"  경고: 응답이 max_tokens({max_tokens})에서 잘렸습니다 — "
                "결과가 불완전할 수 있습니다(청크 축소/재시도 고려)."
            )
