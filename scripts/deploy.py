#!/usr/bin/env python3
"""버전 릴리스 스크립트: 버전 확인 -> 빌드 -> 릴리스 노트 생성 -> GitHub 릴리스 업로드.

사용:
    python scripts/deploy.py            # 버전 확인 -> 빌드 -> 릴리스 생성/업로드
    python scripts/deploy.py --dry-run  # 릴리스 노트만 생성해 출력한다(빌드·업로드 없음)
    python scripts/deploy.py --force    # 아래 안전장치를 전부 무시한다

절차(OS 별 로컬 빌드 → 같은 태그에 에셋 추가, 2단계):
    0. pyproject.toml 의 [project].version(SSOT)을 미리 올려 둔다(이 스크립트가 대신
       올려주지 않는다). 그 태그의 릴리스가 이미 있으면 (a) 이 플랫폼의 에셋
       (releases.<channel>.json)이 이미 올라갔거나 (b) 그 태그가 최신 릴리스가 아니거나
       (c) 태그가 가리키는 커밋이 지금 HEAD 와 다르면 중단한다 — 셋 다 "버전을 올리는 걸
       잊었다"거나 "낡은 체크아웃에서 돌리고 있다"는 신호다(--force 로만 뚫린다).
       또한 릴리스 유무와 무관하게, 워킹 트리에 미커밋 변경이 있거나 HEAD 가 아직 push
       되지 않았으면 중단한다 — 빌드는 작업 트리의 파일을 담는데 태그는 커밋을 가리키므로,
       둘이 어긋나면 "어느 커밋에도 없는 코드"가 그 버전으로 배포된다.
    1. **첫 번째 OS**: scripts/build.py 로 그 OS 의 Velopack 설치기를 빌드하고, 이전 릴리스
       태그 이후의 git 커밋 로그를 `claude -p` 에 넘겨(지침: scripts/release_notes_guide.md)
       릴리스 노트를 생성한 뒤 `gh release create` 로 릴리스를 만들며 에셋을 올린다.
    2. **두 번째 OS**: 같은 커밋·같은 버전에서 이 스크립트를 다시 실행한다. 그 태그의
       릴리스가 이미 있으므로 노트를 다시 만들지 않고(`claude -p` 를 아예 호출하지 않는다)
       `gh release upload` 로 그 OS 의 에셋만 추가한다.

    두 번째 실행은 릴리스 노트를 만들지 않으므로, 1단계에서 만들어지는 노트가 **양쪽
    플랫폼을 모두** 설명해야 한다(지침은 scripts/release_notes_guide.md 에 있다).

    두 실행은 반드시 **같은 커밋**에서 해야 한다. 다른 커밋에서 두 번째 OS 를 빌드하면
    버전만 같고 내용이 다른 에셋이 한 릴리스에 섞인다. 그래서 릴리스가 이미 있는 경로에서는
    그 태그가 가리키는 커밋과 지금 HEAD 를 대조해 다르면 중단한다(--force 로만 뚫린다).

사전 준비:
    - scripts/build.py 와 동일:
        * Windows: VS C++ 빌드 도구, Velopack CLI(dotnet tool install -g vpk)
        * macOS: 전체 Xcode(App Store — Command Line Tools 만으로는 안 된다),
          CocoaPods(brew install cocoapods),
          Velopack CLI(dotnet tool install -g vpk — .NET SDK 필요)
    - uv.
    - gh CLI 로그인(`gh auth login`).
    - claude CLI 로그인(`claude login`) — 릴리스 노트 생성(첫 번째 OS)에만 필요하다.

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
from dataclasses import dataclass
from pathlib import Path

import platform_spec
from _common import REPO_ROOT, check, fail, info, pyproject_version

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


def _release_assets(tag: str) -> list[str] | None:
    """``tag`` 릴리스에 이미 올라간 에셋 '이름' 목록. 릴리스가 없으면 ``None``.

    "릴리스 없음"과 "조회 실패"를 반드시 구분한다. 네트워크·권한 실패를 릴리스 없음으로
    오해하면 두 번째 플랫폼 실행이 `gh release create` 로 넘어가는데, 그건 이미 있는
    태그에 실패하거나(운이 좋으면) 첫 플랫폼이 쓴 릴리스 노트를 망친다.
    """
    proc = subprocess.run(
        ["gh", "release", "view", tag, "--json", "assets"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "release not found" in stderr.lower():
            return None
        fail(f"gh release view {tag} 실패: {stderr}")
    data = json.loads(proc.stdout or "{}")
    return [a["name"] for a in data.get("assets", [])]


def _tag_commit(tag: str) -> str | None:
    """``tag`` 가 가리키는 커밋 SHA. 확인할 수 없으면 ``None``.

    로컬 git 이 아니라 GitHub 쪽에 묻는다. 첫 번째 OS 가 ``gh release create`` 로 만든
    태그는 두 번째 OS 의 로컬 저장소에 ``git fetch`` 전까지 존재하지 않아서, 로컬만 보면
    "태그를 못 찾음"을 "커밋이 다름"으로 오판한다. ``commits/<ref>`` 엔드포인트는 경량·주석
    태그를 모두 커밋으로 풀어 주므로 태그 종류를 따질 필요가 없다.
    """
    proc = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{tag}", "--jq", ".sha"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _head_commit() -> str | None:
    """지금 체크아웃된 커밋 SHA. 확인할 수 없으면 ``None``."""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _worktree_status() -> str | None:
    """``git status --porcelain`` 출력. 확인할 수 없으면 ``None``."""
    proc = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def _remote_has_commit(sha: str) -> bool:
    """``sha`` 가 GitHub 쪽에 존재하는가(= push 됐는가).

    로컬의 ``@{u}`` 는 ``git fetch`` 전이면 낡아 있어 믿을 수 없으므로 원격에 직접 묻는다.
    """
    proc = subprocess.run(
        ["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}", "--jq", ".sha"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def check_worktree_clean(porcelain_status: str | None, *, force: bool) -> str | None:
    """워킹 트리가 깨끗한지. 문제가 있으면 오류 메시지, 없으면 ``None``(순수 함수).

    빌드는 **작업 트리의 파일**을 그대로 번들에 담는데(flet 은 src/ 를 복사한다) 태그는
    커밋을 가리킨다. 그래서 미커밋 변경이 있는 채로 배포하면 "v0.1.5 라고 이름 붙었지만
    어느 커밋에도 존재하지 않는 코드"가 사용자에게 나가고, 나중에 그 버전을 재현할 수
    없다. 추적되지 않는 파일(untracked)도 똑같이 번들에 들어가므로 함께 막는다.

    ``plan_release`` 의 태그↔HEAD 대조는 **릴리스가 이미 있을 때만** 도는 가드라, 첫
    릴리스(create) 경로는 이 검사가 없으면 무방비다.
    """
    if force:
        return None
    if porcelain_status is None:
        return (
            "git status 를 확인하지 못했습니다 — 저장소 상태를 알 수 없는 채로 배포할 수 "
            "없습니다(정말 강행하려면 --force)."
        )
    dirty = [line for line in porcelain_status.splitlines() if line.strip()]
    if not dirty:
        return None
    shown = "\n".join(f"    {line}" for line in dirty[:10])
    more = f"\n    … 외 {len(dirty) - 10}개" if len(dirty) > 10 else ""
    return (
        "커밋되지 않은 변경이 있습니다 — 빌드 산출물에는 들어가지만 태그가 가리키는 "
        "커밋에는 없는 코드가 배포됩니다.\n"
        f"{shown}{more}\n"
        "  커밋(필요하면 push)한 뒤 다시 실행하세요(정말 강행하려면 --force)."
    )


def check_head_pushed(head_commit: str | None, *, remote_has_head: bool, force: bool) -> str | None:
    """HEAD 가 원격에 올라가 있는지. 문제가 있으면 오류 메시지, 없으면 ``None``(순수 함수).

    이 스크립트는 ``git push`` 를 하지 않는다(사용자의 브랜치를 말없이 밀어 올리는 건
    이 도구가 할 일이 아니다). 그런데 ``gh release create`` 는 태그를 만들 때 원격에 있는
    커밋만 가리킬 수 있으므로, push 하지 않은 채 배포하면 태그가 방금 빌드한 코드가 아닌
    엉뚱한 커밋(원격 기본 브랜치의 tip)을 가리키게 된다 — 그래서 여기서 먼저 막는다.
    실제로 태그를 HEAD 에 고정하는 것은 호출부가 넘기는 ``gh release create --target`` 이다.
    """
    if force:
        return None
    if head_commit is None:
        return "HEAD 커밋을 확인하지 못했습니다(git 저장소가 맞습니까?)."
    if not remote_has_head:
        return (
            f"현재 커밋({head_commit[:8]})이 GitHub 에 없습니다 — 먼저 push 하세요.\n"
            "  git push\n"
            "  (push 하지 않으면 릴리스 태그가 이 커밋을 가리킬 수 없습니다.)"
        )
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


@dataclass(frozen=True)
class ReleasePlan:
    """이번 실행이 릴리스를 새로 만들지(create), 에셋만 얹을지(append) 정한 결과."""

    mode: str            # "create" | "append"
    generate_notes: bool
    error: str | None = None


def plan_release(
    *,
    tag: str,
    prev_tag: str | None,
    existing_assets: list[str] | None,
    releases_json: str,
    force: bool,
    tag_commit: str | None,
    head_commit: str | None,
) -> ReleasePlan:
    """gh 조회 결과만 보고 이번 실행의 동작을 정한다(부수효과 없는 순수 함수).

    "버전 올리는 걸 잊었다" 가드를 없앤 게 아니라 **판정 근거를 셋으로 늘린 것**이다. OS 별로
    따로 빌드하면 두 번째 실행은 정상적으로도 "이전 릴리스 == 이번 태그" 상태가 되므로
    태그만 보고는 잊은 것인지 두 번째 플랫폼인지 구분할 수 없다. 그래서 릴리스가 이미 있는
    경로에서 아래 셋을 모두 본다(--force 는 전부 뚫는다):

    1. 그 릴리스에 내 채널의 releases.<channel>.json 이 이미 있는가 → 이미 배포됨.
       파일 이름에 채널이 박혀 있어(win/osx) 플랫폼별로 정확히 한 번씩만 통과한다.
    2. 이 태그가 **최신** 릴리스인가. 낡은 체크아웃에서 돌리면 과거 태그에 에셋이 붙고
       정작 latest 에는 그 OS 설치기가 영영 없는 상태가 된다(사용자가 latest 에서 받지 못한다).
    3. 태그가 가리키는 커밋 == 지금 HEAD 인가. 1·2 만으로는 **아직 한 번도 릴리스된 적 없는
       채널**(첫 macOS 배포가 정확히 이 경우다)에 가드가 하나도 걸리지 않는다 — 버전을 안
       올린 채 HEAD 에서 빌드하면 "v0.1.4" 라는 이름으로 v0.1.4 가 아닌 코드가 올라가고,
       그 OS 사용자의 업데이트 피드가 그것을 0.1.4 로 알린다.

    ``tag_commit``/``head_commit`` 은 조회 실패 시 ``None`` 이며, 그때는 대조가 불가능하므로
    통과시키지 않고 중단한다(모르는 채 올리는 것이 이 가드가 막으려는 바로 그 사고다).
    """
    if existing_assets is None:
        # 릴리스 자체가 없다. prev_tag == tag 는 정상적으로는 나올 수 없는 조합
        # (릴리스가 없는데 최신 릴리스 태그가 같을 수 없다)이지만, gh 조회가 어긋났을 때
        # 조용히 새 릴리스를 만들지 않도록 방어한다.
        if prev_tag == tag:
            return ReleasePlan(
                mode="create",
                generate_notes=False,
                error=(
                    f"버전이 이전 릴리스({tag})와 같습니다. "
                    "pyproject.toml 의 [project].version 을 올린 뒤 다시 실행하세요."
                ),
            )
        return ReleasePlan(mode="create", generate_notes=True, error=None)

    if releases_json in existing_assets and not force:
        return ReleasePlan(
            mode="append",
            generate_notes=False,
            error=(
                f"{tag} 릴리스에 이미 {releases_json} 이 올라가 있습니다 — "
                "이 플랫폼 에셋은 이미 배포됐습니다. "
                "pyproject.toml 의 버전을 올렸는지 확인하세요(덮어쓰려면 --force)."
            ),
        )

    if not force:
        # 낡은 체크아웃 방어. 정상적인 두 번째 OS 실행은 첫 OS 가 방금 만든 릴리스를 보므로
        # 반드시 tag == prev_tag 다. 다르면 과거 릴리스에 에셋을 붙이려는 중이다.
        if prev_tag is not None and tag != prev_tag:
            return ReleasePlan(
                mode="append",
                generate_notes=False,
                error=(
                    f"{tag} 는 최신 릴리스({prev_tag})가 아닙니다 — 오래된 커밋/버전에서 "
                    "실행 중입니다.\n"
                    "  git pull 로 최신 커밋을 받고 pyproject.toml 의 [project].version 을 "
                    "확인한 뒤 다시 실행하세요(의도한 재업로드면 --force)."
                ),
            )
        if tag_commit is None or head_commit is None:
            return ReleasePlan(
                mode="append",
                generate_notes=False,
                error=(
                    f"{tag} 태그의 커밋을 확인하지 못했습니다(gh/git 조회 실패). 첫 번째 OS 와 "
                    "같은 커밋인지 대조할 수 없어 중단합니다(직접 확인했다면 --force)."
                ),
            )
        if tag_commit != head_commit:
            return ReleasePlan(
                mode="append",
                generate_notes=False,
                error=(
                    f"{tag} 태그의 커밋({tag_commit[:12]})과 지금 HEAD({head_commit[:12]})가 "
                    "다릅니다 — 이대로 올리면 그 버전이 아닌 코드가 그 버전으로 배포됩니다.\n"
                    "  * 버전을 올리는 걸 잊었다면: pyproject.toml 의 [project].version 을 올리세요.\n"
                    f"  * 두 번째 OS 라면: 첫 번째 OS 와 같은 커밋에서 실행하세요(git checkout {tag}).\n"
                    "  (의도한 것이면 --force)"
                ),
            )

    # 다른 플랫폼이 같은 커밋에서 방금 만들어 둔 릴리스다(또는 --force 로 재실행).
    # 노트는 손대지 않는다.
    return ReleasePlan(mode="append", generate_notes=False, error=None)


def collect_assets(out_dir: Path, spec: platform_spec.PlatformSpec, version: str) -> list[Path]:
    """이번 플랫폼·이번 버전의 업로드 대상 파일 목록(setup → full → delta → releases json).

    글롭은 scripts/platform_spec.py 가 단일 소스로 정의한다. 여기서 다시 조립하면
    build.py 가 만든 이름과 어긋나 "빌드는 됐는데 업데이트가 안 되는" 조용한 실패가 난다.

    Raises:
        ValueError: 필수 산출물(setup, full nupkg, releases json)이 없을 때. delta 는
            첫 릴리스에 없는 게 정상이라 없어도 통과한다.
    """
    setups = sorted(out_dir.glob(spec.setup_glob))
    if not setups:
        raise ValueError(f"{out_dir} 에서 {spec.setup_glob} 를 찾지 못했습니다(빌드 실패?).")

    full_glob, delta_glob = spec.nupkg_globs(version)
    fulls = sorted(out_dir.glob(full_glob))
    if not fulls:
        raise ValueError(f"{out_dir} 에서 {full_glob} 를 찾지 못했습니다(빌드 실패?).")
    # vpk 는 델타 계산 기준으로 이전 버전 nupkg 를 같은 폴더에 내려받아 둔다. 글롭에 버전과
    # 채널이 모두 들어가 있어야 그것들이 섞여 올라가지 않는다.
    deltas = sorted(out_dir.glob(delta_glob))

    releases_json = out_dir / spec.releases_json
    if not releases_json.is_file():
        raise ValueError(f"{releases_json} 이 없습니다(빌드 실패?).")

    return [*setups, *fulls, *deltas, releases_json]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="릴리스 노트만 생성해 출력한다(빌드·gh 업로드는 하지 않는다).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="에셋 중복·최신 릴리스 아님·태그 커밋 불일치 검사를 모두 무시한다(재실행 복구용).",
    )
    args = parser.parse_args()

    _require_gh()

    version = pyproject_version()
    tag = f"v{version}"

    try:
        spec = platform_spec.current()
    except ValueError as exc:
        fail(f"이 OS 는 Velopack 배포 대상이 아닙니다: {exc}")

    existing = _release_assets(tag)
    prev_tag = _latest_release_tag()
    head_commit = _head_commit()
    # 커밋 대조는 릴리스가 이미 있을 때만 의미가 있다(없으면 태그 자체가 아직 없다).
    tag_commit = _tag_commit(tag) if existing is not None else None

    # --dry-run 은 빌드도 업로드도 하지 않으므로(릴리스 노트만 출력) 아래 두 가드를
    # 건너뛴다 — 노트를 미리 보려는 것뿐인데 커밋을 강요하면 쓸모가 없다.
    if not args.dry_run:
        error = check_worktree_clean(_worktree_status(), force=args.force)
        if error:
            fail(error)
        error = check_head_pushed(
            head_commit,
            remote_has_head=_remote_has_commit(head_commit) if head_commit else False,
            force=args.force,
        )
        if error:
            fail(error)

    plan = plan_release(
        tag=tag,
        prev_tag=prev_tag,
        existing_assets=existing,
        releases_json=spec.releases_json,
        force=args.force,
        tag_commit=tag_commit,
        head_commit=head_commit,
    )
    if plan.error:
        fail(plan.error)

    commit_log = ""
    if plan.generate_notes:
        info(f"{prev_tag or '(첫 릴리스)'} → {tag}")
        commit_log = _commit_log_since(prev_tag)
        if not commit_log:
            fail(f"{prev_tag} 이후 커밋이 없습니다 — 릴리스할 변경사항이 없습니다.")
    else:
        # 두 번째 플랫폼 실행. 커밋 로그는 계산조차 하지 않는다 — 같은 태그 이후 커밋은
        # 0개라 "릴리스할 변경사항이 없습니다" 로 무조건 죽는다.
        info(f"{tag} 릴리스가 이미 있습니다 → 릴리스 노트는 그대로 두고 {spec.channel} 에셋만 추가합니다.")

    if args.dry_run:
        if plan.generate_notes:
            notes = generate_release_notes(prev_tag, tag, commit_log)
            info("--dry-run: 빌드/업로드 없이 릴리스 노트만 출력합니다.")
            print(notes)
        else:
            info("--dry-run: 이미 있는 릴리스라 노트를 재생성하지 않습니다. 업로드할 에셋만 확인하세요.")
        return 0

    # 1) 빌드
    info("빌드 시작 (scripts/build.py)")
    check([sys.executable, str(REPO_ROOT / "scripts" / "build.py")])

    out_dir = platform_spec.VELOPACK_OUT
    try:
        assets = collect_assets(out_dir, spec, version)
    except ValueError as exc:
        fail(str(exc))
    asset_args = [str(p) for p in assets]

    # 2) GitHub 릴리스 생성 / 에셋 추가
    if plan.mode == "create":
        notes = generate_release_notes(prev_tag, tag, commit_log)
        notes_path = out_dir / "RELEASE_NOTES.md"
        notes_path.write_text(notes, encoding="utf-8")
        info(f"릴리스 노트: {notes_path}")

        info(f"GitHub 릴리스 생성/업로드: {tag}")
        # --target 으로 태그를 **방금 빌드한 커밋**에 고정한다. 넘기지 않으면 gh 는 원격
        # 기본 브랜치의 tip 에 태그를 만드는데, 그 사이 다른 커밋이 올라와 있으면 태그가
        # 배포된 산출물과 다른 코드를 가리킨다(위 check_head_pushed 참고).
        create_cmd = ["gh", "release", "create", tag, *asset_args,
                      "--title", tag, "--notes-file", str(notes_path)]
        if head_commit:
            create_cmd += ["--target", head_commit]
        check(create_cmd)
    else:
        # 노트 관련 옵션을 절대 넘기지 않는다 — 첫 플랫폼이 쓴 본문을 덮어쓰면 안 된다.
        # --clobber 는 업로드가 중간에 끊겨 같은 이름 에셋이 남았을 때 재실행 복구용.
        info(f"GitHub 릴리스에 {spec.channel} 에셋 추가: {tag}")
        check(["gh", "release", "upload", tag, *asset_args, "--clobber"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
