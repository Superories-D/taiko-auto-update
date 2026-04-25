#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

APP_NAME="${APP_NAME:-taiko-sync-panel}"
APP_DIR="${APP_DIR:-/opt/taiko-update2}"
APP_USER="${APP_USER:-www-data}"
APP_GROUP="${APP_GROUP:-www-data}"
GUNICORN_BIND="${GUNICORN_BIND:-127.0.0.1:8000}"
SITE_URL="${SITE_URL:-https://taiko.asia}"
REPO_URL="${REPO_URL:-https://ese.tjadataba.se/ESE/ESE.git}"
DAILY_TIME="${DAILY_TIME:-03:00}"
NGINX_SERVER_NAME="${NGINX_SERVER_NAME:-_}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_PATH="/etc/systemd/system/${APP_NAME}.service"
NGINX_SITE="/etc/nginx/sites-available/${APP_NAME}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${APP_NAME}"
PANEL_CONFIG="${APP_DIR}/panel_config.json"

log() {
  echo
  echo "[$1] $2"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少命令: $1"
    exit 1
  fi
}

log "1/9" "检查系统环境"
require_command apt
require_command systemctl

log "2/9" "安装系统依赖"
$SUDO apt update
$SUDO apt install -y python3 python3-venv python3-pip git nginx rsync

log "3/9" "创建部署目录并同步项目文件"
$SUDO mkdir -p "${APP_DIR}"
$SUDO rsync -a \
  --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.venv/' \
  --exclude 'ESE/' \
  --exclude 'panel.log' \
  --exclude 'upload_failed.json' \
  --exclude 'panel_config.json' \
  "${SCRIPT_DIR}/" "${APP_DIR}/"

log "4/9" "准备 Python 虚拟环境并安装依赖"
$SUDO python3 -m venv "${APP_DIR}/.venv"
$SUDO "${APP_DIR}/.venv/bin/pip" install --upgrade pip
$SUDO "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

log "5/9" "写入运行配置"
$SUDO tee "${PANEL_CONFIG}" >/dev/null <<EOF
{
  "repo_url": "${REPO_URL}",
  "repo_dir": "${APP_DIR}/ESE",
  "site_url": "${SITE_URL}",
  "use_proxy": false,
  "daily_time": "${DAILY_TIME}",
  "listen_host": "127.0.0.1",
  "listen_port": 8000
}
EOF

log "6/9" "安装 systemd 服务"
$SUDO tee "${SERVICE_PATH}" >/dev/null <<EOF
[Unit]
Description=Taiko Auto Sync Upload Panel
After=network.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=${APP_DIR}/.venv/bin/gunicorn -w 2 -b ${GUNICORN_BIND} wsgi:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

log "7/9" "安装 nginx 配置"
$SUDO tee "${NGINX_SITE}" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${NGINX_SERVER_NAME};

    client_max_body_size 200M;

    location / {
        proxy_pass http://${GUNICORN_BIND};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 600s;
        proxy_connect_timeout 60s;
        proxy_send_timeout 600s;
    }
}
EOF

$SUDO ln -sf "${NGINX_SITE}" "${NGINX_ENABLED}"
$SUDO rm -f /etc/nginx/sites-enabled/default
$SUDO nginx -t

log "8/9" "设置权限并启动服务"
$SUDO chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"
$SUDO systemctl daemon-reload
$SUDO systemctl enable "${APP_NAME}"
$SUDO systemctl enable nginx
$SUDO systemctl restart "${APP_NAME}"
$SUDO systemctl restart nginx

SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -z "${SERVER_IP}" ]]; then
  SERVER_IP="127.0.0.1"
fi

log "9/9" "部署完成"
echo "面板地址: http://${SERVER_IP}/"
echo "服务状态: ${SUDO} systemctl status ${APP_NAME}"
echo "实时日志: ${SUDO} journalctl -u ${APP_NAME} -f"
echo "重新部署: cd ${SCRIPT_DIR} && bash setup.sh"
