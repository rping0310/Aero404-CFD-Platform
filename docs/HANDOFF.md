# Aero404 CFD Platform — 專案交接文檔

> **歷史文件。** 這是專案早期的設計交接文檔，部分內容（例如「明碼驗證」、設定範例）已過時；以根目錄 `README.md` 與 `data.example/` 為準。

## 1. 專案目標

將現有的 PyQt6 桌面版 **Aero404 CFD Viewer V2.1**（純瀏覽器）升級為 **Web-based CFD Platform**，新增多使用者登入、Job 排程佇列、多 Server 遠端執行、Admin 控管等功能。

### 核心需求
- **多人同時存取**：透過瀏覽器操作，不需要裝桌面程式
- **Job 排程**：使用者在網頁新增 CFD 案例，系統自動排隊執行 STAR-CCM+
- **多 Server 支援**：Host server + 其他 LAN server（透過 SSH），共用 NFS
- **不依賴 GNOME**：Web service 跑在 systemd，不會因為桌面當機而中斷
- **持久化**：關掉網頁、重啟 backend 都不會遺失佇列狀態
- **好部署、開源友善**：所有路徑/指令/參數都不寫死，透過設定檔調整

### 原始 CFD Viewer V2.1 功能（需完整搬遷到網頁）
- **Cases Tab**：讀 Result.dat 顯示表格（Cl/Cd, Cla, FW, RW, SW, UT, W DF, x balance, z balance）
- **Slices Tab**：PNG 序列播放（velocity/pressure 截面圖），zoom/pan/播放速度控制
- **Plots Tab**：CSV 即時監控（Residuals log scale, Forces linear scale），互動式圖表
- **3D Tab**：VTK 讀 q-criterion.stl，互動旋轉/縮放，>500萬面自動抽稀
- Main/Sub 雙 case 對比功能

---

## 2. 網頁分頁結構

```
Case List / Slices / Plots / 3D / Job Session / Settings / About
```

| Tab | 功能 | 技術對應 |
|-----|------|----------|
| Case List | Result.dat 表格，可排序、選 Main/Sub | 動態解析 key=value，不寫死欄位 |
| Slices | PNG 序列播放 | `<canvas>` / `<img>` |
| Plots | CSV 圖表（residual log scale / force linear scale） | Plotly.js |
| 3D | STL 互動式 3D 視圖 | three.js + STLLoader |
| Job Session | 佇列管理：Waiting/Solving/Done/Failed 列表 | WebSocket 或 polling |
| Settings | Admin 設定頁（app_settings / case_parameter default / servers 管理） | 動態表單 |
| About | 靜態資訊頁 | 純 HTML |

---

## 3. 系統架構

```
使用者 (Browser)
  ↓ HTTP/WebSocket
Nginx (reverse proxy, 可選)
  ↓
FastAPI (uvicorn) ← systemd service，不依賴 GNOME
  ↓
┌─────────────────────────────────┐
│ Backend                         │
│  ├── Auth（PBKDF2 雜湊驗證）            │
│  ├── Config（熱重載 YAML）       │
│  ├── Job Queue Manager          │
│  ├── Workflow Executor           │
│  ├── Job Runner                  │
│  │   ├── Local: subprocess       │
│  │   └── Remote: SSH + nohup     │
│  └── Viewer API（結果檔案讀取）   │
└─────────────────────────────────┘
  ↓
┌─────────────────────────────────┐
│ Storage                         │
│  ├── SQLite or JSON（佇列狀態）  │  ← 目前用 JSON (data/jobs.json)
│  ├── YAML（設定檔）              │
│  └── NFS（sim 檔案、結果）       │
└─────────────────────────────────┘
  ↓
┌─────────────────────────────────┐
│ Compute Servers                 │
│  ├── ServerA (local, 64 cores)     │
│  └── ServerB (remote, 32 cores)   │
│     ← SSH + nohup starccm+      │
│     ← 共用 NFS                   │
└─────────────────────────────────┘
```

---

## 4. 設定檔設計（全部不寫死）

### 4.1 `data/servers.yaml` — Server 註冊
- 編輯方式：直接改檔案，存檔即生效（mtime 熱重載）
- 不用重啟服務

