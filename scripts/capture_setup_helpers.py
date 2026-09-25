"""Host-only setup helpers: no credentials in errors, URLs in logs, or update acknowledgements."""

import getpass
import json
import re
import secrets
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import warnings


class SetupError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_secret(prompt):
    # getpass otherwise falls back to echoing the secret in unsuitable terminals.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt).strip()
        except (getpass.GetPassWarning, EOFError):
            raise SetupError("無法隱藏輸入，請在 Mac 或 VS Code 的互動終端機執行。") from None


def select_ledger(docker, root):
    query = (
        "import json; from sqlalchemy import select; "
        "from app.core.db import transaction; from app.core.models import User; "
        "\nwith transaction() as db:\n"
        " print(json.dumps(list(db.scalars(select(User.login_name)"
        ".where(User.disabled_at.is_(None)).order_by(User.login_name)))))"
    )
    try:
        result = subprocess.run(
            [docker, "compose", "exec", "-T", "api", "python", "-c", query],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        accounts = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        accounts = None
    if not isinstance(accounts, list) or not accounts:
        raise SetupError("無法讀取帳本帳號。請確認 Docker 已啟動、網頁可以登入。設定未變更。")
    if any(not isinstance(name, str) or not name or len(name) > 100 for name in accounts):
        raise SetupError("帳本帳號資料無效，設定未變更。")
    if len(accounts) == 1:
        print("使用 Finance Tracker 帳本：" + json.dumps(accounts[0], ensure_ascii=False))
        return accounts[0]
    for index, name in enumerate(accounts, 1):
        print(str(index) + ". " + json.dumps(name, ensure_ascii=False))
    choice = read_secret("請選帳本編號（隱藏輸入；不要填 Bot 帳號或 Token）：")
    if not choice.isascii() or not choice.isdecimal() or not 1 <= int(choice) <= len(accounts):
        raise SetupError("請輸入清單上的帳本編號。設定未變更。")
    return accounts[int(choice) - 1]


def telegram_call(token, method, payload=None):
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise SetupError("Token 格式無效。請只複製 BotFather 提供的完整 Token，不含說明文字。")
    if method not in {"getMe", "getUpdates"}:
        raise SetupError("不支援的設定操作。")
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/" + method,
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=25) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise SetupError("Telegram 回應超過大小限制。設定未變更。")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("ok") is not True or "result" not in result:
            raise SetupError("Telegram 回應格式不正確。設定未變更。")
        return result["result"]
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        if code in {401, 404}:
            message = (
                "Telegram 拒絕此 Token（HTTP "
                + str(code)
                + "）。請使用 BotFather 最新產生的完整 Token。"
            )
        elif code == 409:
            message = (
                "Bot 已由其他程序輪詢或設定了 Webhook。請先確認現有整合；也可改用手動數字 User ID。"
            )
        elif code == 429:
            message = "Telegram 暫時限制請求次數（HTTP 429），請稍後重試。"
        elif 300 <= code < 400:
            message = "Telegram 連線被重新導向，已停止以保護 Token。請檢查網路或代理設定。"
        else:
            message = "Telegram 服務回覆 HTTP " + str(code) + "。請稍後重試。"
        raise SetupError(message + " 設定未變更。") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            message = (
                "Python 無法驗證 HTTPS 憑證，請修復 Python 憑證或檢查代理設定；不要關閉憑證驗證。"
            )
        elif isinstance(error.reason, socket.gaierror):
            message = "無法解析 api.telegram.org，請檢查網路、DNS 或 VPN。"
        else:
            message = "無法連線 Telegram，請檢查網路、VPN 或代理設定。"
        raise SetupError(message + " 設定未變更。") from None
    except (TimeoutError, socket.timeout):
        raise SetupError("Telegram 連線逾時。請稍後重試，設定未變更。") from None
    except (ValueError, UnicodeError):
        raise SetupError("Telegram 回傳無效資料。設定未變更。") from None
    except OSError:
        raise SetupError("Telegram 連線中斷。設定未變更。") from None


def verify_bot(token):
    bot = telegram_call(token, "getMe")
    if (
        not isinstance(bot, dict)
        or bot.get("is_bot") is not True
        or type(bot.get("id")) is not int
        or bot["id"] <= 0
        or not isinstance(bot.get("username"), str)
        or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot["username"])
    ):
        raise SetupError("Telegram 未回傳有效 Bot，設定未變更。")
    return bot


def pair_user(token, bot_username):
    challenge = "finance_" + secrets.token_urlsafe(24)
    command = "/start " + challenge
    print("請在 3 分鐘內，到 Telegram 私聊 @" + bot_username + " 傳送下列整行：")
    print(command)
    print("這是一次性配對碼，只傳給自己的 Bot；請勿分享或截圖。正在等待配對…")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        # Do not advance or use a negative offset: no pending receipts may be acknowledged
        # before the bridge durably stores them. Do not change allowed_updates either.
        updates = telegram_call(token, "getUpdates", {"offset": 0, "limit": 100, "timeout": 15})
        if not isinstance(updates, list):
            raise SetupError("Telegram 配對回應無效。設定未變更。")
        if time.monotonic() >= deadline:
            break
        for update in updates:
            message = update.get("message") if isinstance(update, dict) else None
            if not isinstance(message, dict) or message.get("text") != command:
                continue
            sender, chat = message.get("from", {}), message.get("chat", {})
            if not isinstance(sender, dict) or not isinstance(chat, dict):
                continue
            user_id = sender.get("id")
            if (
                chat.get("type") == "private"
                and type(user_id) is int
                and user_id > 0
                and type(chat.get("id")) is int
                and chat["id"] == user_id
                and sender.get("is_bot") is False
                and not message.get("forward_origin")
            ):
                print("已配對 Telegram User ID：" + str(user_id))
                return user_id
        if len(updates) >= 100:
            raise SetupError("Bot 有大量待處理訊息，為保留訊息請改填數字 User ID。設定未變更。")
        time.sleep(1)
    raise SetupError("配對逾時，請重新執行設定並傳送新的配對碼。設定未變更。")
