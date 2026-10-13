#!/usr/bin/env python3
"""Create, update, list or delete platform accounts (stored hashed in data/users.yaml).

Run from the project root with the project's virtualenv:

    venv/bin/python tools/add_user.py alice --role admin      # prompts for the password
    venv/bin/python tools/add_user.py bob   --role user
    venv/bin/python tools/add_user.py alice                   # existing name -> change password / role
    venv/bin/python tools/add_user.py --list
    venv/bin/python tools/add_user.py bob --delete

Roles: admin | user | visitor.  The running service hot-reloads users.yaml,
so no restart is needed.  The data directory is $CFD_PLATFORM_DATA_DIR or ./data.
"""

import argparse
import getpass
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("admin", "user", "visitor")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="account name (case-sensitive)")
    ap.add_argument("--role", choices=ROLES, help="admin | user | visitor (default: keep current, or 'user' for new accounts)")
    ap.add_argument("--password", help="password (avoid: it ends up in your shell history; omit to be prompted)")
    ap.add_argument("--delete", action="store_true", help="delete the account")
    ap.add_argument("--list", action="store_true", help="list accounts and roles")
    ap.add_argument("--data-dir", help="data directory (default: $CFD_PLATFORM_DATA_DIR or ./data)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir or os.environ.get("CFD_PLATFORM_DATA_DIR") or ROOT / "data").expanduser().resolve()
    os.environ["CFD_PLATFORM_DATA_DIR"] = str(data_dir)
    sys.path.insert(0, str(ROOT))
    import yaml
    from backend.app.auth import hash_password

    users_file = data_dir / "users.yaml"
    users = []
    if users_file.exists():
        users = (yaml.safe_load(users_file.read_text(encoding="utf-8")) or {}).get("users", []) or []

    if args.list:
        for u in users:
            print(f"{u.get('name'):20s} {u.get('role', 'user')}")
        if not users:
            print("(no accounts yet)")
        return 0
    if not args.name:
        ap.error("an account name is required (or use --list)")

    existing = next((u for u in users if u.get("name") == args.name), None)

    if args.delete:
        if not existing:
            print(f"No such account: {args.name}")
            return 1
        users = [u for u in users if u.get("name") != args.name]
        _save(users_file, users)
        print(f"Deleted '{args.name}'.")
        return 0

    password = args.password
    if password is None:
        prompt = f"Password for '{args.name}'" + (" (empty = keep current): " if existing else ": ")
        password = getpass.getpass(prompt)
        if password == "" and existing:
            password = None
        elif password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.")
            return 1
    if password is not None and len(password) < 8:
        print("Password must be at least 8 characters.")
        return 1

    if existing:
        if password is not None:
            existing["password"] = hash_password(password)
        if args.role:
            existing["role"] = args.role
        action = "Updated"
    else:
        if password is None:
            print("A password is required for a new account.")
            return 1
        users.append({"name": args.name, "role": args.role or "user", "password": hash_password(password)})
        action = "Created"
    _save(users_file, users)
    role = (existing or users[-1]).get("role", "user")
    print(f"{action} '{args.name}' (role: {role}) in {users_file}")
    return 0


def _save(path: Path, users: list) -> None:
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".users.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        yaml.safe_dump({"users": users}, f, allow_unicode=True, sort_keys=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(main())