```yaml
servers:
  - name: "ServerA"                    # 網站上顯示的名稱
    host: "127.0.0.1"              # IP（ZeroTier / LAN / 公網都行）
    ssh_user: "usera"
    ssh_port: 22
    ssh_key_path: "~/.ssh/id_ed25519"
    total_cores: 64
    reserved_cores: 2               # 保留給系統
    nfs_path: "/home/usera/CFD_cloud_NFS"
    starccm_path: "/home/usera/20.06.007-R8/STAR-CCM+20.06.007-R8/star/bin/starccm+"
    enabled: true
```

### 4.2 `data/users.yaml` — 帳號
- **明碼存放**（內部工具，不需要加密）
- 兩種角色：admin / user

```yaml
users:
  - name: "admin"
    role: "admin"       # kill case、覆蓋、調順序、改設定
    password: "changeme"
  - name: "user_a"
    role: "user"        # 新增 case、查看結果
    password: "abc123"
```

### 4.3 `data/app_settings.yaml` — 全域設定
- 網站 Admin 設定頁 **和** 後端直接編輯都能改
- 衝突偵測：網站存檔時比對 mtime，若被後端動過會提示重新載入

```yaml
simulation_type_folders:        # simulation_type → 資料夾名稱
  1: "Straight"
  2: "Yaw"
  3: "Cornering"

nfs_root: "/home/usera/CFD_cloud_NFS"
template_folder_name: "template_CFD"  # 三個 type 共用同一版本
entry_macro: "batch_run.java"                      # 執行入口，一支跑完全部

uploaded_file_pattern: "TH1[123]_\\d+\\.x_t"
uploaded_file_display_name: "Wing/Diffuser Package (TH11/TH12/TH13_XXX.x_t)"
max_upload_size_mb: 500

status_poll_interval_sec: 10
session_timeout_min: 480
site_title: "CFD Viewer"
viewer_data_path: "/home/usera/CFD_cloud_NFS"
```

### 4.4 `data/case_parameter_default.dat` — 預設模擬參數
- 只有 Admin 設定頁能改這份 master
- 建 case 時**複製一份**到 case 資料夾，使用者在表單改的只影響 case 自己那份
- 格式：`key = value`，網站動態解析產生表單（不寫死欄位名）

```
simulation_type = 1
corner_radius = 10
free_stream_velocity = 12.5
pitch_angle = 1
roll_angle = 0
yaw_angle = 0
ride_height = 0.035
steering_angle = 0
stopping_iteration = 3000
```

### 4.5 `data/workflow_template.yaml` — Job 執行步驟
- 進階設定，直接改檔案

```yaml
steps:
  - action: copy_folder
    source: "{{nfs_root}}/{{sim_type_folder}}/{{template_folder_name}}"
    dest: "{{nfs_root}}/{{sim_type_folder}}/{{case_name}}"
  - action: rename_file
    source: "{{case_folder}}/{{template_sim_filename}}"
    dest: "{{case_folder}}/{{case_name}}.sim"
  - action: save_uploaded_file
    dest: "{{case_folder}}/{{uploaded_filename}}"
  - action: write_case_parameter
    dest: "{{case_folder}}/case_parameter.dat"
  - action: run_command
    command: "{{starccm_path}} -np {{cores}} -batch {{entry_macro}} {{case_name}}.sim"
    cwd: "{{case_folder}}"
    log_to: "{{case_folder}}/workflow.log"
    pid_to: "{{case_folder}}/run.pid"
```

---

## 5. Job 排程規則（已實作並測試通過）

### 5.1 核心分配

```
max_cores = total_cores - reserved_cores

選「一半」(half)：
  → 固定分配 max_cores // 2 顆（例如 62//2 = 31）
  → 只要 available_cores >= half_cores 就立刻開始
  → 兩個 half job 同時跑 = 各 31 顆，正好吃滿 62 顆

選「全部」(all)：
  → 必須等到 used_cores == 0（完全沒人在跑）
  → 拿走全部 max_cores
```

### 5.2 排程行為

- **WAITING 的 half job 不會被卡住的 all job 擋住**：排程器會跳過 all job 繼續檢查後面的 half job
- **「兩個兩個」補位**：一個 half 跑完 → 釋放核心 → 從 WAITING queue 自動拉下一個 half 開始跑
- **all 要等「所有」都結束**：不是只等第一個，而是 used_cores 歸零才開始

