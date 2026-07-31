"""네이티브 러너 패치(scripts/flet_template.py) 검증.

이 패치가 조용히 빠지면 배포본에서 (1) Windows 설치기가 "설치가 부분적으로 성공했습니다"
경고를 띄우고 (2) 시작할 때 창 크기가 한 번 바뀌며 깜빡인다 — 둘 다 빌드해서 설치해 보기
전에는 알아채기 어려우므로, 패치가 적용되는지와 기준 문자열이 어긋나면 실패하는지를
검사한다. macOS 는 창 크기만 패치한다(Velopack 훅은 `open` 으로 인자 없이 실행되므로
flet 의 '인자가 있으면 개발자 모드' 분기에 걸리지 않는다).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import flet_template  # noqa: E402 - scripts/ 를 sys.path 에 넣은 뒤에만 import 가능

# flet 0.85 빌드 템플릿의 windows/runner/main.cpp 에서 패치와 관련된 부분만 발췌한 것.
_TEMPLATE_MAIN_CPP = """\
#include <flutter/dart_project.h>
#include <flutter/flutter_view_controller.h>
#include <windows.h>

#include "flutter_window.h"
#include "utils.h"

int APIENTRY wWinMain(_In_ HINSTANCE instance, _In_opt_ HINSTANCE prev,
                      _In_ wchar_t *command_line, _In_ int show_command) {
  ::CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

  flutter::DartProject project(L"data");

  FlutterWindow window(project);
  Win32Window::Point origin(10, 10);
  Win32Window::Size size(1280, 720);
  if (!window.Create(L"{{ cookiecutter.product_name }}", origin, size)) {
    return EXIT_FAILURE;
  }

  ::CoUninitialize();
  return EXIT_SUCCESS;
}
"""


# flet 0.85 빌드 템플릿의 macos/Runner/Base.lproj/MainMenu.xib 에서 패치와 관련된 부분만
# 발췌한 것 + 함정 재현용 rect 한 줄. 실제 템플릿에는 `width="800" height="600"` 이 아래 두
# 곳(창의 contentRect, contentView 의 frame)에만 있지만, 그 부분 문자열을 앵커로 쓰면
# 템플릿에 같은 크기의 rect 가 하나만 더 늘어도 패치가 엉뚱한 줄을 건드리거나 조용히
# 어긋난다. 그래서 세 번째 rect 를 일부러 넣어 "앵커가 줄 단위로 유일한가" 를 강제한다.
_TEMPLATE_MAIN_MENU_XIB = """\
<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.Cocoa.XIB" version="3.0">
    <objects>
        <window title="APP_NAME" id="QvC-M9-y7g" customClass="MainFlutterWindow" customModule="Runner">
            <windowStyleMask key="styleMask" titled="YES" closable="YES" resizable="YES"/>
            <rect key="contentRect" x="335" y="390" width="800" height="600"/>
            <rect key="screenRect" x="0.0" y="0.0" width="2560" height="1577"/>
            <view key="contentView" wantsLayer="YES" id="EiT-Mj-1SZ">
                <rect key="frame" x="0.0" y="0.0" width="800" height="600"/>
                <autoresizingMask key="autoresizingMask"/>
            </view>
        </window>
        <customView id="Zzz-00-aaa">
            <rect key="frame" x="10" y="20" width="800" height="600"/>
        </customView>
    </objects>
