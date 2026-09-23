#!/bin/sh
set -eu
cd "$(dirname "$0")/../backend"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest
.venv/bin/alembic check
.venv/bin/python -m app.cli openapi --output openapi.json
