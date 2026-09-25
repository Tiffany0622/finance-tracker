#!/usr/bin/env python3
"""Local, interactive opt-in. Credentials never appear in argv, output, or Git."""
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    env_path = ROOT / '.env'
    if not env_path.is_file():
        raise SystemExit('請先依 README 完成基礎安裝。')
    docker = shutil.which('docker') or str(Path.home() / '.docker/bin/docker')
    username = input('帳本登入帳號：').strip()
    provider = input('辨識方式 disabled / ollama / openai（預設 disabled）：').strip() or 'disabled'
    if provider not in {'disabled','ollama','openai'}:
        raise SystemExit('辨識方式無效。')
    model = input('要使用的視覺模型完整名稱（須已可使用）：').strip() if provider != 'disabled' else ''
    if provider != 'disabled' and not model:
        raise SystemExit('請指定支援圖片與結構化輸出的模型。')
    api_key = getpass.getpass('OpenAI API Key（隱藏輸入）：').strip() if provider == 'openai' else ''
    if provider == 'openai' and not api_key:
        raise SystemExit('未輸入 API Key，設定未變更。')
    token = getpass.getpass('Telegram Bot Token（隱藏輸入，留空則不啟用）：').strip()
    bot_id = user_id = 0
    if token:
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token):
            raise SystemExit('Bot Token 格式無效。')
        try:
            req = urllib.request.Request(f'https://api.telegram.org/bot{token}/getMe', data=b'{}', headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=20) as response:
                result = json.loads(response.read(65536))
            bot_id = result['result']['id']
            print('Bot 已驗證：@' + result['result']['username'])
        except Exception:
            raise SystemExit('Bot 驗證失敗；請檢查網路與 Token。設定未變更。') from None
        user_text = input('只允許的 Telegram 數字 User ID（不是 @帳號）：').strip()
        if not user_text.isdecimal() or int(user_text) <= 0:
            raise SystemExit('請填寫正確的數字 User ID。')
        user_id = int(user_text)
    if any('\n' in val or '\r' in val or "'" in val for val in (username, model, api_key, token)) or len(model)>100:
        raise SystemExit('設定值含無效字元。')
    enabled = bool(token or provider != 'disabled')
    if provider == 'openai':
        print('啟用後，上傳照片的去除中繼資料預覽及記帳文字會送到 OpenAI API，並產生 API 費用。')
    elif provider == 'ollama':
        print('請先在 Mac 啟動 Ollama 並下載指定視覺模型；Docker 透過 host.docker.internal:11434 存取。')
    print('Telegram 照片會經過 Telegram；只有確認草稿後才會入帳。')
    if input('套用以上設定？輸入 yes：').strip() != 'yes':
        raise SystemExit('設定未變更。')
    bridge_token = secrets.token_urlsafe(48)
    # Register a hash inside the API. The random plaintext token travels only through stdin.
    completed = subprocess.run([docker,'compose','exec','-T','api','python','-m','app.capture.setup','--username',username], cwd=ROOT, input=bridge_token+'\n', text=True, capture_output=True)
    if completed.returncode:
        raise SystemExit('無法註冊整合授權，請確認帳號與 API 狀態；沒有更改 .env。')
    updates = {'COMPOSE_PROFILES':'capture' if enabled else '', 'CAPTURE_PROVIDER':provider,
        'CAPTURE_MODEL':model, 'CAPTURE_BRIDGE_TOKEN':bridge_token, 'OPENAI_API_KEY':api_key,
        'TELEGRAM_BOT_TOKEN':token, 'TELEGRAM_BOT_ID':str(bot_id),'TELEGRAM_USER_ID':str(user_id),
        'OLLAMA_URL':'http://host.docker.internal:11434'}
    if any('\n' in val or '\r' in val or "'" in val for val in updates.values()):
        raise SystemExit('設定值含無效字元。')
    lines = env_path.read_text().splitlines()
    lines = [line for line in lines if line.split('=',1)[0] not in updates]
    lines += [key + "='" + val + "'" for key,val in updates.items()]
    fd, temp = tempfile.mkstemp(prefix='.capture-env-', dir=ROOT)
    try:
        with os.fdopen(fd,'w') as stream:
            stream.write('\n'.join(lines)+'\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temp,env_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    state = Path.home() / 'Library/Application Support/FinanceTracker'
    marker = state / 'capture-enabled'
    if not enabled and marker.exists():
        marker.unlink()
    if not enabled:
        subprocess.run([docker,'compose','--profile','capture','stop','capture-bridge'],cwd=ROOT,check=True)
    subprocess.run([docker,'compose','up','-d','--no-build','--wait'],cwd=ROOT,check=True)
    if enabled and state.is_dir():
        marker.write_text('enabled\n')
        marker.chmod(0o600)
    print('設定已套用。請到網頁「收據草稿」查看連線狀態。')
    print('若有啟用 Telegram，請對自己的 Bot 傳送 /start，再傳一張合成測試收據。')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('\n已取消。') from None
