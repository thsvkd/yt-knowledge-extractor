"""Claude/Anthropic 토큰의 영속 저장.

배포 빌드에서 ``.env`` 는 부적절하므로(사용자가 프로젝트 루트에 파일을 만들어야 함),
토큰을 앱 저장소에 보관한다. 기본 백엔드는 flet 의 ``shared_preferences`` 이고,
사용자가 파일 경로를 지정하면 그 JSON 파일에 저장/로드한다(원하면 그 파일을 그대로
다른 기기로 옮기거나 배포 스크립트에서 다룰 수 있다).

``shared_preferences`` 메서드는 async 이므로 이 모듈 함수도 async 다 — GUI 에서는
``page.run_task`` 나 async 핸들러로 호출한다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_KEY = "yke.anthropic_token"
_PATH_KEY = "yke.token_file"


async def load(prefs) -> tuple[str, str]:
    """저장된 토큰과 위치 라벨을 반환한다.

    사용자가 지정한 파일 경로가 있으면 그 파일에서, 없으면 앱 저장소에서 읽는다.

    Returns:
        (token, location_label). 토큰이 없으면 ("", 라벨).
    """
    path = await prefs.get(_PATH_KEY)
    if path:
        p = Path(path)
        if not p.exists():
            return "", f"파일 없음: {p}"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return str(data.get("anthropic_token") or ""), f"파일: {p}"
        except (OSError, ValueError):
            logger.warning("토큰 파일 읽기 실패: %s", p, exc_info=True)
            return "", f"파일 읽기 실패: {p}"
    token = await prefs.get(_TOKEN_KEY)
    return (str(token) if token else ""), "앱 저장소"


async def get_path(prefs) -> str | None:
    """지정된 토큰 저장 파일 경로(없으면 None)."""
    path = await prefs.get(_PATH_KEY)
    return str(path) if path else None


def _try_unlink(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("토큰 파일 삭제 실패: %s", path, exc_info=True)


def _same_file(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return str(a) == str(b)


async def save(prefs, token: str, custom_path: str | None) -> str:
    """토큰을 저장하고 위치 라벨을 반환한다.

    ``custom_path`` 가 있으면 그 JSON 파일에 저장하고 경로를 기억한다(앱 저장소의
    토큰 키는 지워 단일 출처로 만든다). 없으면 앱 저장소에 저장한다.

    백엔드를 바꿀 때(파일→앱저장소, 또는 다른 파일로) 이전에 쓴 평문 토큰 파일이
    고아로 남지 않도록 정리한다(방금 쓴 파일은 건드리지 않는다).
    """
    custom_path = (custom_path or "").strip()
    old_path = await prefs.get(_PATH_KEY)
    if custom_path:
        p = Path(custom_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"anthropic_token": token}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if old_path and not _same_file(old_path, p):
            _try_unlink(old_path)
        await prefs.set(_PATH_KEY, str(p))
        await prefs.remove(_TOKEN_KEY)
        return f"파일: {p}"
    if old_path:
        _try_unlink(old_path)  # 앱 저장소로 전환 — 이전 평문 파일 정리
    await prefs.set(_TOKEN_KEY, token)
    await prefs.remove(_PATH_KEY)
    return "앱 저장소"


async def clear(prefs) -> None:
    """저장된 토큰(및 지정 파일)을 모두 제거한다."""
    path = await prefs.get(_PATH_KEY)
    if path:
        _try_unlink(path)
    await prefs.remove(_TOKEN_KEY)
    await prefs.remove(_PATH_KEY)