### 5.3 狀態機

```
WAITING → SOLVING → DONE (exit code 0)
                  → FAILED (exit code != 0, 或被 kill)
```

- `killed` 是 bool flag，疊加在 FAILED 上（區分自然失敗 vs admin 手動 kill）

### 5.4 覆蓋保護

```
check_can_overwrite(case_name):
  WAITING 或 SOLVING → 禁止覆蓋（回傳 False）
  DONE 或 FAILED → 允許覆蓋，但跳警告讓使用者確認
  完全沒紀錄 → 允許，但如果資料夾已存在也跳警告

資料夾存在 + 佇列允許 → 前端彈 Modal 確認 → force=true 重新送出
```

### 5.5 持久化與重啟恢復

- 佇列狀態存 `data/jobs.json`
- Backend 重啟時：
  - SOLVING 的 job → 用 `os.kill(pid, 0)` 檢查 pid 是否存活
  - 存活 → 繼續監控（不誤判）
  - 不在了 → 標記 FAILED

---

## 6. 新增 Case 流程（已實作 WorkflowExecutor）

### 6.1 使用者操作

```
網站「新增 Case」表單：
  1. 填 Case 名稱（例如 TH12_002）→ 會變成資料夾名和 .sim 檔名
  2. 選 Simulation Type（Straight / Yaw / Cornering）→ 決定資料夾路徑
     → case_parameter 裡的 simulation_type 欄位自動鎖定同步
  3. 上傳 TH1X_XXX.x_t（只有這一個檔案需要上傳，body.x_t / unsprung.x_t 在 template 裡）
  4. 編輯 case_parameter（表單帶入 default 值，可改可不改）
  5. 選 Server（ServerA / ServerB，顯示 Available: 33/64）
  6. 選核心模式（一半 / 全部）
  7. 送出
```

### 6.2 後端檔案操作（WorkflowExecutor.prepare_case）

```python
# 對應實際的 terminal 操作：
# cd ~/CFD_cloud_NFS/Straight
# cp -r template_CFD TH12_002
# cd TH12_002
# mv template.sim TH12_002.sim
# (放上傳的 parasolid)
# (寫入 case_parameter.dat)
# starccm+ -np 31 -batch batch_run.java TH12_002.sim

1. 檢查 template 存在
2. 檢查資料夾是否已存在（覆蓋保護）
3. 複製整個 template 資料夾（含 .sim, body.x_t, unsprung.x_t, macros）
4. Rename .sim 檔案
5. 放上傳的 TH1X_XXX.x_t
6. 寫入使用者編輯過的 case_parameter.dat
7. 組出 command_template（含 {{cores}} 佔位符）
8. 交給 JobQueueManager 排隊
```

### 6.3 NFS 目錄結構

```
~/CFD_cloud_NFS/
├── Straight/
│   ├── template_CFD/     ← template（唯讀基準）
│   │   ├── template.sim
│   │   ├── body.x_t
│   │   ├── unsprung.x_t
│   │   ├── batch_run.java
│   │   ├── gui_run.java
│   │   ├── geometryimport.java
│   │   ├── transform.java
│   │   ├── boundarysetup_1/2/3.java
│   │   ├── mesh.java
│   │   ├── solve.java
│   │   └── postprocess.java
│   │
│   ├── TH12_001/                      ← case（由 workflow 建立）
│   │   ├── TH12_001.sim
│   │   ├── body.x_t
│   │   ├── unsprung.x_t
│   │   ├── TH11_228.x_t              ← 使用者上傳的
│   │   ├── case_parameter.dat         ← 使用者編輯過的
│   │   ├── batch_run.java
│   │   ├── workflow.log
│   │   ├── run.pid
│   │   ├── Result.dat                 ← 結果
│   │   ├── Residuals_final.csv
│   │   └── ...
│   └── TH12_002/
│
├── Yaw/
│   └── template_CFD/
└── Cornering/
    └── template_CFD/
```

---

## 7. 多 Server 遠端執行設計（待實作）

### 7.1 SSH + nohup 方案

