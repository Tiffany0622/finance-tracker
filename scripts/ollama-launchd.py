"""Install a local-only, memory-bounded Ollama service for Finance Tracker."""

import argparse
import os
import plistlib
import subprocess
from pathlib import Path

LABEL = "com.finance-tracker.ollama"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--binary", type=Path, default=Path("/Applications/Ollama.app/Contents/Resources/ollama")
    )
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if not args.binary.is_file():
        raise SystemExit("請先安裝官方 Ollama Mac 應用程式。")
    runtime = Path.home() / "Library/Application Support/FinanceTracker"
    logs = runtime / "logs"
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(args.binary.resolve()), "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "OLLAMA_HOST": "127.0.0.1:11434",
            "OLLAMA_NO_CLOUD": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_CONTEXT_LENGTH": "8192",
            "OLLAMA_KEEP_ALIVE": "2m",
        },
        "StandardOutPath": str(logs / "ollama.log"),
        "StandardErrorPath": str(logs / "ollama-error.log"),
    }
    output = Path(__file__).resolve().parents[1] / "data" / (LABEL + ".plist")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(plistlib.dumps(config))
    print("LaunchAgent 設定：" + str(output))
    if args.install:
        target = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
        if target.exists():
            previous = plistlib.loads(target.read_bytes())
            if previous.get("Label") != LABEL:
                raise SystemExit("既有 LaunchAgent 不屬於本專案，未覆寫。")
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}", str(target)],
                capture_output=True,
                check=False,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        target.write_bytes(plistlib.dumps(config))
        target.chmod(0o644)
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], check=True)
        print("Ollama 本機服務已啟用；登入後自動恢復，閒置 2 分鐘釋放模型記憶體。")


if __name__ == "__main__":
    main()
