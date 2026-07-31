#!/usr/bin/env python3
"""OS 별 Velopack 패키징·업로드 규약의 단일 소스.

build.py(패킹)와 deploy.py(업로드)가 같은 파일명 규칙을 각자 하드코딩하다 어긋나면
"빌드는 됐는데 자동 업데이트가 안 되는" 조용한 실패가 난다(Velopack 은 릴리스에서
releases.<channel>.json 을 이름 완전 일치로만 찾는다). 그래서 채널·실행파일 이름·
업로드 글롭을 여기 한 곳에서만 정의한다.

표준 라이브러리만 쓰고 ``_common`` 도 import 하지 않는다 — deploy.py 쪽 테스트가 이
모듈만 단독으로 import 할 수 있어야 하고, ``_common`` 은 import 시점에 stdout 인코딩을
건드리는 부작용이 있어 순수한 규약 정의와 섞고 싶지 않다. 그래서 REPO_ROOT 도 여기서
따로 계산한다.
"""

from __future__ import annotations

import platform
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Velopack packId / 표시 이름 / 레포 URL. src/yke/velopack_update.py·gpu_runtime.py 의
# 값과 반드시 일치해야 한다(앱이 이 packId 로 설치되고 이 레포 릴리스에서 업데이트를 받는다).
PACK_ID = "YtKnowledgeExtractor"
PRODUCT = "YouTube Knowledge Extractor"          # vpk --packTitle / flet --product
REPO_URL = "https://github.com/thsvkd/yt-knowledge-extractor"
PACK_AUTHORS = "thsvkd"

# Velopack 산출물이 모이는 폴더. **각 OS 의 로컬 폴더여야 한다 — 두 OS 가 실제로 공유하면
# 안 된다**(네트워크/동기화 폴더에 두거나, 확인차 상대 OS 산출물을 여기 복사하는 것 포함).
# 파일명이 겹치지 않는 것과는 **별개의 문제**다: ``vpk pack`` 은 --outputDir 안의 nupkg 를
# 채널과 무관하게 전부 훑어(ReleaseEntryHelper) 상대 채널의 인덱스까지 다시 쓴다. 실측
# 확인: win nupkg 하나가 놓인 폴더에 ``--channel osx`` 로 pack 했더니 그 win nupkg 하나만
# 담은 축소판 ``releases.win.json`` 과 레거시 ``RELEASES`` 가 새로 생성됐다. 그 축소된
# 피드가 업로드되면 구버전 Windows 설치본이 델타 체인을 못 풀어 업데이트가 조용히 깨진다.
# 빌드는 어차피 각 OS 로컬에서 따로 도므로 기본값 그대로 두면 된다.
VELOPACK_OUT = REPO_ROOT / "dist" / "velopack"

# nupkg 이름에서 채널 접미사가 빠지는 유일한 조합. vpk 1.2.0 의
# ``DefaultName.GetSuggestedReleaseName`` 을 역컴파일해 확인한 실제 규칙은
# ``os == Windows && channel == "win"`` 일 때만 접미사를 빼고, **그 밖에는 호스트 OS 와
# 무관하게 항상 ``-<채널>`` 을 붙인다** 는 것이다("채널이 그 OS 의 기본 채널이면 뺀다"가
# 아니다 — macOS/osx 조합에는 그 면제가 적용되지 않는다).
#
# 실측(macOS 호스트, vpk 1.2.0):
#   --channel osx → YtKnowledgeExtractor-0.1.5-osx-full.nupkg   (접미사 유지)
#   --channel win → YtKnowledgeExtractor-0.1.5-win-full.nupkg   (접미사 유지)
# 실측(Windows 호스트): 실제 배포된 v0.1.3 릴리스 에셋이 YtKnowledgeExtractor-0.1.3-full.nupkg.
#
# 따라서 win 글롭(``*-<ver>-full.nupkg``)과 osx 글롭(``*-<ver>-osx-full.nupkg``)은 서로의
# 파일을 절대 매치하지 않는다. 이름이 같아지면 두 OS 의 ``gh release upload --clobber`` 가
# 서로를 덮어쓰므로, tests/test_platform_spec.py 가 이 비충돌성을 직접 고정한다.
_DEFAULT_CHANNEL_TARGET = "windows"


