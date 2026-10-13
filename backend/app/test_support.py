"""Shared test fixtures.

Tests never touch your real ``data/`` directory. They build a throw-away copy of
``data.example/`` (two example servers, no accounts) and add one hashed test
admin account: ``admin`` / ``changeme``.
"""

import os
import shutil
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "data.example"


def prepare_data(root: Path) -> Path:
    """Copy data.example/ to <root>/data and create the test admin account."""
    data = root / "data"
    shutil.copytree(EXAMPLE, data)
    # Point the settings module at the copy BEFORE app.auth imports it.
    os.environ["CFD_PLATFORM_DATA_DIR"] = str(data)
    from app.auth import hash_password
    users = {"users": [{"name": "admin", "role": "admin", "password": hash_password("changeme")}]}
    (data / "users.yaml").write_text(yaml.safe_dump(users, sort_keys=False), encoding="utf-8")
    # The test client talks plain http, so the "Secure" login cookie must be off.
    settings_path = data / "app_settings.yaml"
    app_settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    app_settings["cookie_secure"] = False
    settings_path.write_text(yaml.safe_dump(app_settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return data


def use_temp_data_dir() -> Path:
    """For tests that use the global settings module (job queue / workflow tests)."""
    return prepare_data(Path(tempfile.mkdtemp(prefix="aero404_testdata_")))
