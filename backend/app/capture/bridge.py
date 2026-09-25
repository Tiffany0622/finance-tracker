"""Optional network-facing adapter: API + Telegram/OCR only, no DB or filesystem credentials."""

import logging
import os
import re
import signal
import threading
from typing import Any
from urllib.parse import urlsplit

from . import providers
from .transport import RemoteError, request_bytes, request_json

log = logging.getLogger("finance.capture_bridge")
stop = threading.Event()
MAX_IMAGE = 20 * 1024 * 1024


class Bridge:
    def __init__(self) -> None:
        self.api_url = os.environ.get(
            "CAPTURE_API_URL", "http://api:8000/api/v1/capture-bridge"
        ).rstrip("/")
        url = urlsplit(self.api_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"api", "localhost", "127.0.0.1"}
            or url.username
            or url.query
            or url.fragment
        ):
            raise RemoteError("api_endpoint_invalid")
        self.token = os.environ.get("CAPTURE_BRIDGE_TOKEN", "")
        if len(self.token) < 32:
            raise RemoteError("bridge_token_missing")
        self.telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.ollama_url = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
        self.openai_key = os.environ.get("OPENAI_API_KEY", "")

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token}

    def api(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return request_json(self.api_url + path, payload, self.headers, timeout=40)

    def telegram(self, method: str, payload: dict[str, Any]) -> Any:
        if method not in {"getMe", "getUpdates", "getFile", "sendMessage", "answerCallbackQuery"}:
            raise RemoteError("telegram_method_invalid")
        result = request_json(
            f"https://api.telegram.org/bot{self.telegram_token}/{method}", payload, timeout=45
        )
        if not isinstance(result, dict) or not result.get("ok"):
            raise RemoteError("telegram_failed")
        return result["result"]

    def download(self, file_id: str) -> bytes:
        result = self.telegram("getFile", {"file_id": file_id})
        path = result.get("file_path", "")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path or path.startswith("/"):
            raise RemoteError("telegram_file_invalid")
        if result.get("file_size", 0) > MAX_IMAGE:
            raise RemoteError("attachment_size")
        return request_bytes(
            f"https://api.telegram.org/file/bot{self.telegram_token}/{path}", limit=MAX_IMAGE
        )

    def perform(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        identity, token = job["id"], job["lease_token"]
        base = "/jobs/" + identity
        body: dict[str, Any] = {"lease_token": token}
        done = threading.Event()

        def beat() -> None:
            while not done.wait(job.get("heartbeat_seconds", 10)):
                try:
                    self.api(base + "/heartbeat", {"lease_token": token})
                except Exception:
                    return  # API checks lease again before accepting any result.

        thread = threading.Thread(target=beat, daemon=True)
        thread.start()
        try:
            try:
                if payload.get("skip"):
                    pass
                elif job["kind"] == "capture_parse":
                    if not payload["provider_enabled"]:
                        raise RemoteError("provider_changed")
                    image = (
                        request_bytes(
                            self.api_url + base + "/image",
                            headers={**self.headers, "X-Job-Lease": token},
                            limit=MAX_IMAGE,
                        )
                        if payload["has_image"]
                        else None
                    )
                    result = providers.parse(
                        payload["provider"],
                        payload["model"],
                        payload["text"],
                        image,
                        ollama_url=self.ollama_url,
                        openai_key=self.openai_key,
                        prompt_version=payload.get("prompt_version", 1),
                    )
                    body["parsed"] = result.model_dump(mode="json")
                elif job["kind"] == "telegram_download":
                    data = self.download(payload["file_id"])
                    request_bytes(
                        self.api_url + base + "/image",
                        method="POST",
                        data=data,
                        headers={
                            **self.headers,
                            "Content-Type": "application/octet-stream",
                            "X-Job-Lease": token,
                        },
                    )
                    return  # Publication + transition + lease completion are one API transaction.
                elif job["kind"] == "telegram_send":
                    self.telegram("sendMessage", payload)
                else:
                    raise RemoteError("job_kind_invalid")
            except RemoteError as error:
                body.update(error_code=error.code, retry_after=error.retry_after)
            self.api(base + "/result", body)
        finally:
            done.set()
            thread.join(timeout=2)

    def work(self, kinds: list[str]) -> None:
        while not stop.is_set():
            try:
                job = self.api("/claim", {"kinds": kinds})
                if job:
                    self.perform(job)
                else:
                    stop.wait(2)
            except Exception:
                log.warning("capture_job_connection_failed")
                stop.wait(5)

    def poll(self) -> None:
        while not stop.is_set():
            try:
                state = self.api("/cursor")
                me = self.telegram("getMe", {})
                if me["id"] != state["bot_id"]:
                    raise RemoteError("telegram_identity_mismatch")
                while not stop.is_set():
                    state.update(self.api("/cursor"))
                    if me["id"] != state["bot_id"]:
                        raise RemoteError("telegram_identity_mismatch")
                    updates = self.telegram(
                        "getUpdates",
                        {
                            "offset": state["next_offset"],
                            "timeout": 25,
                            "limit": 20,
                            "allowed_updates": ["message", "callback_query"],
                        },
                    )
                    for update in updates:
                        normalized = normalize(update, state["bot_id"])
                        if (
                            not normalized["private"]
                            or normalized["user_id"] != state["user_id"]
                            or normalized["chat_id"] != state["user_id"]
                        ):
                            normalized.update(
                                text="", callback="", file_id=None, filename="receipt"
                            )
                        # The next Telegram request acknowledges only after the API commits.
                        state.update(self.api("/updates", normalized))
                        callback = update.get("callback_query", {})
                        if callback.get("id") and state.get("accepted"):
                            try:
                                self.telegram(
                                    "answerCallbackQuery", {"callback_query_id": callback["id"]}
                                )
                            except RemoteError:
                                pass
            except RemoteError as error:
                log.warning("telegram_poll_failed code=%s", error.code)
                stop.wait(max(5, error.retry_after))
            except Exception:
                log.warning("telegram_poll_failed")
                stop.wait(5)


def normalize(update: dict[str, Any], bot_id: int) -> dict[str, Any]:
    callback = update.get("callback_query", {})
    message = callback.get("message") or update.get("message") or {}
    sender = callback.get("from") or message.get("from") or {}
    chat = message.get("chat") or {}
    file = message.get("document") or (message.get("photo") or [{}])[-1]
    return {
        "update_id": update["update_id"],
        "bot_id": bot_id,
        "user_id": sender.get("id"),
        "chat_id": chat.get("id"),
        "private": chat.get("type") == "private",
        "message_id": message.get("message_id"),
        "text": (message.get("text") or message.get("caption") or "")[:4096],
        "file_id": file.get("file_id"),
        "filename": (file.get("file_name") or "receipt.jpg")[:200],
        "callback": (callback.get("data") or "")[:64],
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    try:
        bridge = Bridge()
    except RemoteError as error:
        log.error("capture_configuration_error code=%s", error.code)
        return
    groups = [["capture_parse"]]
    if bridge.telegram_token:
        groups += [["telegram_download"], ["telegram_send"]]
    threads = [threading.Thread(target=bridge.work, args=(kinds,), daemon=True) for kinds in groups]
    if bridge.telegram_token:
        threads.append(threading.Thread(target=bridge.poll, daemon=True))
    for thread in threads:
        thread.start()
    while not stop.wait(1):
        pass
    for thread in threads:
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
