"""기동 단계 타임스탬프 로그.

"업데이트 후 다시 켜질 때 무한 로딩에 걸린다"는 사용자 제보를 잡기 위한 것이다. 이 증상은
**재현된 그 머신에서만** 원인을 알 수 있는데, 지금은 아무 흔적도 남지 않아 사후에 물어볼
것이 없다. 그래서 기동 경로의 각 단계에 도달한 시각을 파일로 남긴다 — 다음에 멈추면
마지막 줄이 어디까지 갔는지 그대로 알려 준다.

왜 표준 logging 의 파일 핸들러가 아니라 이 얇은 모듈인가:

- **설치 폴더 밖**에 써야 한다. Velopack 업데이트가 ``current/`` 를 통째로 교체하므로
  그 안에 쓴 로그는 다음 업데이트에 사라진다(무한 로딩은 바로 그 업데이트 직후에 난다).
- 앱이 **뜨기 전에** 죽는 경우를 잡아야 하므로, 무거운 import 나 설정 로딩보다 먼저
  쓸 수 있을 만큼 의존성이 없어야 한다(표준 라이브러리만 쓴다).
- 매 줄 **flush** 해야 한다. 프로세스가 응답 없이 강제 종료돼도 직전까지가 남아야 한다.

로그는 실행마다 새로 시작하지 않고 이어 붙이되, 너무 커지면 통째로 잘라낸다(무한 재시작
루프가 원인일 경우 디스크를 채우지 않도록).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# 이 크기를 넘으면 파일을 비우고 새로 시작한다. 한 번 기동에 열 줄 남짓이라 넉넉하다.
_MAX_BYTES = 512 * 1024

_path: Path | None = None
_start = time.monotonic()


def _resolve_path() -> Path | None:
    """로그 파일 경로. 준비에 실패하면 ``None``(로깅 때문에 앱이 죽으면 안 된다)."""
    global _path
    if _path is not None:
        return _path
    try:
        from .appdirs import user_data_dir

        base = user_data_dir("YtKnowledgeExtractor")
        base.mkdir(parents=True, exist_ok=True)
        path = base / "startup.log"
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            path.unlink()
        _path = path
        return _path
    except Exception:  # noqa: BLE001 - 경로 준비 실패가 기동을 막으면 안 된다.
        return None


def step(name: str, detail: str = "") -> None:
    """기동 단계 하나를 기록한다. 어떤 예외도 밖으로 내보내지 않는다.

    각 줄은 ``<벽시계> +<프로세스 시작 이후 초> pid=<pid> <단계> <상세>`` 꼴이다.
    경과 시간이 있어야 "어디서 오래 걸렸나"를 로그만 보고 알 수 있다.
    """
    path = _resolve_path()
    if path is None:
        return
    line = (
        f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"+{time.monotonic() - _start:7.2f}s pid={os.getpid()} {name}"
    )
    if detail:
        line += f" {detail}"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())  # 강제 종료돼도 직전 줄이 남아야 한다.
    except Exception:  # noqa: BLE001
        pass


def session_start(version: str) -> None:
    """한 번의 기동을 구분하는 머리글. 매 실행의 첫 줄로 남긴다."""
    step("=== 기동 시작", f"v{version} python={sys.version.split()[0]} platform={sys.platform}")
