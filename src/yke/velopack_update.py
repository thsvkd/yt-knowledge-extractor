"""Velopack 기반 설치/자동 업데이트 통합.

Velopack(Squirrel 후속)이 설치와 자동 업데이트를 함께 담당한다. 설치 위치는 OS 마다 다르다.

- Windows: ``%LocalAppData%\\YtKnowledgeExtractor\\current\\`` **고정**(OneDrive 로
  리다이렉트된 "문서" 폴더 경합/제어된 폴더 액세스 이슈를 피한다).
- macOS: 설치 시 사용자가 고른 ``/Applications`` 또는 ``~/Applications`` 아래의
  ``YouTube Knowledge Extractor.app``(번들 이름은 ``vpk --packTitle`` 값이다).

업데이트는 GitHub Releases 의 채널별 피드 — Windows 는 ``releases.win.json``, macOS 는
``releases.osx.json`` — 와 nupkg(델타 우선, 없으면 전체)로 받는다.

이 모듈은 velopack 바인딩(``velopack.pyd``, abi3)을 감싼 얇은 계층이다. velopack 이 없는
개발 실행이나 설치 컨텍스트가 아닌 실행에서는 모든 함수가 안전하게 no-op / False 로
떨어져 앱 기동을 막지 않는다.

설치/업데이트/제거 라이프사이클 훅(``--veloapp-*``)은 이 모듈이 다루지 않는다. flet 이
만드는 Flutter 러너가 명령행 인자를 "개발자 모드"로 해석해 파이썬을 실행조차 하지 않기
때문에, 훅은 네이티브 진입점에서 처리한다(``scripts/flet_template.py`` 참고). 이는
**Windows 한정**이다 — macOS 는 애초에 훅 인자를 넘기지 않고, ``.pkg`` 의 postinstall
스크립트가 ``VELOPACK_FIRSTRUN=1`` 환경변수를 세운 채 인자 없이 앱을 띄운다.

- :func:`run_startup_maintenance` — 오래된 패키지 정리 등 설치본 유지보수. 무거우므로
  창이 뜬 뒤 워커 스레드에서 부른다(GUI 의 업데이트 확인 스레드가 먼저 호출).
- :func:`is_installed` — Velopack 설치 컨텍스트에서 도는지(업데이트 적용 가능 여부 가드).
- :func:`check` / :func:`download_and_apply` — 업데이트 확인·적용(네트워크는 호출자가
  워커 스레드에서 돌린다).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# GitHub Releases 소스. 릴리스 에셋에 그 OS 채널의 피드(releases.win.json /
# releases.osx.json) + *.nupkg 가 올라가 있어야 한다(파일명 완전 일치로만 찾는다).
REPO_URL = "https://github.com/thsvkd/yt-knowledge-extractor"


def run_startup_maintenance() -> None:
    """velopack ``App().run()`` — 설치본 유지보수. **워커 스레드에서** 호출한다.

    설치/업데이트/제거 라이프사이클 훅은 네이티브 러너가 이미 처리하므로(모듈 docstring
    참고) 여기서 걸리는 훅은 없다. 그래도 이 호출이 필요한 이유는 ``App().run()`` 이
    다음 일도 같이 하기 때문이다.

    - **packages 폴더의 오래된 nupkg 삭제.** velopack 에서 이 정리를 하는 곳은 여기뿐이다
      (업데이트 적용 경로는 실패한 패키지만 지운다). 부르지 않으면 업데이트할 때마다 이전
      전체 패키지가 그대로 쌓인다(이 앱은 전체 패키지가 100MB 단위다).
    - 받아 두고 아직 적용하지 않은 업데이트가 있으면 적용 후 재시작.
    - 현재 프로세스의 AppUserModelID 설정(작업 표시줄 그룹화).

    velopack 은 네이티브 모듈이라 import 만으로 0.5초 이상 걸린다. 창이 뜨기 전에 부르면
    첫 화면이 그만큼 늦어지므로 반드시 백그라운드에서 부른다(창은 어차피 이 호출 전에
    떠 있었다 — Flutter 가 첫 프레임에서 창을 보여준 뒤에야 파이썬이 붙는다).
    비설치/개발 실행이면 조용히 no-op 이며, 어떤 예외도 앱 동작을 막지 않는다.
    """
    try:
        from velopack import App

        App().run()
    except Exception:  # noqa: BLE001 - 업데이트 계층 실패가 앱 동작을 막으면 안 된다.
        logger.debug("velopack 시작 유지보수 건너뜀(미설치/개발 실행)", exc_info=True)


_manager_cache = None


def _manager():
    """``UpdateManager`` 를 만들어 캐시한다.

    velopack 은 여기서 지연 임포트한다 — 네이티브 모듈이라 로드가 무거워(0.5초 이상) 앱
    기동 경로에서 부르면 첫 화면이 그만큼 늦어진다. 호출부(GUI)가 워커 스레드에서만
    쓰므로 시작 시간에는 영향을 주지 않는다.
    """
    global _manager_cache
    if _manager_cache is None:
        from velopack import GithubSource, UpdateManager

        # 채널(win/osx)을 여기서 넘기지 않는 것은 의도된 것이다. 채널은 패키징 시점
        # (``vpk pack --channel``)에 nuspec 에 박혀 설치본이 스스로 안다. ``ExplicitChannel``
        # 을 여기서 넘기면 오히려 win 설치본이 osx 피드를 보는 식의 채널 전환 버그를 만든다.
        _manager_cache = UpdateManager(GithubSource(REPO_URL))
    return _manager_cache


def is_installed() -> bool:
    """Velopack 설치본에서 실행 중인지. 개발/비설치 실행이면 False.

    설치 메타데이터가 없으면 ``get_current_version`` 이 실패하므로 그걸 가드로 쓴다.
    """
    try:
        _manager().get_current_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def current_version() -> str | None:
    """Velopack 이 인식하는 현재 설치 버전(비설치면 None)."""
    try:
        return _manager().get_current_version()
    except Exception:  # noqa: BLE001
        return None


def check():
    """업데이트가 있으면 ``UpdateInfo``, 없으면 None.

    GitHub 로 네트워크 호출이 일어나므로 워커 스레드에서 호출한다. 네트워크/컨텍스트
    오류는 그대로 올려 호출자가 처리한다(GUI 가 상태 메시지로 표시).
    """
    return _manager().check_for_updates()


def target_version(info) -> str:
    """``UpdateInfo`` 가 가리키는 대상 버전 문자열."""
    try:
        return info.TargetFullRelease.Version
    except Exception:  # noqa: BLE001
        return "?"


def download(info, progress_cb: Callable[[float], None] | None = None) -> None:
    """업데이트(델타 우선)를 로컬로 내려받는다(아직 적용하지 않음).

    ``progress_cb`` 는 0.0~1.0 진행률을 받는다(velopack 은 0~100 정수를 주므로 환산).
    """
    cb = None
    if progress_cb is not None:

        def cb(percent):  # velopack: 0~100 int
            # 진행률 표시가 실패해도 다운로드가 끊기면 안 된다(콜백 예외는 velopack 쪽으로
            # 전파된다).
            with contextlib.suppress(Exception):
                progress_cb(max(0.0, min(1.0, float(percent) / 100.0)))

    _manager().download_updates(info, cb)


def apply_and_restart(info) -> None:
    """받아둔 업데이트로 설치본을 교체하고 앱을 재시작한다 — 이 호출로 프로세스가
    종료된다. :func:`download` 를 먼저 마친 뒤 호출하며, 호출 전 사용자 안내를 끝내야 한다.

    교체 방식은 OS 마다 다르다. Windows 는 ``current\\`` 폴더를 통째로 갈아 끼우고,
    macOS 는 ``.app`` 번들 전체를 ``renamex_np`` 로 **원자 교체**한다. 그래서 macOS 에서
    ``/Applications`` (모든 사용자 공용)에 설치했다면 교체 시점에 관리자 인증 팝업이 뜰 수
    있다 — 이 팝업을 피하려면 설치할 때 ``~/Applications`` 를 고르면 된다.
    """
    _manager().apply_updates_and_restart(info)
