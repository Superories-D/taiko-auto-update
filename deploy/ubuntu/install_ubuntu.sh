#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/taiko-update2"
SERVICE_NAME="taiko-sync-panel"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"
PROJECT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "[1/8] 安装系统依赖"
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx rsync

echo "[2/8] 创建部署目录"
sudo mkdir -p "${APP_DIR}"
sudo rsync -a --delete "${PROJECT_SOURCE_DIR}/" "${APP_DIR}/"

echo "[3/8] 准备 Python 虚拟环境"
sudo python3 -m venv "${APP_DIR}/.venv"
sudo "${APP_DIR}/.venv/bin/pip" install --upgrade pip
sudo "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "[4/8] 设置目录权限"
sudo chown -R www-data:www-data "${APP_DIR}"

echo "[5/8] 安装 systemd 服务"
sudo cp "${APP_DIR}/deploy/ubuntu/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "[6/8] 安装 nginx 站点配置"
sudo cp "${APP_DIR}/deploy/ubuntu/${SERVICE_NAME}.nginx.conf" "${NGINX_SITE}"
sudo ln -sf "${NGINX_SITE}" "${NGINX_ENABLED}"
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx

echo "[7/8] 启动服务"
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl restart nginx

echo "[8/8] 完成"
echo "面板地址: http://$(hostname -I | awk '{print $1}')/"
echo "服务状态: sudo systemctl status ${SERVICE_NAME}"
echo "日志查看: sudo journalctl -u ${SERVICE_NAME} -f"
