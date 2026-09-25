"""Local AI opt-in checks using synthetic configuration and no real services."""

import contextlib
import importlib.util
import io
import json
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / (name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = load("enable-local-ai")
launcher = load("ollama-launchd")
SYNTHETIC_ENV = (
    "# synthetic test settings\nTELEGRAM_BOT_TOKEN='synthetic-private-token'\n"
    "CAPTURE_BRIDGE_TOKEN='synthetic-bridge-token'\nTELEGRAM_USER_ID='77'\n"
    "DATABASE_URL='synthetic-db'\nCAPTURE_PROVIDER='disabled'\n"
    "CAPTURE_MODEL=''\nCOMPOSE_PROFILES='capture'\n"
)


class LocalAISetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / ".env"
        self.env.write_text(SYNTHETIC_ENV)
        self.output = io.StringIO()
        self._redirect()
        self.root_patch = patch.object(setup, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def _redirect(self):
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def response(self, payload):
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.StringIO(json.dumps(payload)))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        return opener

    def test_local_model_apply_preserves_credentials_and_secure_file_mode(self):
        with (
            patch("sys.argv", ["setup", "--apply"]),
            patch.object(
                setup.urllib.request,
                "build_opener",
                return_value=self.response({"capabilities": ["vision"]}),
            ),
            patch.object(
                setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as run,
        ):
            setup.main()
        result = self.env.read_text()
        for line in SYNTHETIC_ENV.splitlines()[:5]:
            self.assertIn(line + "\n", result)
        self.assertIn("CAPTURE_PROVIDER='ollama'", result)
        self.assertIn("CAPTURE_MODEL='qwen3-vl:8b-instruct'", result)
        self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args.args[0][-1], "web")
        self.assertIn("--force-recreate", run.call_args.args[0])
        self.assertIn("--no-deps", run.call_args.args[0])
        self.assertNotIn("synthetic-private-token", self.output.getvalue())
        self.assertEqual(list(self.root.glob(".capture-env-*")), [])

    def test_probe_does_not_write_or_restart(self):
        with (
            patch("sys.argv", ["setup"]),
            patch.object(
                setup.urllib.request,
                "build_opener",
                return_value=self.response({"capabilities": ["vision"]}),
            ),
            patch.object(
                setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
            ) as run,
        ):
            setup.main()
        self.assertEqual(self.env.read_text(), SYNTHETIC_ENV)
        self.assertEqual(run.call_count, 1)

    def test_remote_or_nonvision_model_never_changes_settings(self):
        for model in [
            {"capabilities": ["completion"]},
            {"capabilities": ["vision"], "remote_host": "https://example.com"},
        ]:
            with (
                self.subTest(model=model),
                patch("sys.argv", ["setup", "--apply"]),
                patch.object(
                    setup.urllib.request, "build_opener", return_value=self.response(model)
                ),
                patch.object(setup.subprocess, "run") as run,
                self.assertRaises(SystemExit),
            ):
                setup.main()
            run.assert_not_called()
            self.assertEqual(self.env.read_text(), SYNTHETIC_ENV)

    def test_network_failure_preserves_settings_and_hides_diagnostics(self):
        for result in [
            subprocess.CompletedProcess([], 1, "", "private-detail"),
            subprocess.TimeoutExpired("private-detail", 15),
        ]:
            with (
                self.subTest(result=type(result)),
                patch("sys.argv", ["setup", "--apply"]),
                patch.object(
                    setup.urllib.request,
                    "build_opener",
                    return_value=self.response({"capabilities": ["vision"]}),
                ),
                patch.object(
                    setup.subprocess,
                    "run",
                    **(
                        {"side_effect": result}
                        if isinstance(result, Exception)
                        else {"return_value": result}
                    ),
                ),
                self.assertRaises(SystemExit) as caught,
            ):
                setup.main()
            self.assertEqual(self.env.read_text(), SYNTHETIC_ENV)
            self.assertNotIn("private-detail", str(caught.exception) + self.output.getvalue())

    def test_cloud_and_invalid_tags_fail_before_network(self):
        for model in ["qwen3-vl:cloud", "model\nKEY=value", "model'break"]:
            with (
                self.subTest(model=model),
                patch("sys.argv", ["setup", "--model", model, "--apply"]),
                patch.object(setup.urllib.request, "build_opener") as request,
                self.assertRaises(SystemExit),
            ):
                setup.main()
            request.assert_not_called()
        self.assertIsNone(
            setup.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")
        )

    def test_launch_service_is_native_local_and_cloud_disabled(self):
        binary = self.root / "Ollama.app/Contents/Resources/ollama"
        binary.parent.mkdir(parents=True)
        binary.write_text("synthetic binary")
        source = self.root / "repo/scripts/ollama-launchd.py"
        source.parent.mkdir(parents=True)
        with (
            patch.object(launcher, "__file__", str(source)),
            patch.object(launcher.Path, "home", return_value=self.root),
            patch("sys.argv", ["setup", "--binary", str(binary), "--install"]),
            patch.object(launcher.subprocess, "run") as run,
        ):
            launcher.main()
        installed = self.root / "Library/LaunchAgents/com.finance-tracker.ollama.plist"
        config = plistlib.loads(installed.read_bytes())
        self.assertEqual(config["ProgramArguments"], [str(binary.resolve()), "serve"])
        self.assertEqual(config["EnvironmentVariables"]["OLLAMA_HOST"], "127.0.0.1:11434")
        self.assertEqual(config["EnvironmentVariables"]["OLLAMA_NO_CLOUD"], "1")
        self.assertTrue(config["RunAtLoad"] and config["KeepAlive"])
        self.assertEqual(run.call_args.args[0][1], "bootstrap")


if __name__ == "__main__":
    unittest.main()