</document>
"""


class TestPatchWindowsRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.patched = flet_template.patch_windows_runner(
            _TEMPLATE_MAIN_CPP, width=820, height=860
        )

    def test_exits_early_on_velopack_hook(self):
        """Velopack 훅 인자면 Flutter 엔진(창 생성)보다 먼저 성공 종료해야 한다."""
        self.assertIn('::wcsstr(command_line, L"--veloapp-")', self.patched)
        self.assertLess(
            self.patched.index("--veloapp-"),
            self.patched.index("FlutterWindow window(project)"),
            "훅 처리는 창을 만들기 전에 있어야 한다",
        )

    def test_declares_wcsstr(self):
        self.assertIn("#include <wchar.h>", self.patched)

    def test_initial_window_size_matches_gui(self):
        self.assertIn("Win32Window::Size size(820, 860);", self.patched)
        self.assertNotIn("1280, 720", self.patched)

    def test_rest_of_template_is_untouched(self):
        # 패치는 추가/치환만 한다 — 원본의 다른 줄이 사라지면 안 된다.
        for line in ("FlutterWindow window(project);", "return EXIT_FAILURE;"):
            self.assertIn(line, self.patched)

    def test_fails_loudly_when_anchor_is_missing(self):
        """flet 이 템플릿을 바꾸면 조용히 넘어가지 말고 빌드를 실패시켜야 한다."""
        changed = _TEMPLATE_MAIN_CPP.replace(
            "Win32Window::Size size(1280, 720);", "Win32Window::Size size(1024, 768);"
        )
        with self.assertRaises(ValueError):
            flet_template.patch_windows_runner(changed, width=820, height=860)

    def test_is_idempotent_guard(self):
        """이미 패치된 소스에 다시 적용하면(앵커가 사라져) 실패해야 한다."""
        with self.assertRaises(ValueError):
            flet_template.patch_windows_runner(self.patched, width=820, height=860)


class TestPatchMacosRunner(unittest.TestCase):
    """macOS 첫 창 크기 패치.

    macOS 러너의 창 크기는 C++ 코드가 아니라 XIB(Interface Builder 문서)에 박혀 있어서,
    패치하지 않으면 창이 800x600 으로 먼저 뜬 뒤 파이썬이 붙고 나서야 앱 크기로 바뀐다
    (Windows 와 같은 깜빡임). 창 크기의 SSOT 는 src/yke/gui.py 다.
    """

    def setUp(self) -> None:
        self.patched = flet_template.patch_macos_runner(
            _TEMPLATE_MAIN_MENU_XIB, width=820, height=860
        )

    def test_both_window_rects_are_resized(self):
        # 창 프레임(contentRect)과 그 안의 contentView frame 이 함께 바뀌어야 한다.
        # 하나만 바꾸면 뷰가 창보다 크거나 작은 채로 첫 프레임이 그려진다.
        self.assertNotIn('<rect key="contentRect" x="335" y="390" width="800" height="600"/>', self.patched)
        self.assertNotIn('<rect key="frame" x="0.0" y="0.0" width="800" height="600"/>', self.patched)
        self.assertEqual(self.patched.count('width="820" height="860"'), 2)

    def test_unrelated_rect_is_left_alone(self):
        """같은 800x600 이어도 창과 무관한 rect 는 건드리면 안 된다(앵커는 줄 단위로 유일)."""
        self.assertIn('<rect key="frame" x="10" y="20" width="800" height="600"/>', self.patched)

    def test_rest_of_template_is_untouched(self):
        for line in ('customClass="MainFlutterWindow"', '<rect key="screenRect"'):
            self.assertIn(line, self.patched)

    def test_fails_loudly_when_anchor_is_missing(self):
        """flet 이 템플릿을 바꾸면 조용히 넘어가지 말고 빌드를 실패시켜야 한다."""
        changed = _TEMPLATE_MAIN_MENU_XIB.replace(
            '<rect key="contentRect" x="335" y="390" width="800" height="600"/>',
            '<rect key="contentRect" x="100" y="100" width="1024" height="768"/>',
        )
        with self.assertRaises(ValueError):
            flet_template.patch_macos_runner(changed, width=820, height=860)

    def test_is_idempotent_guard(self):
        """이미 패치된 XIB 에 다시 적용하면(앵커가 사라져) 실패해야 한다."""
        with self.assertRaises(ValueError):
            flet_template.patch_macos_runner(self.patched, width=820, height=860)


class TestPatchRevisionBumped(unittest.TestCase):
    def test_revision_is_at_least_two(self):
        """macOS 패치를 추가하면서 _PATCH_REVISION 을 올렸는지 확인한다.

        flet build 는 템플릿의 '내용'이 아니라 경로/버전만 해시해 Flutter 프로젝트 재생성
        여부를 정한다(cache_dir_name 이 리비전을 경로에 넣는 이유). 패치를 늘렸는데 리비전을
        그대로 두면 이미 캐시된 build/_flet_template 을 재사용해 **macOS 패치가 빠진 채로**
        조용히 빌드된다 — 빌드는 성공하고 증상만 남으므로 여기서 잡는다.
        """
        self.assertGreaterEqual(flet_template._PATCH_REVISION, 2)


class TestCacheDirName(unittest.TestCase):
    """패치를 고쳤는데 flet 이 옛 Flutter 프로젝트를 재사용하는 사고를 막는 불변식."""

    def test_includes_patch_revision_and_window_size(self):
        # flet 은 템플릿 '경로'만 해시하므로, 패치 리비전·창 크기가 경로에 없으면 패치를
        # 고쳐도 build/flutter 가 재생성되지 않아 옛 main.cpp 로 빌드된다.
        name = flet_template.cache_dir_name("0.85.3", width=820, height=860)
        self.assertIn("0.85.3", name)
        self.assertIn(f"r{flet_template._PATCH_REVISION}", name)
        self.assertIn("820x860", name)

    def test_differs_when_patch_or_size_changes(self):
        base = flet_template.cache_dir_name("0.85.3", width=820, height=860)
        self.assertNotEqual(base, flet_template.cache_dir_name("0.85.3", width=900, height=860))
        self.assertNotEqual(base, flet_template.cache_dir_name("0.86.0", width=820, height=860))


class TestWindowSize(unittest.TestCase):
    def test_reads_gui_constants(self):
        """네이티브 러너의 첫 창 크기는 gui.py 의 상수(SSOT)에서 온다."""
        from yke import gui

        self.assertEqual(
            flet_template.window_size(), (gui._WINDOW_WIDTH, gui._WINDOW_HEIGHT)
        )


if __name__ == "__main__":
    unittest.main()
