"""yt-knowledge-extractor: 유튜브 말 중심 콘텐츠 -> 개념 단위 지식베이스 PoC."""

# SSOT 는 pyproject.toml 의 [project].version 이다. 이 줄은 scripts/_common.py 의
# sync_version() 이 자동으로 덮어쓰는 결과물이니 직접 고치지 말 것 — 버전을 바꾸려면
# pyproject.toml 을 고친 뒤 `python scripts/setup.py` 또는 `python scripts/build.py` 를
# 실행해 반영한다(flet build 가 앱을 site-packages 로 정식 설치하지 않고 src/ 를 그대로
# 복사하므로, importlib.metadata 로는 배포된 앱에서 버전을 읽을 수 없다).
__version__ = "0.1.3"
