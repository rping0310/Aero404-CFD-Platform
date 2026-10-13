#!/usr/bin/env bash
# One-shot setup: virtualenv + dependencies + a data/ folder from the examples.
# Usage: bash deploy/install.sh [project_dir]
set -euo pipefail

project_dir="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$project_dir"

python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

if [ ! -d data ]; then
  cp -r data.example data
  chmod 700 data
  echo "Created data/ from data.example/ - edit servers.yaml and app_settings.yaml."
else
  echo "data/ already exists - left untouched."
fi

cat <<MSG

Installed. Next steps:
  1. Edit data/servers.yaml and data/app_settings.yaml (paths, servers).
  2. Create the first admin account:
       venv/bin/python tools/add_user.py <name> --role admin
  3. Start it (foreground test):
       venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8080
     or install the systemd service - see README.md.
MSG
