# Aero404 CFD Platform

**語言 / Language:** [English](README.md) · 繁體中文

多使用者 **STAR-CCM+ 排程平台 + CFD 結果瀏覽器**（Web 版）。
Multi-user job queue for STAR-CCM+ (local or over SSH) with a built-in result viewer, running as a small FastAPI service.

- **Job Session**：網頁新增 case → 自動複製 template、寫入參數、排隊、依核心數啟動 `starccm+ -batch`，可多台伺服器
- **結果瀏覽**：Case List（`Result.dat` 表格）、Slices（PNG 序列）、Plots（CSV 殘差／力係數）、3D（STL）
- **管理**：三種角色（admin / user / visitor）、Kill、插隊、備註、登入記錄、設定頁（YAML 熱重載）
- **安全**：密碼以 PBKDF2-SHA256 加鹽雜湊儲存、登入失敗限流（每 IP 5 分鐘內最多 5 次失敗）、HttpOnly + Secure cookie
- **Log 檢視**：每個 job 一鍵開啟 `workflow.log`，分頁（最前／上一頁／下一頁／最後），可左右捲動
- **自動判定失敗**：log 出現指定文字就停止 job 並標示 FAILED（預設偵測「沒有 inlet 邊界」）
- **重啟不中斷**：重啟網頁服務不會殺掉正在跑的 job，重啟後自動接手

```
Browser ──HTTPS──> nginx (可選) ──> uvicorn / FastAPI (systemd)
                                        ├─ Server A（本機：subprocess）
                                        └─ Server B（SSH + nohup）     兩台掛載同一份 NFS 案例資料夾
```

---

## 1. 需求

| 項目 | 說明 |
|---|---|
| OS | Linux（Ubuntu 22.04 / 24.04 測試過） |
| Python | 3.10 以上 |
| STAR-CCM+ | 每台運算伺服器都要自行安裝並有授權（本專案不含） |
| 共用儲存 | 所有伺服器掛載同一份案例資料夾（NFS）；各機本地路徑可以不同 |
| SSH | 執行平台的主機要能**免密碼金鑰** SSH 到每台遠端伺服器 |

## 2. 快速開始

以下以 `/home/user/aero404_cfd_platform` 為例，請換成你自己的路徑與使用者。

```bash
git clone https://github.com/rping0310/Aero404-CFD-Platform.git /home/user/aero404_cfd_platform
cd /home/user/aero404_cfd_platform
bash deploy/install.sh          # 建 venv、裝套件、由 data.example/ 複製出 data/
```

1. 編輯 `data/servers.yaml`、`data/app_settings.yaml`（見「3. 修改路徑與設定」）
2. 建立第一個管理員帳號（見「5. 註冊帳號」）：
   ```bash
   venv/bin/python tools/add_user.py alice --role admin
   ```
3. 前景啟動測試：
   ```bash
   venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8080
   ```
   瀏覽器開 `http://localhost:8080` 登入。若從別台電腦用 `http://` 連線，登入會失敗，原因與解法見「7. HTTPS」。
4. 設成開機自動啟動（見「6. systemd」）。

## 3. 修改路徑與設定

所有設定都在 `data/`（不會被 git 追蹤）。`data.example/` 是範例，裡面全是假資料（Server A / Server B、`192.0.2.x`、`/home/usera/...`）。
**`data/*.yaml` 修改後立即生效，不需重啟**（admin 也可在網頁 Settings 分頁編輯）。

| 要改什麼 | 檔案與欄位 |
|---|---|
| 伺服器清單（名稱、IP、核心數、保留核心） | `data/servers.yaml`：`name`、`host`、`total_cores`、`reserved_cores`、`enabled` |
| 每台機器上的 STAR-CCM+ 路徑 | `data/servers.yaml`：`starccm_path`（該機器上的路徑） |
| 每台機器上共用資料夾的本地路徑 | `data/servers.yaml`：`nfs_path`（同一份 NFS 在不同機器可掛在不同路徑） |
| SSH 使用者、金鑰 | `data/servers.yaml`：`ssh_user`、`ssh_port`、`ssh_key_path` |
| 平台所在機器看到的案例資料夾 | `data/app_settings.yaml`：`nfs_root`、`viewer_data_path` |
| Straight / Yaw / Cornering 子資料夾名稱 | `data/app_settings.yaml`：`simulation_type_folders` |
| Template 資料夾名稱、入口 macro | `data/app_settings.yaml`：`template_folder_name`、`entry_macro` |
| 上傳檔名規則 | `data/app_settings.yaml`：`uploaded_file_pattern`（正規表示式） |
| 建立 case 的步驟 | `data/workflow_template.yaml` |
| 新 case 的預設參數 | `data/case_parameter_default.dat` |
| 網頁埠、專案路徑、執行使用者 | `/etc/systemd/system/aero404.service`（見第 6 節） |