```bash
# Host server 透過 SSH 在 Remote server 執行：
ssh -i ~/.ssh/id_ed25519 usera@192.0.2.20 \
  "cd /home/usera/CFD_cloud_NFS/Straight/TH12_002 && \
   nohup starccm+ -np 16 -batch batch_run.java TH12_002.sim \
   > workflow.log 2>&1 & echo \$! > run.pid"
```

- `nohup` → SSH 斷線不影響 process
- PID 寫到 NFS 上的 `run.pid` → Host 可以隨時讀取

### 7.2 監控（不需要持續 SSH 連線）

```python
# 檢查 process 存活：短暫 SSH
ssh remote "ps -p $(cat /NFS/case_xxx/run.pid) -o pid= 2>/dev/null"

# 讀 log：直接讀 NFS（不需要 SSH）
with open("/path/to/NFS/case_xxx/workflow.log") as f:
    log = f.read()

# Kill（admin）：短暫 SSH
ssh remote "kill $(cat /NFS/case_xxx/run.pid)"

# CPU 使用率：短暫 SSH
ssh remote "mpstat 1 1"
```

### 7.3 本機 vs 遠端的判斷

```python
# 如果 server.host 是 127.0.0.1 或 localhost → 用 subprocess
# 否則 → 用 SSH

class RealRunner(JobRunner):
    def start(self, job_id, cores, work_dir, command):
        server = self._get_server(...)
        if self._is_local(server):
            return self._start_local(command, work_dir)
        else:
            return self._start_ssh(server, command, work_dir)
```

---

## 8. 前端技術選型

| 需求 | 選擇 | 原因 |
|------|------|------|
| 前端框架 | Alpine.js（或 Vue CDN） | 不需要 npm/build，維護簡單 |
| 3D 渲染 | three.js + STLLoader | 瀏覽器端 WebGL，server 不需要 VTK/OpenGL |
| 圖表 | Plotly.js | 互動式，支援 log scale，hover 顯示數值 |
| CSS | Tailwind CDN（或簡單自寫） | 快速開發 |
| 即時更新 | WebSocket 或 polling | Job 狀態、CPU load |

### Server 端完全不需要桌面版的相依套件
- ❌ 不需要 VTK、PyQt6、Mesa、OpenGL、PyQtGraph
- ✅ 只需要 Python 3.10+、FastAPI、uvicorn

---

## 9. 帳號與權限

| 角色 | 權限 |
|------|------|
| admin | 所有 user 權限 + kill case + 覆蓋 case + 調整 queue 順序 + 編輯 Settings |
| user | 查看 Case List/Slices/Plots/3D + 新增 case |

登入方式：輸入 name + password，對照 `data/users.yaml`

---

## 10. 已完成的程式碼（10 個檔案，共 ~1100 行）

### 10.1 `backend/app/config/loader.py`（88 行）
- `HotReloadFile` 類別：包裝一份設定檔，比對 mtime 決定是否重新讀取
- `ConfigConflictError`：Admin 表單存檔時偵測到檔案被後端動過
- `get()` → 回傳快取內容（mtime 沒變就不重讀）
- `write(content, expected_mtime)` → 寫入時可帶 mtime 做衝突偵測

### 10.2 `backend/app/config/settings.py`（81 行）
- 統一存取入口：`get_servers()`, `get_users()`, `get_app_settings()`, `get_workflow_steps()`
- `parse_key_value_file()` / `write_key_value_file()` → 處理 `case_parameter.dat` 格式
- `DATA_DIR` 可透過環境變數 `CFD_PLATFORM_DATA_DIR` 覆蓋

### 10.3 `backend/app/auth.py`（40 行）
- `authenticate(name, password)` → 回傳 `AuthResult(ok, name, role, error)`
- 明碼比對，不用 bcrypt
- 帳號來自 `users.yaml`，熱重載

### 10.4 `backend/app/jobs/models.py`（66 行）
- `JobStatus`: WAITING / SOLVING / DONE / FAILED
- `CoreMode`: HALF / ALL
- `Job` dataclass：case_name, server_name, mode, added_by, status, cores_allocated, pid, command_template, work_dir, created_at, started_at, finished_at, killed, error_message, queue_position
- `to_dict()` / `from_dict()` → JSON 序列化
- `runtime_seconds()` → 從開始到現在（或結束）的秒數

