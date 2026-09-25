"""Synthetic setup checks; no database, real Bot, or user credentials."""

import contextlib
import getpass
import importlib.util
import io
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import warnings
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import capture_setup_helpers as setup

SPEC = importlib.util.spec_from_file_location(
    "configure_capture", Path(__file__).resolve().parents[1] / "configure-capture.py"
)
wizard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wizard)

TOKEN = "12345:" + "synthetic_token_only_" * 2
BOT = {"id": 12345, "is_bot": True, "username": "synthetic_test_bot"}


class CaptureSetupTests(unittest.TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.output = contextlib.redirect_stdout(self.stdout)
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)

    def response(self, payload):
        response = Mock()
        response.read.return_value = json.dumps(payload).encode()
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        return opener

    def test_single_account_never_asks_for_username(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(["alice"]), "")
        with (
            patch.object(setup.subprocess, "run", return_value=result) as run,
            patch("builtins.input") as ask,
        ):
            self.assertEqual(setup.select_ledger("docker", Path("/tmp")), "alice")
        ask.assert_not_called()
        compile(run.call_args.args[0][-1], "<account-query>", "exec")
        self.assertNotIn("Token", self.stdout.getvalue())

    def test_account_failure_stops_without_echoing_command_output(self):
        result = subprocess.CompletedProcess([], 1, "", "private diagnostic")
        with patch.object(setup.subprocess, "run", return_value=result):
            with self.assertRaises(setup.SetupError) as caught:
                setup.select_ledger("docker", Path("/tmp"))
        self.assertNotIn("private diagnostic", str(caught.exception))

    def test_account_choice_hides_accidental_secret(self):
        result = subprocess.CompletedProcess([], 0, json.dumps(["alice", "bob"]), "")
        with (
            patch.object(setup.subprocess, "run", return_value=result),
            patch.object(setup, "read_secret", return_value=TOKEN),
        ):
            with self.assertRaises(setup.SetupError) as caught:
                setup.select_ledger("docker", Path("/tmp"))
        self.assertNotIn(TOKEN, str(caught.exception) + self.stdout.getvalue())

    def test_no_echo_fallback_for_secrets(self):
        def unsuitable_terminal(prompt):
            warnings.warn("Cannot hide input", getpass.GetPassWarning, stacklevel=2)
            self.fail("Must stop before fallback input")

        with patch.object(setup.getpass, "getpass", side_effect=unsuitable_terminal):
            with self.assertRaisesRegex(setup.SetupError, "無法隱藏輸入"):
                setup.read_secret("Token: ")

    def test_bot_verification_and_fixed_https_endpoint(self):
        opener = self.response({"ok": True, "result": BOT})
        with patch.object(setup.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(setup.verify_bot(TOKEN), BOT)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.full_url, "https://api.telegram.org/bot" + TOKEN + "/getMe")
        self.assertIsNone(
            setup.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")
        )

    def test_bad_token_format_never_sends_request(self):
        with patch.object(setup.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(setup.SetupError, "Token 格式"):
                setup.verify_bot("copy this: " + TOKEN)
        opener.assert_not_called()

    def test_http_errors_are_specific_and_never_contain_secret(self):
        for status, expected in [
            (401, "Token"),
            (404, "Token"),
            (409, "其他程序"),
            (429, "限制"),
            (302, "重新導向"),
            (500, "HTTP 500"),
        ]:
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://api.telegram.org/bot" + TOKEN,
                    status,
                    TOKEN,
                    {},
                    io.BytesIO(TOKEN.encode()),
                )
                with patch.object(setup.urllib.request, "build_opener") as build:
                    build.return_value.open.side_effect = error
                    with self.assertRaisesRegex(setup.SetupError, expected) as caught:
                        setup.verify_bot(TOKEN)
                self.assertNotIn(TOKEN, str(caught.exception))

    def test_network_errors_are_distinguished_without_secret(self):
        for reason, expected in [
            (ssl.SSLCertVerificationError(TOKEN), "憑證"),
            (socket.gaierror(TOKEN), "DNS"),
            (OSError(TOKEN), "無法連線"),
        ]:
            with (
                self.subTest(expected=expected),
                patch.object(setup.urllib.request, "build_opener") as build,
            ):
                build.return_value.open.side_effect = urllib.error.URLError(reason)
                with self.assertRaisesRegex(setup.SetupError, expected) as caught:
                    setup.verify_bot(TOKEN)
                self.assertNotIn(TOKEN, str(caught.exception))
        with patch.object(setup.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = TimeoutError(TOKEN)
            with self.assertRaisesRegex(setup.SetupError, "逾時"):
                setup.verify_bot(TOKEN)

    def test_invalid_or_oversize_result_rejected(self):
        for payload in [
            {"ok": False, "description": TOKEN},
            {"ok": True, "result": {**BOT, "is_bot": False}},
            {"ok": True, "result": {**BOT, "username": TOKEN}},
        ]:
            with patch.object(
                setup.urllib.request, "build_opener", return_value=self.response(payload)
            ):
                with self.assertRaises(setup.SetupError) as caught:
                    setup.verify_bot(TOKEN)
                self.assertNotIn(TOKEN, str(caught.exception))
        opener = self.response({})
        opener.open.return_value.__enter__.return_value.read.return_value = b"x" * (1024 * 1024 + 1)
        with patch.object(setup.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(setup.SetupError, "大小限制"):
                setup.verify_bot(TOKEN)

    def test_pairing_requires_exact_code_private_chat_and_matching_user(self):
        good = {
            "text": "/start finance_challenge",
            "from": {"id": 77, "is_bot": False},
            "chat": {"id": 77, "type": "private"},
        }
        invalid = [
            {**good, "text": "/start"},
            {**good, "text": "/start finance_old_code"},
            {**good, "chat": {"id": 77, "type": "group"}},
            {**good, "chat": {"id": 88, "type": "private"}},
            {**good, "from": {"id": 77, "is_bot": True}},
            {**good, "forward_origin": {"type": "user"}},
        ]
        with (
            patch.object(setup.secrets, "token_urlsafe", return_value="challenge"),
            patch.object(
                setup,
                "telegram_call",
                side_effect=[[{"message": x} for x in invalid], [{"message": good}]],
            ) as call,
            patch.object(setup.time, "sleep"),
        ):
            self.assertEqual(setup.pair_user(TOKEN, BOT["username"]), 77)
        self.assertEqual(call.call_count, 2)
        for args in call.call_args_list:
            self.assertEqual(args.args[2], {"offset": 0, "limit": 100, "timeout": 15})

    def test_pairing_expiry_and_full_queue_do_not_acknowledge_updates(self):
        with (
            patch.object(setup.time, "monotonic", side_effect=[0, 181]),
            patch.object(setup, "telegram_call") as call,
        ):
            with self.assertRaisesRegex(setup.SetupError, "配對逾時"):
                setup.pair_user(TOKEN, BOT["username"])
            call.assert_not_called()
        with patch.object(
            setup, "telegram_call", return_value=[{"update_id": i} for i in range(100)]
        ) as call:
            with self.assertRaisesRegex(setup.SetupError, "保留訊息"):
                setup.pair_user(TOKEN, BOT["username"])
            call.assert_called_once()
            self.assertEqual(call.call_args.args[2]["offset"], 0)

    def test_failed_verification_never_changes_env_or_registers_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / ".env"
            env.write_text("EXISTING=preserved\n")
            with (
                patch.object(wizard, "ROOT", root),
                patch.object(wizard, "select_ledger", return_value="alice"),
                patch.object(wizard, "read_secret", return_value=TOKEN),
                patch("builtins.input", return_value=""),
                patch.object(wizard, "verify_bot", side_effect=setup.SetupError("Token 無效")),
                patch.object(wizard.subprocess, "run") as run,
            ):
                with self.assertRaises(setup.SetupError):
                    wizard.main()
            run.assert_not_called()
            self.assertEqual(env.read_text(), "EXISTING=preserved\n")

    def test_confirmed_setup_preserves_env_and_writes_private_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = root / ".env"
            env.write_text("EXISTING=preserved\nTELEGRAM_BOT_TOKEN=old-synthetic\n")
            with (
                patch.object(wizard, "ROOT", root),
                patch.object(wizard.Path, "home", return_value=root),
                patch.object(wizard, "select_ledger", return_value="alice"),
                patch.object(wizard, "read_secret", side_effect=[TOKEN, ""]),
                patch("builtins.input", side_effect=["", "yes"]),
                patch.object(wizard, "verify_bot", return_value=BOT),
                patch.object(wizard, "pair_user", return_value=77),
                patch.object(
                    wizard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
                ) as run,
            ):
                wizard.main()
            content = env.read_text()
            self.assertIn("EXISTING=preserved\n", content)
            self.assertIn("TELEGRAM_USER_ID='77'", content)
            self.assertIn("CAPTURE_PROVIDER='disabled'", content)
            self.assertNotIn("old-synthetic", content)
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(TOKEN, self.stdout.getvalue())
            self.assertEqual(run.call_args_list[0].args[0][-2:], ["--username", "alice"])
            for call in run.call_args_list:
                self.assertNotIn(TOKEN, str(call.args))


if __name__ == "__main__":
    unittest.main()
