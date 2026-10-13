# Aero404 CFD Platform

**Language:** English · [繁體中文](README_zh.md)

A multi-user **STAR-CCM+ job queue and CFD result viewer** for the web. It queues simulations, runs them locally or over SSH on several servers, and lets your team browse the results — all from a small FastAPI service.

- **Job Session** — add a case in the browser; the platform copies your template, writes the parameters, queues the job and starts `starccm+ -batch` when cores are free, across multiple servers
- **Result viewer** — Case List (`Result.dat` table), Slices (PNG sequences), Plots (residual / force CSVs), 3D (STL)
- **Administration** — three roles (admin / user / visitor), Kill, jump the queue, notes, login log, settings page (hot-reloaded YAML)
- **Security** — passwords stored as salted PBKDF2-SHA256 hashes, login rate limiting (5 failures per IP per 5 minutes), HttpOnly + Secure session cookie
- **Log viewer** — one click opens a job's `workflow.log`, with paging (first / previous / next / last) and horizontal scrolling
- **Automatic failure detection** — stop a job and mark it FAILED when its log contains a given text (default: detects a missing inlet boundary)
- **Restart-safe** — restarting the web service does not kill running jobs; it re-adopts them afterwards

```
Browser ──HTTPS──> nginx (optional) ──> uvicorn / FastAPI (systemd)
                                            ├─ Server A  (this machine: subprocess)
                                            └─ Server B  (SSH + nohup)     both mount the same NFS case folder
```

---

## 1. Requirements

| Item | Notes |
|---|---|
| OS | Linux (tested on Ubuntu 22.04 / 24.04) |
| Python | 3.10 or newer |
| STAR-CCM+ | Installed and licensed on every compute server (not included) |
| Shared storage | All servers mount the same case folder (NFS); the local path may differ per machine |
| SSH | The machine running the platform must reach every remote server with **passwordless key-based** SSH |

## 2. Quick start

The examples use `/home/user/aero404_cfd_platform`; replace it with your own path and user.

```bash
git clone https://github.com/rping0310/Aero404-CFD-Platform.git /home/user/aero404_cfd_platform
cd /home/user/aero404_cfd_platform
bash deploy/install.sh          # creates the venv, installs packages, copies data.example/ to data/
```

1. Edit `data/servers.yaml` and `data/app_settings.yaml` (see "3. Paths and settings").
2. Create the first admin account (see "5. Creating accounts"):
   ```bash
   venv/bin/python tools/add_user.py alice --role admin
   ```
3. Run it in the foreground to test:
   ```bash
   venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8080
   ```
   Open `http://localhost:8080` and log in. From another computer over plain `http://` the login will fail — see "7. HTTPS".
4. Start it automatically at boot (see "6. systemd").

## 3. Paths and settings

All configuration lives in `data/` (not tracked by git). `data.example/` holds examples filled with dummy values (Server A / Server B, `192.0.2.x`, `/home/usera/...`).
**Changes to `data/*.yaml` take effect immediately — no restart needed** (admins can also edit them in the Settings tab).

| What to change | File and field |
|---|---|
| Server list (name, IP, cores, reserved cores) | `data/servers.yaml`: `name`, `host`, `total_cores`, `reserved_cores`, `enabled` |
| STAR-CCM+ path on each machine | `data/servers.yaml`: `starccm_path` (path on that machine) |
| Local path of the shared folder on each machine | `data/servers.yaml`: `nfs_path` (the same NFS share may be mounted at different paths) |
| SSH user and key | `data/servers.yaml`: `ssh_user`, `ssh_port`, `ssh_key_path` |
| Case folder as seen by the machine running the platform | `data/app_settings.yaml`: `nfs_root`, `viewer_data_path` |
| Names of the Straight / Yaw / Cornering sub-folders | `data/app_settings.yaml`: `simulation_type_folders` |
| Template folder name, entry macro | `data/app_settings.yaml`: `template_folder_name`, `entry_macro` |
| Accepted upload file names | `data/app_settings.yaml`: `uploaded_file_pattern` (regular expression) |
| Steps performed when a case is created | `data/workflow_template.yaml` |
| Default parameters of a new case | `data/case_parameter_default.dat` |
| Web port, project path, service user | `/etc/systemd/system/aero404.service` (see section 6) |

