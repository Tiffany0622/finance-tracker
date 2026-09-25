"""Opt-in model evaluation with synthetic receipt images; no ledger or Telegram writes.

Run with backend/.venv/bin/python, which has the locked Pillow/Pydantic dependencies.
"""

import argparse
import io
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.capture.prompts import PROMPT_VERSION  # noqa: E402
from app.capture.providers import parse  # noqa: E402
from app.capture.transport import RemoteError  # noqa: E402

MONEY_FIELDS = {"amount", "subtotal", "tax", "tip", "discount"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3-vl:8b-instruct")
    parser.add_argument("--prompt-version", type=int, choices=[1, 2], default=PROMPT_VERSION)
    parser.add_argument(
        "--font", type=Path, default=Path("/System/Library/Fonts/STHeiti Medium.ttc")
    )
    parser.add_argument("--repeat", type=int, choices=range(1, 11), default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error("請用 --font 指定可顯示繁體中文的 TTF / TTC 字型。")
    if "cloud" in args.model.lower():
        parser.error("此評估僅使用已下載的本機模型。")
    font = ImageFont.truetype(str(args.font), 32)
    cases = json.loads((ROOT / "backend/tests/fixtures/receipt-eval.json").read_text())
    report = {
        "model": args.model,
        "prompt_version": args.prompt_version,
        "at": datetime.now(UTC).isoformat(),
        "results": [],
    }
    for repeat in range(args.repeat):
        for case in cases:
            image = Image.new("RGB", (1000, len(case["lines"]) * 46 + 80), "white")
            draw = ImageDraw.Draw(image)
            for index, line in enumerate(case["lines"]):
                draw.text((30, 30 + index * 46), line, font=font, fill="black")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=95)
            started = time.monotonic()
            result = {"case": case["id"], "repeat": repeat + 1, "mismatches": {}}
            try:
                parsed = parse(
                    "ollama",
                    args.model,
                    "",
                    buffer.getvalue(),
                    ollama_url="http://127.0.0.1:11434",
                    openai_key="",
                    prompt_version=args.prompt_version,
                ).model_dump(mode="json")
                for key, expected in case["expected"].items():
                    actual = parsed[key]
                    equal = actual == expected
                    if key in MONEY_FIELDS and actual is not None and expected is not None:
                        equal = Decimal(actual) == Decimal(expected)
                    if not equal:
                        result["mismatches"][key] = {
                            "expected": expected,
                            "actual": actual,
                        }
            except RemoteError as error:
                result["error"] = error.code
            result["seconds"] = round(time.monotonic() - started, 2)
            result["passed"] = not result["mismatches"] and "error" not in result
            report["results"].append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if all(result["passed"] for result in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