**規則：** `host` 為 `127.0.0.1` / `localhost` 的那台視為「本機」，以子行程執行；其他一律走 SSH。
`nfs_path` 是「該台機器上」看到的路徑，與平台所在機器的 `nfs_root` 不同時，平台會自動換算。

### 案例資料夾結構（共用 NFS）

```
CFD_cloud_NFS/                    ← nfs_root
├── Straight/                     ← simulation_type 1
│   ├── template_CFD/             ← template_folder_name：必須含「一個」.sim 與你的 macro
│   │   ├── template.sim
│   │   ├── gui_run.java          ← entry_macro
│   │   └── ...
│   └── CASE_001/                 ← 每個新 case 由 template 複製而來
│       ├── CASE_001.sim
│       ├── case_parameter.dat    ← 平台寫入的參數
│       ├── workflow.log          ← 執行輸出（網頁可檢視）
│       └── Result.dat            ← 你的 macro 產生，Case List 讀這個
├── Yaw/
└── Cornering/
```

### 加入第二台伺服器（SSH）

在**執行平台的主機**上，用執行平台的使用者：

```bash
ssh-keygen -t ed25519                       # 已有金鑰可略過
ssh-copy-id -i ~/.ssh/id_ed25519.pub userb@192.0.2.20
ssh -o BatchMode=yes userb@192.0.2.20 true  # 不可要求輸入密碼，成功才算完成
```

再於 `data/servers.yaml` 新增該台。遠端也必須掛載同一份 NFS，且 `starccm_path` 正確。

## 4. 角色權限

| 動作 | admin | user | visitor |
|---|:-:|:-:|:-:|
| 瀏覽結果、看 job 列表與 log | ✓ | ✓ | ✓ |
| 新增 case、編輯備註 | ✓ | ✓ | |
| Kill、插隊、刪除記錄、清除 FAILED | ✓ | | |
| Settings、登入記錄 | ✓ | | |

## 5. 註冊帳號

平台**沒有自助註冊**，帳號由管理員用命令列建立（密碼只存雜湊，`users.yaml` 裡看不到明碼）：

```bash
cd /home/user/aero404_cfd_platform
venv/bin/python tools/add_user.py alice --role admin     # 新增管理員（會提示輸入密碼，至少 8 字元）
venv/bin/python tools/add_user.py bob   --role user
venv/bin/python tools/add_user.py carol --role visitor
venv/bin/python tools/add_user.py bob                    # 已存在 → 重設密碼
venv/bin/python tools/add_user.py bob --role admin       # 已存在 → 改角色（密碼提示直接 Enter = 保留原密碼）
venv/bin/python tools/add_user.py bob --delete
venv/bin/python tools/add_user.py --list
```

- 服務會熱重載 `users.yaml`，**不必重啟**。
- systemd 的 `CFD_PLATFORM_DATA_DIR` 不是預設的 `./data` 時，加上 `--data-dir /path/to/data`。
- 登入記錄（時間、帳號、IP、成功與否）寫在 `data/login_log.jsonl`，admin 可在網頁 Settings 分頁底部的 Login Log 查看。

## 6. 設成開機自動啟動（systemd）

1. 編輯 `deploy/aero404.service`：改 `User=`、`WorkingDirectory=`、`CFD_PLATFORM_DATA_DIR=`、`ExecStart=` 裡的路徑（範例為 `/home/user/aero404_cfd_platform`）。
2. 安裝並啟動：
   ```bash
   sudo cp deploy/aero404.service /etc/systemd/system/aero404.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now aero404       # enable = 開機自動啟動
   ```
3. 常用指令：
   ```bash
   systemctl status aero404
   journalctl -u aero404 -f                  # 即時 log
   sudo systemctl restart aero404            # 後端程式更新後需要；前端檔案更新只需瀏覽器 Ctrl+Shift+R
   ```

> **為什麼設定檔裡有 `KillMode=process`？** 本機伺服器上的 job 是這個服務的子行程。systemd 預設
> （`control-group`）在 `restart` 時會把整個行程群組殺掉，連同跑了好幾天的 STAR-CCM+ 一起。
> `KillMode=process` 只停網頁服務，job 繼續跑；服務再啟動後會依 PID 與 exit code 檔接手，結束時正確標示 DONE / FAILED。
> 遠端（SSH）job 本來就不受影響。

## 7. HTTPS

登入 cookie 帶有 `Secure` 旗標，瀏覽器在純 `http://` 下（`localhost` 除外）會丟掉它，結果是「密碼正確卻登不進去」。擇一：