**Rule:** the server whose `host` is `127.0.0.1` / `localhost` is treated as "local" and runs jobs as child processes; every other server is reached over SSH.
`nfs_path` is the path *on that machine*; when it differs from `nfs_root` on the platform host, the platform converts between them.

### Case folder layout (shared NFS)

```
CFD_cloud_NFS/                    ← nfs_root
├── Straight/                     ← simulation_type 1
│   ├── template_CFD/             ← template_folder_name: must contain exactly ONE .sim plus your macros
│   │   ├── template.sim
│   │   ├── gui_run.java          ← entry_macro
│   │   └── ...
│   └── CASE_001/                 ← every new case is copied from the template
│       ├── CASE_001.sim
│       ├── case_parameter.dat    ← parameters written by the platform
│       ├── workflow.log          ← run output (viewable in the browser)
│       └── Result.dat            ← produced by your macro; the Case List reads this
├── Yaw/
└── Cornering/
```

### Adding a second server (SSH)

On the **machine running the platform**, as the user that runs it:

```bash
ssh-keygen -t ed25519                       # skip if you already have a key
ssh-copy-id -i ~/.ssh/id_ed25519.pub userb@192.0.2.20
ssh -o BatchMode=yes userb@192.0.2.20 true  # must NOT ask for a password; success means it is set up
```

Then add the server to `data/servers.yaml`. The remote machine must also mount the same NFS share and have a correct `starccm_path`.

## 4. Roles

| Action | admin | user | visitor |
|---|:-:|:-:|:-:|
| Browse results, see the job list and logs | ✓ | ✓ | ✓ |
| Add cases, edit notes | ✓ | ✓ | |
| Kill, jump the queue, delete records, clear FAILED | ✓ | | |
| Settings, login log | ✓ | | |

## 5. Creating accounts

There is **no self-registration**. An administrator creates accounts on the command line. Passwords are stored only as hashes — you will never see them in `users.yaml`.

```bash
cd /home/user/aero404_cfd_platform
venv/bin/python tools/add_user.py alice --role admin     # new admin (prompts for a password, min. 8 characters)
venv/bin/python tools/add_user.py bob   --role user
venv/bin/python tools/add_user.py carol --role visitor
venv/bin/python tools/add_user.py bob                    # existing account → reset the password
venv/bin/python tools/add_user.py bob --role admin       # existing account → change role (press Enter at the prompt to keep the password)
venv/bin/python tools/add_user.py bob --delete
venv/bin/python tools/add_user.py --list
```

- The service hot-reloads `users.yaml`, so **no restart is needed**.
- If your systemd unit sets `CFD_PLATFORM_DATA_DIR` to something other than `./data`, add `--data-dir /path/to/data`.
- Every login attempt (time, account, IP, success) is appended to `data/login_log.jsonl`; admins can read it under **Settings → Login Log** in the web UI.

## 6. Start at boot (systemd)

1. Edit `deploy/aero404.service`: change `User=`, `WorkingDirectory=`, `CFD_PLATFORM_DATA_DIR=` and the paths in `ExecStart=` (the example uses `/home/user/aero404_cfd_platform`).
2. Install and start it:
   ```bash
   sudo cp deploy/aero404.service /etc/systemd/system/aero404.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now aero404       # enable = start automatically at boot
   ```
3. Everyday commands:
   ```bash
   systemctl status aero404
   journalctl -u aero404 -f                  # live log
   sudo systemctl restart aero404            # needed after backend code changes; frontend changes only need a browser hard refresh (Ctrl+Shift+R)
   ```

> **Why does the unit contain `KillMode=process`?** Jobs on the local server are child processes of this service. systemd's default
> (`control-group`) kills the whole process group on `restart` — including a STAR-CCM+ run that has been going for days.
> With `KillMode=process` only the web service stops; jobs keep running, and after the service starts again it re-adopts them by PID and exit-code file and marks them DONE / FAILED correctly when they finish.
> Remote (SSH) jobs are unaffected either way.

## 7. HTTPS

