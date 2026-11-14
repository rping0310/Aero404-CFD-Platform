# Liquid Glass 整合與上機驗證

整合目標為 `aero404_debug/`，不是 `github-release-v1.1/`。
保留原有 FastAPI 路由、角色檢查、排程器、Plotly、STL Web Worker、切片操作。
沒有把靜態原型的假資料或示範按鈕邏輯放進正式平台。

## 本次內容

- `frontend/templates/index.html`：玻璃版面、Gallery、各 Plots 獨立 CSV 選單。
- `frontend/static/css/liquid-platform.css`：中性 Smoke／Light／Dark、工作列、內部捲動、垂直曲線清單。
- `frontend/static/js/liquid-platform.js`：包裝原平台的外觀狀態、伺服器共用 Gallery、工作列互動。
- `frontend/static/js/app.js`：各圖獨立 CSV、保留原 API；Plotly.react、場景更新通知；登出清理播放及 log timer。
- `frontend/static/js/glass-overlay.js` 與本機 vendor：工作列折射實際工作區快照；更新中退回原生模糊。
- Server CPU 使用後端既有 `metrics.cpu_percent`，沒有資料顯示 `CPU —`，不使用原型示範值。
- Gallery 僅最右邊一個匯入框，僅 admin 可上傳／切換；所有登入者共用同一背景，既有用戶端約每 5 秒同步。
- 照片存於 `DATA_DIR/wallpapers/<id>.jpg`，選用狀態與清單在 `gallery.json`。
  未設定 `CFD_PLATFORM_DATA_DIR` 時預設是平台路徑下的 `data/wallpapers/`。
  資料夾自動建立，服務帳號需要寫入權限；部署／容器需保留此資料夾並納入備份。
  後端驗證解碼 JPEG/PNG/WebP，上限 25 MB／24 百萬像素，縮至最長 2200px、移除影像中繼資料並另存 JPEG。
  舊瀏覽器 IndexedDB 照片不自動上傳，需由管理員重新上傳。外觀模式、模糊等仍屬個人設定。

## 本機測試

在本目錄執行：

```powershell
python tests/test_liquid_platform.py
node frontend/static/js/test-case-identity.cjs
cd backend
python -m unittest app.routers.test_viewer_identity
```

瀏覽器測試使用真實 FastAPI 路由、登入 cookie、暫存案例與設定；替換服務生命週期，
停用派工、SSH 與求解程序。測試資料包括 10 條曲線、兩種案例、PNG、CSV 及小型 STL。
需要 FastAPI、uvicorn、httpx、PyYAML、Pillow、Playwright 及 Edge。
這不等於已驗證真實 Linux、NFS、SSH 或 STAR-CCM+。

## 上機前

1. 備份伺服器現有 `frontend/`、`backend/` 與 `data/`；保留可回退版本。
2. 本地 `aero404_debug/` 沒有部署用 `data/`。沿用伺服器正式設定與帳號，
   **不要上傳測試暫存資料，也不要用範例覆蓋現有 jobs.json/users.yaml**。
3. 對照本機與伺服器的後端 API 版本。這次前端保留 debug 版本的 API 合約；
   若伺服器版本不同，不應直接盲目覆蓋。
4. 安排測試環境／測試埠，部署完整 `frontend/`，包括新增 CSS、JS、圖片、vendor 授權，
   同步部署新增 `backend/app/routers/gallery.py`、修改後的 `backend/app/main.py` 與 `requirements.txt`。
   安裝更新依賴（新增 Pillow）。不要清空現有運算工作；後端更新與重啟需按既有維運程序安排。
5. 使用 HTTPS。debug 的登入 cookie 是 Secure；遠端一般 HTTP 不適合驗證登入。
6. 先以 visitor 檢查 Case List、切片同步／播放／縮放、10 條以上曲線勾選／捲動、
   真實大型 STL、Gallery、工作列、CPU 更新、離線資產；再檢查 user/admin 權限。
7. 管理員以專用測試案例驗證上傳、建立、排隊、log 分頁、備註、插隊、Kill、
   刪除測試紀錄及設定儲存。不要對現有正式案例做破壞性測試。

## 已知限制

- 折射為 DOM 快照，不等同直接讀取瀏覽器合成畫面。巢狀 backdrop-filter、
  Plotly SVG、WebGL 大模型在不同瀏覽器的快照結果可能有差異；內容更新時暫用原生模糊。
- 3D WebGL 畫布可能無法提供可擷取像素；不為外觀而改動科學畫布的繪圖緩衝設定。
- 常變動的播放畫面可持續使用原生模糊，避免每影格重新擷取。
- 減少透明度／玻璃效果關閉時停用光學層；工作列收回時不擷取內容。
- **目前維持單一 Uvicorn worker**：登入 session、工作佇列與排程監控均為程序內狀態，
  共用圖庫寫入鎖也是程序內鎖。增加 `--workers` 會造成狀態分離與重複排程風險。
  多核心仍可供科學求解、檔案處理與其他服務使用；若要多 worker，應先抽離單例排程器，
  並以共享 session／佇列／交易式資料庫取代程序內與 JSON 狀態。
