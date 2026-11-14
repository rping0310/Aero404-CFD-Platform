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
    # API fixtures use an HTTPS TestClient URL, honoring Secure login cookies.
    return data


def use_temp_data_dir() -> Path:
    """For tests that use the global settings module (job queue / workflow tests)."""
    return prepare_data(Path(tempfile.mkdtemp(prefix="aero404_testdata_")))
