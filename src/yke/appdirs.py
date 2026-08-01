"""OS 관습에 맞는 사용자 데이터 디렉터리.

Velopack 설치본은 ``current/`` 안에 놓이고 업데이트 때 그 폴더가 **통째로 교체**된다.
그래서 GUI 설정·온디맨드로 받은 GPU 런타임처럼 업데이트를 넘어 살아남아야 하는 데이터는
설치 폴더 밖, 각 OS 가 정한 사용자 데이터 경로에 둬야 한다.

경로 규칙은 OS 관습을 그대로 따른다.

- Windows: ``%LOCALAPPDATA%\\<vendor>\\<app_id>``. 로밍(``%APPDATA%``)이 아니라 로컬을
  쓰는 이유는 GPU 런타임처럼 수백 MB짜리 캐시가 도메인 로그인마다 동기화되면 안 되기
  때문이다. **vendor 하위 폴더가 반드시 필요하다** — 아래 주석 참고.
- macOS: ``~/Library/Application Support/<app_id>`` — 홈 최상위에 점 없는 폴더
  (``~/YtKnowledgeExtractor``)를 만드는 건 macOS 관습 위반이라 Finder 에 그대로 노출된다.
- 그 외(Linux 등): XDG Base Directory 규약의 ``$XDG_DATA_HOME`` (미설정 시
  ``~/.local/share``) 아래.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows 에서 사용자 데이터를 담을 vendor 폴더. Velopack 은 앱을
# ``%LOCALAPPDATA%\<packId>\`` 에 설치하는데(packId 는 우리 app_id 와 같은
# "YtKnowledgeExtractor"), 예전에는 우리 사용자 데이터도 **정확히 같은 폴더**에 넣었다.
# 그래서 설치·업데이트가 돌 때마다 Velopack 이 그 폴더를 정리하며 우리 파일을 지웠다.
#
# 실측 확인(Windows, v0.1.3 설치기 --silent): 설치 직후 gui_settings.json 이 사라졌다.
# gpu-runtime/ 도 같은 자리에 있었으므로, 업데이트할 때마다 사용자는 저장 폴더 설정이
# 초기화되고 cuBLAS 런타임 ~900MB 를 다시 받아야 했다.
#
# vendor 폴더를 한 단계 끼우면 Velopack 이 관리하는 경로와 완전히 갈라진다.
_WINDOWS_VENDOR = "thsvkd"

# 예전 경로에서 새 경로로 옮길 우리 소유 항목. **이 목록에 있는 것만** 옮긴다 —
# 예전 경로는 Velopack 의 설치 루트이기도 해서 current/·packages/·Update.exe 가 같이
# 들어 있고, 그것들을 건드리면 설치본이 깨진다.
_OWNED_ITEMS = ("gui_settings.json", "gpu-runtime")

# app_id 별로 이 프로세스에서 이미 이전을 시도했는지.
_migrated: set[str] = set()


def _windows_data_dir(app_id: str, localappdata: str | None) -> Path:
    """Windows 사용자 데이터 경로. ``%LOCALAPPDATA%`` 가 비면 홈으로 떨어진다.

    폴백을 홈으로 두는 것은 예전 동작 그대로다(서비스 계정 등 드문 환경). 바꾸면 기존
    사용자의 설정이 통째로 사라진 것처럼 보인다.
    """
    return Path(localappdata or os.path.expanduser("~")) / _WINDOWS_VENDOR / app_id


def _windows_legacy_dir(app_id: str, localappdata: str | None) -> Path:
    """예전(=Velopack 설치 루트와 겹치던) Windows 경로. 이전 대상이자 **삭제 금지** 경로."""
    return Path(localappdata or os.path.expanduser("~")) / app_id


def migrate_owned_items(legacy: Path, target: Path) -> list[str]:
    """``legacy`` 에 남은 우리 소유 항목만 ``target`` 으로 옮기고, 옮긴 이름을 돌려준다.

    - :data:`_OWNED_ITEMS` 에 있는 것만 건드린다(legacy 는 Velopack 설치 루트이기도 하다).
    - ``target`` 에 이미 같은 이름이 있으면 건너뛴다 — 새 경로 쪽이 최신이다.
    - 어떤 실패도 올리지 않는다. 데이터 이전이 앱 기동을 막으면 안 된다.
    """
    moved: list[str] = []
    if not legacy.is_dir() or legacy == target:
        return moved
    for name in _OWNED_ITEMS:
        src = legacy / name
        dst = target / name
        if not src.exists() or dst.exists():
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(name)
        except OSError:
            logger.debug("사용자 데이터 이전 실패: %s", src, exc_info=True)
    return moved


def user_data_dir(app_id: str) -> Path:
    """앱의 영속 데이터 루트(설치 폴더 밖, 업데이트에도 유지되는 경로).

    Windows 에서는 **처음 호출될 때 한 번** 예전 경로에 남은 데이터를 새 경로로 옮긴다
    (:data:`_WINDOWS_VENDOR` 주석의 실측 사례 참고). 부수효과를 함수에 넣은 이유는,
    호출자가 GUI·CLI·GPU 런타임으로 흩어져 있어 "시작할 때 한 번 부르는 자리"를 한 곳으로
    정할 수 없기 때문이다. 이전은 프로세스당 app_id 마다 한 번만 시도한다.

    Args:
        app_id: 앱 식별자. Velopack packId(``YtKnowledgeExtractor``)와 같은 값을 쓴다.
    """
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA")
        target = _windows_data_dir(app_id, localappdata)
        if app_id not in _migrated:
            _migrated.add(app_id)
            moved = migrate_owned_items(_windows_legacy_dir(app_id, localappdata), target)
            if moved:
                logger.info("사용자 데이터를 새 경로로 옮겼습니다: %s", ", ".join(moved))
        return target
    if sys.platform == "darwin":
        # macOS 는 Velopack 이 /Applications 에 설치하므로 경로가 겹치지 않는다 — 옮길 이유가
        # 없고, 옮기면 기존 사용자의 설정만 미아가 된다.
        return Path.home() / "Library" / "Application Support" / app_id
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / app_id