**A. nginx + HTTPS（建議）**

```bash
sudo apt install nginx
sudo mkdir -p /etc/ssl/aero404
sudo openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout /etc/ssl/aero404/server.key -out /etc/ssl/aero404/server.crt \
  -subj "/CN=cfd.example.internal"          # 自簽憑證；有正式憑證請直接使用
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/aero404
sudo ln -s /etc/nginx/sites-available/aero404 /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

然後用 `https://<主機>:6200` 開啟（埠號見 `nginx.conf.example`；自簽憑證瀏覽器會警告，確認後繼續）。uvicorn 維持只聽 `127.0.0.1:8080`。

**B. 僅限內網、純 HTTP 測試：** 在 `data/app_settings.yaml` 設 `cookie_secure: false`。不要在不受信任的網路使用。

## 8. Log 檢視與自動判定失敗

- Job Session 每列的 📄 按鈕會開啟該 case 的 `workflow.log`（1000 行一頁，⏮ ◀ ▶ ⏭ 翻頁；停在最後一頁時 SOLVING 的 job 每 3 秒自動更新）。每頁行數是 `frontend/static/js/app.js` 裡的 `logLines`（後端上限 2000）。
- 若 job 還在 SOLVING，而 `workflow.log` 出現指定文字，平台會停掉該 job 並標示 FAILED，原因顯示在列上，避免白白算完。預設規則為偵測 `Named object "domain.inlet" does not exist in manager`（CAD 有洞，內流場沒有 inlet）。
  自訂請在 `data/app_settings.yaml` 加入 `fail_log_rules`（範例已附在檔案中；一旦加入就**取代**內建預設）。
- 平台判定 FAILED 的其他情況：程序結束且 exit code 非 0、啟動失敗、服務重啟後找不到程序、管理員 Kill。macro 內部自己印出的 `[ERROR]` **不會**被判為失敗，請用上面的規則涵蓋。

## 9. 更新與備份

```bash
cd /home/user/aero404_cfd_platform
git pull
venv/bin/pip install -r requirements.txt
sudo systemctl restart aero404          # 不會中斷執行中的 job（KillMode=process）
```

備份：只需要 `data/` 資料夾（帳號、佇列 `jobs.json`、伺服器清單、登入記錄）。**不要把 `data/` 提交到 git**（已在 `.gitignore`）。

## 10. 測試

```bash
cd backend
python3 -m app.jobs.test_queue
python3 -m app.jobs.test_workflow
python3 -m unittest app.jobs.test_real_runner app.test_log_page
python3 -m unittest app.test_api app.test_integration app.test_frontend_assets
```

測試使用 `data.example/` 的暫存副本與測試帳號 `admin` / `changeme`，不會碰你的 `data/`。

## 11. 疑難排解

| 現象 | 原因與處理 |
|---|---|
| 密碼正確卻登不進去 | 用 `http://` 連線，Secure cookie 被丟掉 → 見第 7 節 |
| 登入回 429 | 同一 IP 5 分鐘內失敗超過 5 次，等待後再試 |
| 新增 case 後立刻 FAILED，`Failed to start` | 遠端 SSH 無法免密碼登入，或 `starccm_path` / `nfs_path` 錯誤；先在主機上手動 `ssh -o BatchMode=yes user@host true` |
| 遠端 job 的 log 打不開 | SSH 連不上該台，或 `nfs_path` 與實際路徑不符 |
| FAILED `Exit code: 1` 但 log 沒有錯誤 | 程序被外力結束（機器重開、NFS／磁碟故障）時，exit code 檔讀不到也會顯示 1；檢查 `journalctl -k` 與 `dmesg` |
| FAILED `Process no longer existed after service restart` | 服務重啟時該 job 的程序已不存在；確認 service 檔有 `KillMode=process` |
| 改了 `*.yaml` 沒生效 | 應立即生效；確認改的是 `CFD_PLATFORM_DATA_DIR` 指到的那份 `data/` |

## 12. 專案結構

```
backend/app/        FastAPI：routers/、jobs/（佇列、runner、workflow）、config/（熱重載）、auth、session
frontend/           templates/index.html、static/js/app.js（Alpine.js）、static/vendor/（Plotly、three.js）
data.example/       範例設定（假資料）→ 複製成 data/
deploy/             aero404.service、nginx.conf.example、install.sh
tools/add_user.py   帳號管理
docs/HANDOFF.md     早期設計文件（歷史資料，部分內容已過時）
```

## 授權

Copyright © 2026 Ping Lin · Non-Commercial Use Only（同網頁 About 頁所載）。
