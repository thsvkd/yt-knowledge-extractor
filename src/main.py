"""flet build 진입점.

``flet build`` 는 앱 루트(``src/``)에서 이 모듈을 ``__main__`` 스크립트로 실행하므로,
패키지 내부 상대 임포트가 깨지지 않도록 절대 임포트로 GUI 를 불러와 실행한다.
개발 중 실행(``yke-gui``)은 :func:`yke.gui.main` 을 직접 진입점으로 쓰며, 이 파일은
네이티브 빌드 전용 얇은 셔임이다.

Velopack 설치/업데이트/제거 훅(``--veloapp-*``)은 여기서 처리하지 않는다 — 처리할 수가
없다. flet 이 만드는 Flutter 러너는 명령행 인자가 있으면 "개발자 모드"로 간주해 파이썬을
아예 실행하지 않으므로, 훅과 함께 실행된 프로세스는 이 파일에 도달하지 못한다. 그래서
훅은 네이티브 진입점에서 곧바로 처리한다(``scripts/flet_template.py`` 의 러너 패치 참고).
덕분에 앱이 정상 실행될 때 velopack 네이티브 모듈(로드에만 0.5초 이상)을 시작 경로에서
import 하지 않아도 되어 첫 화면이 그만큼 빨리 뜬다. 훅이 아닌 설치본 유지보수(오래된
패키지 정리 등)는 GUI 가 창을 띄운 뒤 워커 스레드에서 처리한다
(:func:`yke.velopack_update.run_startup_maintenance`).
"""

from yke.gui import main

main()
