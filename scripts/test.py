#!/usr/bin/env python3
"""테스트 실행 스크립트.

사용:
    python scripts/test.py              # 전체 테스트 (tests/) 실행
    python scripts/test.py -k stt       # 이후 인자는 그대로 pytest 로 전달
    python scripts/test.py tests/test_stt.py -v

설명:
    - `uv run pytest` 로 실행한다. `uv run` 이 실행 전 dev 의존성(pytest 포함,
      pyproject.toml 의 [dependency-groups].dev)을 자동 동기화하므로 별도 설치가 필요 없다.
    - 인자를 안 주면 기본으로 `tests/` 전체를 `-q` 로 돌린다. 인자를 하나라도 주면 그대로
      pytest 에 넘기고(-q 강제 안 함) 대상 선택은 호출자가 책임진다.
"""

from __future__ import annotations

import sys

from _common import require_uv, run


def main() -> int:
    forwarded = sys.argv[1:]
    args = forwarded if forwarded else ["-q", "tests/"]

    require_uv()
    return run(["uv", "run", "pytest", *args])


if __name__ == "__main__":
    raise SystemExit(main())
