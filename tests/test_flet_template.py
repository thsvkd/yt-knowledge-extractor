"""Windows 러너 패치(scripts/flet_template.py) 검증.

이 패치가 조용히 빠지면 배포본에서 (1) 설치기가 "설치가 부분적으로 성공했습니다" 경고를
띄우고 (2) 시작할 때 창 크기가 한 번 바뀌며 깜빡인다 — 둘 다 빌드해서 설치해 보기 전에는
알아채기 어려우므로, 패치가 적용되는지와 기준 문자열이 어긋나면 실패하는지를 검사한다.
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


class TestWindowSize(unittest.TestCase):
    def test_reads_gui_constants(self):
        """네이티브 러너의 첫 창 크기는 gui.py 의 상수(SSOT)에서 온다."""
        from yke import gui

        self.assertEqual(
            flet_template.window_size(), (gui._WINDOW_WIDTH, gui._WINDOW_HEIGHT)
        )


if __name__ == "__main__":
    unittest.main()
