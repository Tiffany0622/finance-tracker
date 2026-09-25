"""Enable an already-downloaded local vision model while preserving Telegram credentials."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3-vl:8b-instruct")
    parser.add_argument(
        "--apply", action="store_true", help="Apply after checking local connectivity"
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,100}", args.model) or "cloud" in args.model.lower():
        raise SystemExit("請指定已下載的本機模型標籤，不使用 cloud 模型。")
    env_path = ROOT / ".env"
    if not env_path.is_file():
        raise SystemExit("請先完成帳本與 Telegram 設定，再啟用本機 AI。")
    docker = shutil.which("docker") or str(Path.home() / ".docker/bin/docker")
    try:
        request = urllib.request.Request(
            "http://127.0.0.1:11434/api/show",
            data=json.dumps({"model": args.model}).encode(),
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            model = json.load(response)
        if "vision" not in model.get("capabilities", []) or model.get("remote_host"):
            raise ValueError("Not a local vision model")
    except Exception:
        raise SystemExit(
            "本機視覺模型未就緒。請啟動 Ollama 並先下載指定模型。設定未變更。"
        ) from None
    # Probe the exact network path used by the bridge; never open an unauthenticated LAN port.
    try:
        probe = subprocess.run(
            [
                docker,
                "compose",
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--entrypoint",
                "python",
                "capture-bridge",
                "-c",
                "import urllib.request; "
                "o=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
                "r=o.open('http://host.docker.internal:11434/api/version',timeout=5); "
                "assert r.status==200",
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("無法完成 Docker 連線檢查；設定未變更。") from None
    if probe.returncode:
        raise SystemExit("整合容器無法連到本機 Ollama。請先修復連線，設定未變更。")
    print("本機視覺模型與 Docker 連線檢查通過：" + args.model)
    if not args.apply:
        print("尚未變更設定；加上 --apply 才啟用。")
        return
    updates = {
        "CAPTURE_PROVIDER": "ollama",
        "CAPTURE_MODEL": args.model,
        "OLLAMA_URL": "http://host.docker.internal:11434",
        "COMPOSE_PROFILES": "capture",
    }
    lines = [
        line for line in env_path.read_text().splitlines() if line.split("=", 1)[0] not in updates
    ]
    lines += [key + "='" + value + "'" for key, value in updates.items()]
    fd, temp = tempfile.mkstemp(prefix=".capture-env-", dir=ROOT)
    try:
        with os.fdopen(fd, "w") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, env_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    subprocess.run([docker, "compose", "up", "-d", "--no-build", "--wait"], cwd=ROOT, check=True)
    # Nginx resolves the API hostname at startup; a recreated API may have a new IP.
    subprocess.run(
        [
            docker,
            "compose",
            "up",
            "-d",
            "--no-build",
            "--force-recreate",
            "--no-deps",
            "--wait",
            "web",
        ],
        cwd=ROOT,
        check=True,
    )
    print("本機 AI 辨識已啟用；Telegram 金鑰與私聊配對沿用原設定。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("已取消。") from None
