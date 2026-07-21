#!/usr/bin/env python3
"""버전 릴리스 스크립트: 버전 확인 -> 빌드 -> 릴리스 노트 생성 -> GitHub 릴리스 업로드.

사용:
    python scripts/deploy.py            # 버전 확인 -> 빌드 -> 릴리스 생성/업로드
    python scripts/deploy.py --dry-run  # 릴리스 노트만 생성해 출력한다(빌드·업로드 없음)

절차:
    0. pyproject.toml 의 [project].version(SSOT)을 미리 올려 둔다(이 스크립트가 대신
       올려주지 않는다). 이전 GitHub 릴리스와 버전이 같으면 올리는 걸 잊은 것으로 보고
       중단한다.
    1. scripts/build.py 로 Velopack 설치기를 빌드한다(빌드 중 src/yke/__init__.py 의
       __version__ 을 pyproject.toml 기준으로 자동 동기화한다).
    2. 이전 릴리스 태그 이후의 git 커밋 로그를 `claude -p` 에 넘겨(지침:
       scripts/release_notes_guide.md) 릴리스 노트를 생성하고, GitHub 릴리스를 만들어
       Setup.exe + *.nupkg + releases.win.json 을 업로드한다.

사전 준비:
    - scripts/build.py 와 동일(uv, Windows 라면 VS C++ 빌드 도구·Velopack CLI).
    - gh CLI 로그인(`gh auth login`).
    - claude CLI 로그인(`claude login`) — 릴리스 노트 생성에 필요하다.

GPU 온디맨드 런타임(gpu-runtime-cu12 릴리스)은 앱 버전과 무관해 이 스크립트가 다루지
않는다. 필요할 때 `python scripts/build.py --gpu-runtime` 으로 따로 만든다.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

from _common import REPO_ROOT, check, fail, info, pyproject_version

# 콘솔이 UTF-8 이 아니면(한국어 Windows 기본 cp949) 릴리스 노트(claude -p 가 생성한 임의의
# 텍스트) 출력 중 인코딩 불가 문자에서 UnicodeEncodeError 로 죽는다 — 이 스크립트 자체
# 출력(도움말·릴리스 노트 등)은 UTF-8 로 강제한다.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_GUIDE_PATH = REPO_ROOT / "scripts" / "release_notes_guide.md"
_CLAUDE_TIMEOUT = 300  # 커밋 로그 요약이라 5분이면 충분히 여유 있다.


def _require_gh() -> None:
    if shutil.which("gh") is None:
        fail("gh(GitHub CLI)가 필요합니다. https://cli.github.com/ 를 참고하세요.")


def _latest_release_tag() -> str | None:
    """가장 최근 정식 앱 버전 릴리스 태그(v0.0.0 형식)를 돌려준다.

    gpu-runtime-cu12 같은 프리릴리스/드래프트는 버전 릴리스가 아니므로 제외한다.
    """
    proc = subprocess.run(
        ["gh", "release", "list", "--json", "tagName,isDraft,isPrerelease", "--limit", "100"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        fail(f"gh release list 실패: {proc.stderr.strip()}")
    releases = json.loads(proc.stdout or "[]")
    for r in releases:  # gh 는 최신순으로 돌려준다.
        if not r["isDraft"] and not r["isPrerelease"] and _VERSION_TAG_RE.match(r["tagName"]):
            return r["tagName"]
    return None


def _commit_log_since(prev_tag: str | None) -> str:
    """prev_tag 이후(없으면 전체 히스토리) 커밋의 제목+본문을 최신순으로 모은다."""
    rev_range = f"{prev_tag}..HEAD" if prev_tag else "HEAD"
    proc = subprocess.run(
        ["git", "log", rev_range, "--no-merges", "--pretty=format:- %s%n%b%n---"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        fail(f"git log 실패: {proc.stderr.strip()}")
    return proc.stdout.strip()


def generate_release_notes(prev_tag: str | None, tag: str, commit_log: str) -> str:
    """scripts/release_notes_guide.md 지침대로 `claude -p` 를 호출해 릴리스 노트를 만든다."""
    if shutil.which("claude") is None:
        fail("claude CLI 를 찾을 수 없습니다. https://claude.com/claude-code 를 설치·로그인하세요.")
    guide = _GUIDE_PATH.read_text(encoding="utf-8")
    user_prompt = (
        f"이전 릴리스: {prev_tag or '없음(첫 릴리스)'}\n"
        f"이번 릴리스: {tag}\n\n"
        f"커밋 로그:\n{commit_log}\n"
    )
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--system-prompt", guide,
        "--tools", "",
        "--no-session-persistence",
        "--setting-sources", "",
    ]
    info("릴리스 노트 생성 중 (claude -p)…")
    try:
        proc = subprocess.run(
            cmd,
            input=user_prompt,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        fail(f"claude -p 응답 시간 초과({_CLAUDE_TIMEOUT}초)")

    data: dict | None = None
    if proc.stdout.strip():
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = None
    if proc.returncode != 0 or (data is not None and data.get("is_error")):
        detail = (data or {}).get("result") or proc.stderr.strip() or f"종료 코드 {proc.returncode}"
        fail(f"claude -p 실패: {detail}")
    if data is None:
        fail(f"claude -p 출력 파싱 실패: {proc.stdout[:500]!r}")

    notes = str(data.get("result") or "").strip()
    if not notes:
        fail("claude -p 가 빈 릴리스 노트를 반환했습니다.")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="릴리스 노트만 생성해 출력한다(빌드·gh 업로드는 하지 않는다).",
    )
    args = parser.parse_args()

    _require_gh()

    version = pyproject_version()
    tag = f"v{version}"
    prev_tag = _latest_release_tag()

    # 0) 버전 확인: 이전 릴리스와 같으면 pyproject.toml 의 version 을 올리는 걸 잊은 것이다.
    if prev_tag == tag:
        fail(
            f"버전이 이전 릴리스({tag})와 같습니다. "
            "pyproject.toml 의 [project].version 을 올린 뒤 다시 실행하세요."
        )
    info(f"{prev_tag or '(첫 릴리스)'} → {tag}")

    commit_log = _commit_log_since(prev_tag)
    if not commit_log:
        fail(f"{prev_tag} 이후 커밋이 없습니다 — 릴리스할 변경사항이 없습니다.")

    if args.dry_run:
        notes = generate_release_notes(prev_tag, tag, commit_log)
        info("--dry-run: 빌드/업로드 없이 릴리스 노트만 출력합니다.")
        print(notes)
        return 0

    # 1) 빌드
    info("빌드 시작 (scripts/build.py)")
    check([sys.executable, str(REPO_ROOT / "scripts" / "build.py")])

    out_dir = REPO_ROOT / "dist" / "velopack"
    setup_exes = sorted(out_dir.glob("*-Setup.exe"))
    nupkgs = sorted(out_dir.glob("*.nupkg"))
    releases_json = out_dir / "releases.win.json"
    if not setup_exes:
        fail(f"{out_dir} 에서 *-Setup.exe 를 찾지 못했습니다(빌드 실패?).")
    if not nupkgs:
        fail(f"{out_dir} 에서 *.nupkg 를 찾지 못했습니다(빌드 실패?).")
    if not releases_json.exists():
        fail(f"{releases_json} 이 없습니다(빌드 실패?).")

    # 2) 릴리스 노트 생성 + GitHub 릴리스 생성/업로드
    notes = generate_release_notes(prev_tag, tag, commit_log)
    notes_path = out_dir / "RELEASE_NOTES.md"
    notes_path.write_text(notes, encoding="utf-8")
    info(f"릴리스 노트: {notes_path}")

    assets = [str(p) for p in (*setup_exes, *nupkgs)] + [str(releases_json)]
    info(f"GitHub 릴리스 생성/업로드: {tag}")
    check(["gh", "release", "create", tag, *assets, "--title", tag, "--notes-file", str(notes_path)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
