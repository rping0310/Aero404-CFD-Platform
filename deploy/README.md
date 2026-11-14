# 部署

1. 將專案放到 `/home/userb/aero404_cfd_platform`，執行 `bash deploy/install.sh`。
2. 修改 `data/servers.yaml`、`data/users.yaml`、`data/app_settings.yaml`，並立即更換預設密碼。
3. 確認 Host 可免密 SSH 到每台遠端 server，且每台都掛載相同 NFS case 目錄。
4. 複製 `deploy/cfd_platform.service` 到 `/etc/systemd/system/`，執行 `sudo systemctl daemon-reload && sudo systemctl enable --now cfd_platform`。
5. 如需 Nginx，依 `nginx.conf.example` 設定；對外或跨不受信任網路使用時務必啟用 HTTPS。

查看狀態：`systemctl status cfd_platform`；查看 log：`journalctl -u cfd_platform -f`。
