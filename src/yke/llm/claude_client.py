"""Claude Code CLI(`claude -p`) 기반 클라이언트 래퍼.

Anthropic SDK 로 API 키/OAuth 토큰을 직접 관리하는 대신, 로컬에 설치된 `claude` CLI 를
헤드리스(`-p`/`--print`) 모드로 subprocess 호출한다. 인증은 CLI 자체의 로그인 상태
(`claude login` 또는 CLI 가 인식하는 환경변수)를 그대로 쓰므로 이 앱은 토큰을 저장·
주입하지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess

_CLAUDE_BIN = shutil.which("claude")
_TIMEOUT_SECONDS = 1200  # 대형 통합(6단계) 호출도 버틸 여유(20분)


def is_available() -> bool:
    """`claude` CLI 를 PATH 에서 찾을 수 있는지."""
    return _CLAUDE_BIN is not None


class ClaudeClient:
    def __init__(self) -> None:
        if _CLAUDE_BIN is None:
            raise RuntimeError(
                "'claude' CLI 를 찾을 수 없습니다. Claude Code"
                "(https://claude.com/claude-code)를 설치하고 `claude login` 으로 로그인하세요."
            )
        self._bin = _CLAUDE_BIN

    def complete(self, system: str, user: str, *, model: str) -> str:
        """단일 턴 completion. `claude -p` 를 호출해 응답 텍스트를 반환한다."""
        cmd = [
            self._bin,
            "-p",
            "--output-format", "json",
            "--model", model,
            "--system-prompt", system,
            "--tools", "",
            "--no-session-persistence",
            "--setting-sources", "",
        ]
        try:
            proc = subprocess.run(
                cmd,
                input=user,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude -p 응답 시간 초과({_TIMEOUT_SECONDS}초)") from exc

        data: dict | None = None
        if proc.stdout.strip():
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                data = None

        if proc.returncode != 0 or (data is not None and data.get("is_error")):
            detail = (data or {}).get("result") or proc.stderr.strip() or f"종료 코드 {proc.returncode}"
            raise RuntimeError(f"claude -p 실패: {detail}")
        if data is None:
            raise RuntimeError(f"claude -p 출력 파싱 실패: {proc.stdout[:500]!r}")

        self._warn_if_truncated(data)
        return str(data.get("result") or "")

    @staticmethod
    def _warn_if_truncated(data: dict) -> None:
        if data.get("stop_reason") == "max_tokens":
            print(
                "  경고: 응답이 모델 최대 출력 토큰에서 잘렸습니다 — "
                "결과가 불완전할 수 있습니다(청크 축소/재시도 고려)."
            )
