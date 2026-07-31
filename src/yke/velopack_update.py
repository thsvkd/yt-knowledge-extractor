"""Velopack 기반 설치/자동 업데이트 통합.

Velopack(Squirrel 후속)이 설치와 자동 업데이트를 함께 담당한다. 설치본은
``%LocalAppData%\\YtKnowledgeExtractor\\current\\`` 에 놓이고(경로 고정 — OneDrive 로
리다이렉트된 "문서" 폴더 경합/제어된 폴더 액세스 이슈를 피한다), 업데이트는 GitHub
Releases 의 ``releases.win.json`` + nupkg(델타 우선, 없으면 전체)로 받는다.

이 모듈은 velopack 바인딩(``velopack.pyd``, abi3)을 감싼 얇은 계층이다. velopack 이 없는
개발 실행이나 설치 컨텍스트가 아닌 실행에서는 모든 함수가 안전하게 no-op / False 로
떨어져 앱 기동을 막지 않는다.

설치/업데이트/제거 라이프사이클 훅(``--veloapp-*``)은 이 모듈이 다루지 않는다. flet 이
만드는 Flutter 러너가 명령행 인자를 "개발자 모드"로 해석해 파이썬을 실행조차 하지 않기
때문에, 훅은 네이티브 진입점에서 처리한다(``scripts/flet_template.py`` 참고).

- :func:`is_installed` — Velopack 설치 컨텍스트에서 도는지(업데이트 적용 가능 여부 가드).
- :func:`check` / :func:`download_and_apply` — 업데이트 확인·적용(네트워크는 호출자가
  워커 스레드에서 돌린다).
"""

from __future__ import annotations

from collections.abc import Callable

# GitHub Releases 소스. 릴리스 에셋에 releases.win.json + *.nupkg 가 올라가 있어야 한다.
REPO_URL = "https://github.com/thsvkd/yt-knowledge-extractor"

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
            try:
                progress_cb(max(0.0, min(1.0, float(percent) / 100.0)))
            except Exception:  # noqa: BLE001
                pass

    _manager().download_updates(info, cb)


def apply_and_restart(info) -> None:
    """받아둔 업데이트로 ``current\\`` 를 교체하고 앱을 재시작한다 — 이 호출로 프로세스가
    종료된다. :func:`download` 를 먼저 마친 뒤 호출하며, 호출 전 사용자 안내를 끝내야 한다.
    """
    _manager().apply_updates_and_restart(info)
