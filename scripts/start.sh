#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
command -v docker >/dev/null 2>&1 || { echo '請先安裝並啟動 Docker Desktop 或 OrbStack。'; exit 1; }
test -f .env || { echo '請先執行 python3 scripts/configure.py。'; exit 1; }
mkdir -p data/app/attachments data/backups
attempt=0
until docker info >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 30 || { echo '容器引擎尚未就緒，請啟動後重試。'; exit 1; }
  sleep 2
done
if [ "${1:-}" = "--no-build" ]; then
  exec docker compose up -d --no-build --wait
fi
exec docker compose up -d --build --wait
