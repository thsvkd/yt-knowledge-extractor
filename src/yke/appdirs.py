"""OS 관습에 맞는 사용자 데이터 디렉터리.

Velopack 설치본은 ``current/`` 안에 놓이고 업데이트 때 그 폴더가 **통째로 교체**된다.
그래서 GUI 설정·온디맨드로 받은 GPU 런타임처럼 업데이트를 넘어 살아남아야 하는 데이터는
설치 폴더 밖, 각 OS 가 정한 사용자 데이터 경로에 둬야 한다.

경로 규칙은 OS 관습을 그대로 따른다.

- Windows: ``%LOCALAPPDATA%\\<app_id>`` — 기존 동작 그대로. 로밍(``%APPDATA%``)이 아니라
  로컬을 쓰는 이유는 GPU 런타임처럼 수백 MB짜리 캐시가 도메인 로그인마다 동기화되면
  안 되기 때문이다.
- macOS: ``~/Library/Application Support/<app_id>`` — 홈 최상위에 점 없는 폴더
  (``~/YtKnowledgeExtractor``)를 만드는 건 macOS 관습 위반이라 Finder 에 그대로 노출된다.
- 그 외(Linux 등): XDG Base Directory 규약의 ``$XDG_DATA_HOME`` (미설정 시
  ``~/.local/share``) 아래.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def user_data_dir(app_id: str) -> Path:
    """앱의 영속 데이터 루트(``current/`` 밖, 업데이트에도 유지되는 경로).

    Args:
        app_id: 앱 식별자. Velopack packId(``YtKnowledgeExtractor``)와 같은 값을 쓴다.
    """
    if sys.platform == "win32":
        # %LOCALAPPDATA% 가 비어 있는 드문 환경(서비스 계정 등)에서만 홈으로 떨어진다.
        # 이 폴백을 다른 값으로 바꾸면 기존 사용자의 GUI 설정이 통째로 사라진 것처럼
        # 보이므로, 기존 코드의 표현(os.path.expanduser("~"))을 그대로 유지한다.
        return Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / app_id
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_id
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / app_id