### 10.5 `backend/app/jobs/runner.py`（100 行）
- `JobRunner` ABC：`start()`, `is_alive()`, `get_return_code()`, `kill()`
- `FakeSleepRunner`：測試用，執行 `sleep N` 模擬 starccm+
- `start()` 接受 `command` 參數，會寫入 log 但實際跑 sleep
- **待實作**：`RealRunner`（本機 subprocess + SSH 遠端）

### 10.6 `backend/app/jobs/queue_manager.py`（250 行）
- `JobQueueManager`：排程核心
- **核心分配**：half = 固定 `max_cores // 2`，all = 等 `used_cores == 0`
- **排程器**：`_try_schedule()` 依 queue_position 順序，能跑就跑，不能跑跳過（不餓死 half）
- **持久化**：`data/jobs.json`，每次狀態變動都寫入
- **重啟恢復**：`_recover_on_startup()` 檢查 SOLVING 的 pid 是否存活
- **覆蓋保護**：`check_can_overwrite()` → WAITING/SOLVING 禁止，DONE/FAILED 允許
- **Admin 操作**：`kill_job()`, `reorder_queue()`
- **輪詢**：`poll()` 檢查所有 SOLVING job 的 process 是否結束，結束就釋放核心觸發下一輪排程

### 10.7 `backend/app/jobs/workflow.py`（151 行）
- `WorkflowExecutor.prepare_case()` → 所有檔案操作（複製 template、改名、放上傳檔案、寫參數）
- `validate_uploaded_filename()` → 用 app_settings 的 regex 驗證
- 回傳 `CaseCreationResult(ok, case_folder, command_template, error)`
- `command_template` 包含 `{{cores}}` 佔位符，由 queue_manager 在啟動時填入

### 10.8 `backend/app/jobs/test_queue.py`（295 行）— 5/5 通過
- 測試 1：half 模式固定半數分配 + 第三個補位（兩個兩個）
- 測試 2：all 模式等所有 job 結束 + half 不被 all 餓死
- 測試 3：一個跑完下一個自動接上
- 測試 4：覆蓋保護規則
- 測試 5：backend 重啟後佇列不消失

### 10.9 `backend/app/jobs/test_workflow.py`（270 行）— 5/5 通過
- 測試 1：正常建立 case（驗證所有檔案到位 + command_template）
- 測試 2：資料夾已存在警告 + force 覆蓋
- 測試 3：template 不存在錯誤處理
- 測試 4：上傳檔名驗證（TH11/12/13 合法，其他拒絕）
- 測試 5：simulation_type 正確對應資料夾

---

## 11. 待實作（按優先順序）

### 11.1 Real Runner（替換 FakeSleepRunner）
```
檔案：backend/app/jobs/real_runner.py
功能：
  - 本機執行：subprocess.Popen(command, shell=True, ...)
  - 遠端執行：SSH + nohup（見第 7 節設計）
  - is_alive()：本機用 os.kill(pid,0)，遠端用 SSH "ps -p PID"
  - kill()：本機用 os.kill(SIGTERM)，遠端用 SSH "kill PID"
  - 判斷本機/遠端：server.host == 127.0.0.1 → 本機，否則 SSH
測試：
  - 本機 subprocess 跑真指令（echo / sleep）
  - SSH 到 localhost 模擬遠端（如果環境允許）
  - kill + is_alive 邊界情況
```

### 11.2 Monitor（背景輪詢）
```
檔案：backend/app/jobs/monitor.py
功能：
  - 背景 asyncio task，每 status_poll_interval_sec 呼叫 queue_manager.poll()
  - 收集各 server 的 CPU load（本機讀 /proc/stat，遠端 SSH mpstat）
  - 提供 API 給前端查詢即時 CPU / available cores
```

### 11.3 FastAPI 主程式
```
檔案：backend/app/main.py
功能：
  - 初始化 JobQueueManager（用 RealRunner）
  - 初始化 WorkflowExecutor
  - 啟動背景 Monitor task
  - 掛載靜態檔案（frontend/）
  - Session 管理（cookie-based，session_timeout_min 可設定）
框架：
  - FastAPI + uvicorn
  - Jinja2 templates（或純 API + 前端 SPA）
```