@dataclass(frozen=True)
class PlatformSpec:
    target: str          # "windows" | "macos"  (build.py 의 _target() 값과 같은 어휘)
    channel: str         # "win" | "osx"        (vpk --channel)
    main_exe: str        # vpk --mainExe 에 넘길 '파일 이름'(경로 아님)
    setup_glob: str      # "*-Setup.exe" | "*-Setup.pkg"
    releases_json: str   # "releases.win.json" | "releases.osx.json"

    def nupkg_globs(self, version: str) -> tuple[str, str]:
        """(full, delta) 글롭. Velopack 은 **Windows 타깃 + win 채널**일 때만 채널 접미사를
        생략하고 그 밖에는 항상 붙인다(위 :data:`_DEFAULT_CHANNEL_TARGET` 의 역컴파일·실측
        근거 참고) — 그래서 win/osx 글롭은 서로의 파일을 절대 매치하지 않는다."""
        suffix = "" if self.target == _DEFAULT_CHANNEL_TARGET else f"-{self.channel}"
        return (f"*-{version}{suffix}-full.nupkg", f"*-{version}{suffix}-delta.nupkg")

    def upload_globs(self, version: str) -> tuple[str, ...]:
        """GitHub 릴리스에 올릴 에셋 글롭 전체(setup, full, delta, releases json 순).
        Portable.zip / assets.*.json / RELEASES-* 는 GithubSource 가 쓰지 않으므로 제외."""
        full, delta = self.nupkg_globs(version)
        return (self.setup_glob, full, delta, self.releases_json)


def artifact_name() -> str:
    """flet 이 만드는 실행 파일/번들의 이름. pyproject [project].name 이 SSOT다.

    flet 의 artifact_name 우선순위(--artifact → tool.flet.*.artifact → tool.flet.artifact
    → --project → [project].name) 중 이 저장소는 마지막만 설정돼 있다. 하드코딩하면
    이름이 바뀌었을 때 vpk 가 <app>/Contents/MacOS/<mainExe> 를 못 찾아 죽는다.
    """
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    try:
        return data["project"]["name"]
    except KeyError as exc:  # pragma: no cover - pyproject 가 깨진 경우
        raise ValueError("pyproject.toml 에서 [project].name 을 찾지 못했습니다.") from exc


def spec_for(target: str, *, artifact: str | None = None) -> PlatformSpec:
    """target("windows"|"macos") 의 규약. artifact 를 주면 pyproject 를 읽지 않는다(테스트용).

    Raises:
        ValueError: Velopack 을 쓰지 않는 타깃("linux" 등).
    """
    if target not in ("windows", "macos"):
        raise ValueError(f"Velopack 을 쓰지 않는 타깃입니다: {target}")
    name = artifact if artifact is not None else artifact_name()
    if target == "windows":
        return PlatformSpec(
            target="windows",
            channel="win",
            # Windows 는 .exe 확장자까지 포함한 '파일 이름'을 --mainExe 로 넘긴다.
            main_exe=f"{name}.exe",
            setup_glob="*-Setup.exe",
            releases_json="releases.win.json",
        )
    return PlatformSpec(
        target="macos",
        channel="osx",
        # macOS 는 <App>.app/Contents/MacOS/ 안의 확장자 없는 실행 파일 이름이다.
        main_exe=name,
        setup_glob="*-Setup.pkg",
        releases_json="releases.osx.json",
    )


def current() -> PlatformSpec:
    """실행 중인 OS 의 PlatformSpec. platform.system() 기준.

    Raises:
        ValueError: Velopack 을 쓰지 않는 OS.
    """
    system = platform.system()
    target = {"Windows": "windows", "Darwin": "macos"}.get(system)
    if target is None:
        raise ValueError(f"Velopack 을 쓰지 않는 OS 입니다: {system}")
    return spec_for(target)
