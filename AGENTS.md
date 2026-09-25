# Finance Tracker development

Read `01-requirements.md`, `02-architecture.md`, `03-data-model.md`, `04-dev-plan.md` and the current verification record before changing scope. Keep those baseline documents in the repository root. Communication and UI are primarily Traditional Chinese.

## Scope and data

- Current implementation is Phase 1's manual-ledger delivery (bank/cash/credit card, income/expense/refund/transfer, charts and CSV) plus D1-04 receipt attachments the first capture/Telegram delivery (provider-disabled by default), and reviewed receipt items with literal purchase-history search (an early Phase 5 subset). Remaining Phase 1 tasks stay open in 04-dev-plan.md. Do not show unfinished financial metrics as zero or mark future requirements complete.
- PostgreSQL is authoritative. Use real PostgreSQL integration tests, Decimal for financial values, migrations for schema changes, and backend-generated report snapshots in later phases.
- Never commit `.env`, production data, attachments, backups, logs, local runtime downloads or browser sessions. Test fixtures must be synthetic.
- Do not overwrite user changes or a real DB to make tests pass. `TEST_DATABASE_URL` must name a disposable `finance_test*` database. Tests truncate its app tables and create temporary `finance_restore_*` databases.
- Do not ask for secrets in chat. Initial accounts are created by the interactive CLI; no default production account/password.

## Commands

Prerequisites: Python 3.12, uv 0.12.18, Node 24.19.0, pnpm 11.19.0, PostgreSQL 16 plus matching pg_dump / pg_restore on PATH.

From root:

```sh
uv sync --project backend --frozen
pnpm --dir frontend install --frozen-lockfile
sh scripts/check-backend.sh
pnpm --dir frontend api:generate
pnpm --dir frontend test
pnpm --dir frontend build
```

Set the documented database, secret and data-directory environment variables before backend checks. `DATABASE_URL` and `TEST_DATABASE_URL` must both point to the disposable test DB for the full check script.

From `backend/`:

```sh
.venv/bin/alembic upgrade head
.venv/bin/alembic check
.venv/bin/python -m app.cli init-user
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
.venv/bin/python -m app.worker
```

Run API and worker in separate terminals; Vite uses `pnpm --dir frontend dev` from root. Browser tests require the synthetic demo seed and the three processes, then `pnpm --dir frontend test:e2e`. Chrome desktop/mobile emulation does not substitute for actual iPhone Safari or Numbers validation.

Compose startup: `python3 scripts/configure.py`, `sh scripts/start.sh`, then interactive init-user. Never run `docker compose down -v` against user data.

## Implementation rules

- API routes validate inputs/owner, services own transactions, repositories must not commit independently. All business writers use `core.db.transaction()` to participate in backup maintenance locks.
- Job heartbeat metadata may bypass the maintenance lock; financial writes and attachments may not. A handler must fence lease ownership and be safe after retries. External delivery is at least once, never promise exactly once.
- Schema changes need a reviewed static Alembic revision. Update `SCHEMA_VERSION` with the expected head so API/worker reject mismatched schema. Do not use runtime `create_all` in production migrations.
- Cookie mutations require CSRF and exact Origin. Don't expose tokens or secrets in exceptions/logs. Refresh replay revokes its entire session family; preserve this guarantee.
- OpenAPI is the frontend contract. Regenerate and commit `backend/openapi.json` and `frontend/src/api/schema.d.ts` together.
- Use the pinned lockfiles. Record a compatibility reason before changing a major baseline dependency.
- Financial and valuation/category/account writers lock the owner's BookSettings row. Report creation takes the same lock and freezes displayed rows and totals. Exports read only that immutable snapshot.
- Never run Alembic downgrade against a real ledger. Corrections use reversal/replacement; migration rollback is only for disposable tests or a planned restore.
- For nontrivial behavior, test independent outcomes and failure/retry paths rather than merely mirroring implementation.
- Update task statuses and `docs/verification/` with actual evidence. Missing Docker/hardware/remote checks stay unverified. Do not mark a whole phase DONE based only on compilation.
- Use multiple agents only when the user explicitly requests them or a separately applicable instruction authorizes delegation. This file does not authorize automatic delegation.

- Receipt files must use the receipts service, backup maintenance lock and attachment advisory lock. Keep original bytes private and immutable; publish files before metadata commit. Cleanup must check all references and the 24-hour grace period. Never put production receipts in Git; the tiny receipt fixtures are explicitly synthetic.

- Capture bridge has no DB credentials or attachment volume. Keep network jobs out of the core worker. API rechecks the limited token and Telegram private-chat owner whitelist. Never acknowledge an update before committing its inbox/cursor and effects.
- Confirm captures in a top-level transaction: the immutable ledger's xmin guard intentionally rejects postings created inside a savepoint. On input errors, roll back first, then record the Telegram error reply/inbox in a new transaction.
- OCR may only propose data. Preserve unknown values and original parse attempts; explicit user confirmation must call the existing ledger service. Lease and draft revision checks fence stale responses. Keep real provider/Telegram tests pending until the user configures them.

- Receipt item corrections append a new review and item rows; never overwrite parse attempts or financial postings. Search only the latest review matching both draft and posted transaction revisions. Keep unknown quantities/prices null; do not infer unit prices from a receipt total or mix currencies/specifications.