### 11.4 API 路由
```
backend/app/routers/
├── auth.py         POST /api/login, POST /api/logout, GET /api/me
├── jobs.py         POST /api/jobs（新增 case）
│                   GET /api/jobs（列表，可篩 server/status）
│                   DELETE /api/jobs/{id}（kill，admin only）
│                   PUT /api/jobs/reorder（admin only）
│                   POST /api/jobs/upload（上傳 parasolid）
├── servers.py      GET /api/servers（列表 + available cores）
├── settings.py     GET /api/settings, PUT /api/settings（admin only）
│                   GET /api/settings/case_parameter_default
│                   PUT /api/settings/case_parameter_default（admin only）
└── viewer.py       GET /api/viewer/cases（Case List 資料）
                    GET /api/viewer/case/{name}/result（Result.dat）
                    GET /api/viewer/case/{name}/slices/{type}（PNG 列表）
                    GET /api/viewer/case/{name}/slice/{type}/{index}（單張 PNG）
                    GET /api/viewer/case/{name}/csv（CSV 列表）
                    GET /api/viewer/case/{name}/csv/{filename}（CSV 內容→JSON）
                    GET /api/viewer/case/{name}/stl（STL 檔案位元流）
```

### 11.5 前端頁面
```
frontend/
├── templates/
│   └── index.html          # SPA 主頁（所有 tab 在同一頁切換）
├── static/
│   ├── js/
│   │   ├── app.js          # Alpine.js / Vue 主邏輯
│   │   ├── viewer.js       # Case List + Slices + Plots + 3D
│   │   ├── job_session.js  # Job Session 頁面邏輯
│   │   └── settings.js     # Settings 頁面邏輯
│   ├── css/
│   │   └── style.css
│   └── img/
│       └── favicon.png     ✅ 已有（aero404 哭臉圖）
```

#### Job Session 頁面 UI 草圖
```
┌───────────────────────────────────────────┐
│ Job Session                [+ Add Case]   │
├───────────────────────────────────────────┤
│ Server: ServerA (Available: 33/64)           │
│ Server: ServerB (Available: 28/32)          │
├────┬──────────┬────────┬──────┬──────┬────┤
│ #  │ Name     │ Status │Cores │ By   │    │
├────┼──────────┼────────┼──────┼──────┼────┤
│ 1  │ TH12_002 │SOLVING │ 31   │admin │Kill│
│ 2  │ TH12_003 │SOLVING │ 31   │user_a │Kill│
│ 3  │ TH12_004 │WAITING │ -    │user_b │ ↑↓ │
├────┼──────────┼────────┼──────┼──────┼────┤
│ 4  │ TH12_001 │ DONE   │ 31   │admin │    │
│ 5  │ TH11_999 │ FAILED │ 62   │user_a │    │
└────┴──────────┴────────┴──────┴──────┴────┘
```

#### 新增 Case 表單
```
┌───────────────────────────────────────────┐
│ 新增 Case                                  │
├───────────────────────────────────────────┤
│ Case 名稱: [TH12_002        ]              │
│ Simulation Type: ○Straight ○Yaw ○Cornering│
│ 上傳檔案: [選擇檔案...]                     │
│                                            │
│ Case Parameters:                           │
│   simulation_type: [1    ] ← 自動鎖定      │
│   corner_radius:   [10   ]                 │
│   free_stream_velocity: [12.5]             │
│   ...                                      │
│                                            │
│ Server: ○ServerA (33/64) ○ServerB (28/32)      │
│ Cores:  ○一半  ○全部                       │
│                                [送出]       │
└───────────────────────────────────────────┘
```

### 11.6 部署
```
deploy/
├── install.sh          # 安裝相依套件、建立 venv、初始化 data/
├── cfd_platform.service # systemd unit file
├── nginx.conf.example   # Nginx reverse proxy 範例
└── README.md            # 部署說明
```

#### systemd service
```ini
[Unit]
Description=Aero404 CFD Platform
After=network.target

[Service]
Type=simple
User=user
WorkingDirectory=/home/usera/aero404_cfd_platform
ExecStart=/home/usera/aero404_cfd_platform/venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8080
Restart=always
Environment="DISPLAY="
Environment="PATH=..."

[Install]
WantedBy=multi-user.target
```

---

## 12. 設計決策備忘

