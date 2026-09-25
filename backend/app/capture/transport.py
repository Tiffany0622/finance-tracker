"""Bounded HTTP transport. Never include remote bodies, URLs or tokens in errors/logs."""

import json
import urllib.error
import urllib.request
from typing import Any


class RemoteError(Exception):
    def __init__(self, code: str, retry_after: int = 0):
        super().__init__(code)
        self.code = code
        self.retry_after = max(0, min(retry_after, 86400))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        return None


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
    limit: int = 2 * 1024 * 1024,
) -> bytes:
    opener = urllib.request.build_opener(NoRedirect, urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with opener.open(req, timeout=timeout) as response:
            content = response.read(limit + 1)
            if len(content) > limit:
                raise RemoteError("response_too_large")
            return bytes(content)
    except urllib.error.HTTPError as error:
        retry = 0
        if error.code == 429:
            value = error.headers.get("Retry-After", "0")
            retry = int(value) if value.isdecimal() else 0
            # Telegram places retry_after in the JSON error body.
            try:
                body = json.loads(error.read(8192))
                retry = max(retry, int(body.get("parameters", {}).get("retry_after", 0)))
            except (ValueError, TypeError, AttributeError):
                pass
        code = (
            "rate_limited"
            if error.code == 429
            else "remote_unauthorized"
            if error.code in {401, 403}
            else "remote_unavailable"
        )
        raise RemoteError(code, retry) from None
    except (OSError, TimeoutError, urllib.error.URLError):
        raise RemoteError("remote_unavailable") from None


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 120,
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    content = request_bytes(
        url,
        method="POST" if data is not None else "GET",
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        timeout=timeout,
    )
    try:
        return json.loads(content)
    except (ValueError, UnicodeError):
        raise RemoteError("response_invalid") from None