The login cookie carries the `Secure` flag, so browsers drop it on plain `http://` (except `localhost`) — the symptom is "correct password, but login does not stick". Pick one:

**A. nginx + HTTPS (recommended)**

```bash
sudo apt install nginx
sudo mkdir -p /etc/ssl/aero404
sudo openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout /etc/ssl/aero404/server.key -out /etc/ssl/aero404/server.crt \
  -subj "/CN=cfd.example.internal"          # self-signed; use a real certificate if you have one
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/aero404
sudo ln -s /etc/nginx/sites-available/aero404 /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Then open `https://<host>:6200` (the port is set in `nginx.conf.example`; with a self-signed certificate your browser will warn once). Keep uvicorn listening on `127.0.0.1:8080` only.

**B. Trusted LAN only, plain HTTP for testing:** set `cookie_secure: false` in `data/app_settings.yaml`. Do not do this on an untrusted network.

## 8. Log viewer and automatic failure detection

- The 📄 button on each Job Session row opens that case's `workflow.log` (1000 lines per page; ⏮ ◀ ▶ ⏭ to page; while you are on the last page, a SOLVING job refreshes every 3 seconds). The page size is `logLines` in `frontend/static/js/app.js` (the backend caps it at 2000).
- While a job is SOLVING and its `workflow.log` contains a configured text, the platform kills the job and marks it FAILED with the reason shown on the row, so you do not waste hours of compute. The built-in rule detects `Named object "domain.inlet" does not exist in manager` (a CAD hole turns the inside into external flow, so no inlet exists).
  Define your own under `fail_log_rules` in `data/app_settings.yaml` (an example is included in the file; once the list exists it **replaces** the built-in default).
- Other situations the platform marks FAILED: the process ended with a non-zero exit code, it failed to start, it no longer existed after a service restart, or an admin killed it. An `[ERROR]` merely printed by your macro is **not** treated as a failure — cover it with a rule as above.

## 9. Updating and backups

```bash
cd /home/user/aero404_cfd_platform
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart aero404          # does not interrupt running jobs (KillMode=process)
```

Back up only the `data/` folder (accounts, the `jobs.json` queue, server list, login log). **Never commit `data/` to git** (it is in `.gitignore`).

## 10. Tests

```bash
cd backend
python3 -m app.jobs.test_queue
python3 -m app.jobs.test_workflow
python3 -m unittest app.jobs.test_real_runner app.test_log_page
python3 -m unittest app.test_api app.test_integration app.test_frontend_assets
```

Tests use a temporary copy of `data.example/` and a test account `admin` / `changeme`; they never touch your `data/`.

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Correct password but cannot log in | Connecting over `http://`; the Secure cookie is dropped → see section 7 |
| Login returns 429 | More than 5 failed attempts from the same IP within 5 minutes; wait and retry |
| New case is FAILED immediately with `Failed to start` | Passwordless SSH to the remote server does not work, or `starccm_path` / `nfs_path` is wrong; test with `ssh -o BatchMode=yes user@host true` on the platform host |
| A remote job's log will not open | SSH to that server is down, or `nfs_path` does not match the real path |
| FAILED `Exit code: 1` but the log shows no error | The process was killed from outside (reboot, NFS / disk failure); when the exit-code file cannot be read, `1` is shown too. Check `journalctl -k` and `dmesg` |
| FAILED `Process no longer existed after service restart` | The job's process was already gone when the service restarted; make sure the unit has `KillMode=process` |
| Edited a `*.yaml` but nothing changed | It should apply immediately; make sure you edited the `data/` that `CFD_PLATFORM_DATA_DIR` points to |

## 12. Project layout

```
backend/app/        FastAPI: routers/, jobs/ (queue, runners, workflow), config/ (hot reload), auth, session
frontend/           templates/index.html, static/js/app.js (Alpine.js), static/vendor/ (Plotly, three.js)
data.example/       example configuration (dummy values) → copy to data/
deploy/             aero404.service, nginx.conf.example, install.sh
tools/add_user.py   account management
docs/HANDOFF.md     early design document (historical, partly outdated; written in Chinese)
```

## License

Copyright © 2026 Ping Lin · Non-Commercial Use Only (as stated on the web UI's About page).
