"""flet build 진입점.

``flet build`` 는 앱 루트(``src/``)에서 이 모듈을 ``__main__`` 스크립트로 실행하므로,
패키지 내부 상대 임포트가 깨지지 않도록 절대 임포트로 GUI 를 불러와 실행한다.
개발 중 실행(``yke-gui``)은 :func:`yke.gui.main` 을 직접 진입점으로 쓰며, 이 파일은
네이티브 빌드 전용 얇은 셔임이다.

맨 먼저 Velopack 설치/업데이트 라이프사이클 훅을 처리한다. 설치/업데이트/제거 시
Velopack 이 앱을 훅(환경변수)과 함께 재실행하는데, :func:`run_startup_hooks` 가 그걸
가로채 처리하고 필요하면 프로세스를 종료한다. 무거운 flet(``yke.gui``) 임포트보다 앞에
두어, 훅 실행 상황에서 불필요한 UI 로드를 피한다. (velopack 미설치/개발 실행이면 no-op)
"""

from yke.velopack_update import run_startup_hooks

run_startup_hooks()

from yke.gui import main  # noqa: E402 - Velopack 훅을 UI 로드보다 먼저 처리해야 한다.

main()
