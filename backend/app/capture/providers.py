import base64
import json
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from .prompts import PROMPT_VERSION, receipt_prompt
from .schemas import ParsedReceipt
from .transport import RemoteError, request_json


def parse(
    provider: str,
    model: str,
    text: str,
    image: bytes | None,
    *,
    ollama_url: str,
    openai_key: str,
    prompt_version: int = PROMPT_VERSION,
) -> ParsedReceipt:
    if not model:
        raise RemoteError("model_not_configured")
    encoded = base64.b64encode(image).decode() if image else None
    schema = ParsedReceipt.model_json_schema()
    try:
        prompt = receipt_prompt(prompt_version)
    except ValueError:
        raise RemoteError("prompt_version_unsupported") from None
    if provider == "ollama":
        url = urlsplit(ollama_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"localhost", "127.0.0.1", "host.docker.internal", "::1"}
            or url.username
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise RemoteError("local_endpoint_invalid")
        message: dict[str, Any] = {"role": "user", "content": text or "Extract the receipt."}
        if encoded:
            message["images"] = [encoded]
        if prompt_version >= 2:
            schema["properties"]["subtotal"]["description"] = (
                "Copy the printed Subtotal / 小計 value; do not use the later Total or payment."
            )
            schema["properties"]["currency"]["description"] = (
                "Null unless USD/US$ or TWD/NT$ or a currency name is explicitly printed. "
                "A bare $ and a US address do not identify currency."
            )
            prompt += "\nJSON schema:\n" + json.dumps(schema, ensure_ascii=False)
        response = request_json(
            ollama_url.rstrip("/") + "/api/chat",
            {
                "model": model,
                "stream": False,
                "think": False,
                "format": schema,
                "messages": [{"role": "system", "content": prompt}, message],
                "options": {"temperature": 0},
            },
        )
        try:
            raw = response["message"]["content"]
        except (TypeError, KeyError):
            raise RemoteError("response_invalid") from None
    elif provider == "openai":
        if not openai_key:
            raise RemoteError("provider_key_missing")
        content = [{"type": "input_text", "text": text or "Extract the receipt."}]
        if encoded:
            content.append(
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64," + encoded,
                    "detail": "high",
                }
            )
        response = request_json(
            "https://api.openai.com/v1/responses",
            {
                "model": model,
                "store": False,
                "instructions": prompt,
                "input": [{"role": "user", "content": content}],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "receipt",
                        "strict": True,
                        "schema": schema,
                    }
                },
                "max_output_tokens": 12000,
            },
            {"Authorization": "Bearer " + openai_key},
        )
        if not isinstance(response, dict) or response.get("status") != "completed":
            raise RemoteError("response_incomplete")
        try:
            raw = "".join(
                part["text"]
                for item in response["output"]
                if item.get("type") == "message"
                for part in item["content"]
                if part.get("type") == "output_text"
            )
        except (KeyError, TypeError, AttributeError):
            raise RemoteError("response_invalid") from None
    else:
        raise RemoteError("provider_disabled")
    try:
        return ParsedReceipt.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError):
        raise RemoteError("parse_invalid") from None
