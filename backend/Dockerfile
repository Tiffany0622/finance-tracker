FROM ghcr.io/astral-sh/uv:0.12.18 AS uv
# Shares the server's exact major/minor, so pg_dump and pg_restore match.
FROM postgres:16.15-bookworm@sha256:efedf3595f1d6f415c08568ba171029bf54052e754cc9f030e3f2412b21f3d67
# The PostgreSQL base does not include a CA bundle. Network adapters require
# verified HTTPS for Telegram and cloud OCR; never disable TLS verification.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
RUN uv python install 3.12.14
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --python 3.12.14
RUN .venv/bin/python -c "import ssl; assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0, 'HTTPS CA trust store is empty'"
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
RUN useradd --uid 10001 --create-home app && mkdir -p /data/attachments /backups && chown -R app:app /data /backups
ENV PATH="/app/.venv/bin:$PATH"
USER app
ENTRYPOINT []
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
