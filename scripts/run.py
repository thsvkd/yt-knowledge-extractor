#!/usr/bin/env python3
"""앱 실행 스크립트. 기본은 GUI 데스크톱 앱을 띄우고, --cli 로 CLI 파이프라인을 돌린다.

사용:
    python scripts/run.py                              # GUI 앱 실행 (기본)
    python scripts/run.py --cli                        # CLI 파이프라인 실행 (전체)
    python scripts/run.py --cli --stage transcript     # --cli 뒤 인자는 그대로 yke 로 전달
    python scripts/run.py --cli --limit 5 --force

설명:
    - GUI(`yke-gui`)와 CLI(`yke`)는 동일한 파이프라인 코어(run_pipeline)를 공유한다.
    - --cli 뒤의 인자는 손대지 않고 그대로 `yke` 로 넘긴다(옵션 목록은 `uv run yke --help`).
    - `uv run` 이 실행 전 의존성을 자동 동기화한다. 최초 구성은 `python scripts/setup.py`.
    - GPU STT 가속은 `python scripts/setup.py --gpu` 로 런타임을 설치한 경우에만 켜지며,
      없으면 자동으로 CPU 로 폴백한다.
"""

from __future__ import annotations

import argparse

from _common import fail, require_uv, run


def main() -> int:
    # allow_abbrev=False: --cli 만 정확히 소비하고, 나머지(--stage 등)는 축약 매칭 없이
    # 그대로 yke 로 전달되게 한다.
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--cli",
        action="store_true",
        help="GUI 대신 CLI 파이프라인(yke)을 실행한다. 이후 인자는 yke 로 전달된다.",
    )
    args, forwarded = parser.parse_known_args()

    require_uv()

    # GUI 는 추가 인자를 받지 않는다. --cli 없이 인자가 붙었다면 대개 --cli 를 빠뜨린 것이므로
    # yke-gui 로 넘겨 알 수 없는 에러를 내는 대신 여기서 안내하고 멈춘다.
    if not args.cli and forwarded:
        fail(
            "GUI 모드는 추가 인자를 받지 않습니다. CLI 옵션을 쓰려면 --cli 를 앞에 붙이세요:\n"
            f"  python scripts/run.py --cli {' '.join(forwarded)}"
        )

    entry = "yke" if args.cli else "yke-gui"
    command = ["uv", "run", entry, *forwarded]

    try:
        return run(command)
    except KeyboardInterrupt:
        # CLI 는 Ctrl+C 로 중단하는 게 정상 흐름 — 트레이스백 대신 관용적 종료 코드로 끝낸다.
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
