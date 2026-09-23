import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from app.core.backup import create_backup, verify_backup
from app.core.config import Settings
from app.core.security import valid_csrf


def test_remote_plaintext_cookies_are_rejected() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        Settings(app_origin="http://private.example", cookie_secure=False)


@given(st.text(max_size=128))
def test_untrusted_csrf_values_do_not_crash(value: str) -> None:
    assert valid_csrf(value) is False


def test_backup_rejects_unlisted_files_and_paths(tmp_path: Path) -> None:
    path = create_backup()
    extra = path / "extra.txt"
    extra.write_text("unlisted")
    with pytest.raises(ValueError, match="inventory"):
        verify_backup(path)
    extra.unlink()
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["../outside.txt"] = "invalid"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_backup(path)


def test_migration_matches_current_models() -> None:
    from alembic.config import Config

    from alembic import command

    command.check(Config(str(Path(__file__).parents[1] / "alembic.ini")))
