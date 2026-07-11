"""claude_client(ClaudeClient, `claude -p` subprocess 래퍼) 특성화 테스트."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from yke.llm import claude_client


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> mock.Mock:
    return mock.Mock(stdout=stdout, stderr=stderr, returncode=returncode)


class TestIsAvailable(unittest.TestCase):
    def test_true_when_bin_found(self):
        with mock.patch.object(claude_client, "_CLAUDE_BIN", "C:/fake/claude.exe"):
            self.assertTrue(claude_client.is_available())

    def test_false_when_bin_missing(self):
        with mock.patch.object(claude_client, "_CLAUDE_BIN", None):
            self.assertFalse(claude_client.is_available())


class TestClaudeClientInit(unittest.TestCase):
    def test_raises_when_cli_missing(self):
        with mock.patch.object(claude_client, "_CLAUDE_BIN", None):
            with self.assertRaises(RuntimeError):
                claude_client.ClaudeClient()

    def test_ok_when_cli_found(self):
        with mock.patch.object(claude_client, "_CLAUDE_BIN", "C:/fake/claude.exe"):
            claude_client.ClaudeClient()  # 예외 없이 통과


class TestComplete(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(claude_client, "_CLAUDE_BIN", "C:/fake/claude.exe")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = claude_client.ClaudeClient()

    def test_success_returns_result_text(self):
        payload = json.dumps({"result": "[1,2,3]", "stop_reason": "end_turn", "is_error": False})
        with mock.patch.object(
            claude_client.subprocess, "run", return_value=_completed(stdout=payload)
        ):
            out = self.client.complete("sys", "user", model="m")
        self.assertEqual(out, "[1,2,3]")

    def test_command_shape_and_stdin(self):
        payload = json.dumps({"result": "ok", "stop_reason": "end_turn", "is_error": False})
        with mock.patch.object(
            claude_client.subprocess, "run", return_value=_completed(stdout=payload)
        ) as mock_run:
            self.client.complete("SYS PROMPT", "USER MSG", model="claude-haiku-4-5-20251001")
        args, kwargs = mock_run.call_args
        cmd = args[0]
        self.assertEqual(cmd[:2], ["C:/fake/claude.exe", "-p"])
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-haiku-4-5-20251001")
        self.assertIn("--system-prompt", cmd)
        self.assertEqual(cmd[cmd.index("--system-prompt") + 1], "SYS PROMPT")
        self.assertEqual(kwargs.get("input"), "USER MSG")  # user 는 stdin 으로 전달(인자 길이 제한 회피)

    def test_truncation_warns_but_returns_text(self):
        payload = json.dumps({"result": "partial", "stop_reason": "max_tokens", "is_error": False})
        with mock.patch.object(
            claude_client.subprocess, "run", return_value=_completed(stdout=payload)
        ):
            with mock.patch("builtins.print") as mock_print:
                out = self.client.complete("sys", "user", model="m")
        self.assertEqual(out, "partial")
        self.assertTrue(mock_print.called)

    def test_nonzero_exit_with_error_json_raises_with_result_detail(self):
        payload = json.dumps({"result": "model not found", "is_error": True})
        with mock.patch.object(
            claude_client.subprocess,
            "run",
            return_value=_completed(stdout=payload, returncode=1),
        ):
            with self.assertRaisesRegex(RuntimeError, "model not found"):
                self.client.complete("sys", "user", model="bogus")

    def test_nonzero_exit_with_unparsable_stdout_uses_stderr(self):
        with mock.patch.object(
            claude_client.subprocess,
            "run",
            return_value=_completed(stdout="not json", stderr="boom", returncode=2),
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.client.complete("sys", "user", model="m")

    def test_is_error_true_with_zero_exit_raises(self):
        payload = json.dumps({"result": "refused", "is_error": True})
        with mock.patch.object(
            claude_client.subprocess, "run", return_value=_completed(stdout=payload, returncode=0)
        ):
            with self.assertRaisesRegex(RuntimeError, "refused"):
                self.client.complete("sys", "user", model="m")

    def test_unparsable_stdout_zero_exit_raises(self):
        with mock.patch.object(
            claude_client.subprocess, "run", return_value=_completed(stdout="garbage", returncode=0)
        ):
            with self.assertRaises(RuntimeError):
                self.client.complete("sys", "user", model="m")

    def test_timeout_raises_runtime_error(self):
        with mock.patch.object(
            claude_client.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1),
        ):
            with self.assertRaises(RuntimeError):
                self.client.complete("sys", "user", model="m")


if __name__ == "__main__":
    unittest.main()