| 決策 | 結論 | 原因 |
|------|------|------|
| 密碼 | 明碼 | 內部工具，簡化 |
| 持久化 | JSON 檔案 | 不需要裝 DB |
| 設定格式 | YAML | 人類可讀，好編輯 |
| 設定載入 | mtime 熱重載 | 改檔案不用重啟 |
| case_parameter 表單 | 動態解析 key=value | 不寫死欄位，換團隊不用改 code |
| Result.dat 表格 | 同上 | 不寫死那 9 個欄位 |
| half 核心分配 | 固定 max_cores//2 | 不是遞減式 |
| 前端框架 | Alpine.js + CDN libs | 不需要 npm/build |
| 3D 渲染 | three.js（瀏覽器端） | Server 不需要 VTK/OpenGL |
| 檔案上傳 | 直接 HTTP multipart | 比 Google Drive 簡單 |
| 遠端執行 | SSH + nohup | 只有 host server 裝東西 |
| template 版本 | 三個 type 共用一個版本號 | 同步升版 |
| 外網存取 | 先用 ZeroTier 測試，之後改 router port forwarding | 彈性 |

---

## 13. 執行測試

```bash
cd aero404_cfd_platform/backend

# Job Queue 排程測試（5 個情境）
python3 -m app.jobs.test_queue

# Workflow 檔案操作測試（5 個情境）
python3 -m app.jobs.test_workflow
```

---

## 14. 原始 CFD Viewer V2.1 程式碼

原始碼在 `cfd_viewer_V2_1.py`（1828 行，PyQt6），重要的資料解析邏輯：

### Result.dat 解析
```python
# 格式：mean_Cl/Cd = 1.135077
# 顯示映射：mean_Cla → Cla, mean_RW_DF → RW, Aero_balance_x → x balance
# 網頁版應該動態解析所有 key=value，不寫死映射
```

### CSV 解析（Plots）
```python
# 依檔名判斷類型：
# 包含 "residual" or "resid" → log scale (y: 1e-6 ~ 1.0)
# 其他 → linear scale (y: 0 ~ max)
# 第一行是 header，第一列是 Iteration
# Header 自動清理引號和多餘空白
```

### Slices
```python
# 資料夾結構：case_folder/x_direction_velocity/*.png
# 依檔名排序播放
```

### 3D (STL)
```python
# 讀 q-criterion.stl
# >500 萬面自動抽稀（vtkQuadricDecimation, reduction=0.7）
# 網頁版用 three.js，瀏覽器端渲染，不需要 server 端 VTK
```

---

## 15. 相依套件

### Server 端（Python）
```
fastapi
uvicorn
python-multipart    # 檔案上傳
pyyaml              # 設定檔
aiofiles            # 非同步檔案讀取（可選）
```

### 前端（CDN，不需要安裝）
```
Alpine.js           # 前端框架
three.js            # 3D STL 渲染
Plotly.js           # 圖表
Tailwind CSS        # 樣式（可選）
```

### 不需要（桌面版才用到的）
```
❌ PyQt6, VTK, PyQtGraph, Mesa, OpenGL drivers
```

---

## 16. 2026-09-19 續作狀態

已完成原「待實作」中的主要可運行骨架：

- `RealRunner`：本機 process group、遠端 SSH + nohup/setsid、PID 與 exit code 落盤、kill 與重啟後監控。
- `JobMonitor`：背景 poll、local `/proc/stat` 與 remote `mpstat` CPU 資訊。
- FastAPI 主程式與 cookie session；Auth、Jobs、Servers、Settings、Viewer API。
- 新增 Case multipart 流程已完整串到 WorkflowExecutor 和 JobQueueManager。
- Viewer API：動態 Result.dat、slice PNG、CSV JSON、STL 串流，含路徑穿越防護。
- Alpine.js SPA：登入、Case List、Slices 播放、Plotly 圖表、three.js STL、Job Session、新增 Case、Settings。
- systemd、Nginx、安裝腳本與部署文件。
- 新增 RealRunner 單元測試與 FastAPI smoke test；既有 queue/workflow 測試保留。

部署前仍需在實際 Linux/NFS/STAR-CCM+ 環境做整合驗收，尤其是遠端 SSH PID/kill、NFS 掛載路徑、STAR-CCM+ 指令與大型 STL 瀏覽器效能。前端 CDN 資源若部署網路無法連外，需改為下載後由 `/static` 本機提供。
