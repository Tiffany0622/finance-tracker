"""An explicit synthetic seed; refuses production names and never replaces users."""
from sqlalchemy.engine import make_url
from app.cli import initialize_user
from app.core.config import settings
from app.core.db import check_schema

if __name__ == '__main__':
    name = make_url(settings().database_url.get_secret_value()).database or ''
    if not name.startswith(('finance_demo', 'finance_test')):
        raise SystemExit('Demo seed only supports an empty finance_demo* or finance_test* database.')
    check_schema()
    initialize_user('alice', 'synthetic-test-passphrase')
    print('Synthetic demo user created. Never use this account for real finances.')
