# Taiko 自动同步上传工具

这个项目会基于 `upload.py` 自动完成下面几件事：

- 首次部署时自动 `clone https://ese.tjadataba.se/ESE/ESE.git`
- 后续每天按设定时间自动 `git pull --ff-only`
- 同步完成后自动把新歌曲上传到 `https://taiko.asia`
- 提供一个浏览器控制面板，默认监听 `80` 端口

## 启动

```powershell
pip install -r requirements.txt
python panel.py
```

或者直接双击 `start_panel.bat`。

## 控制面板

启动后访问：

```text
http://127.0.0.1/
```

可以在页面里完成：

- 保存仓库地址、本地目录、站点地址、每日执行时间
- 立即执行 `clone/pull + 上传`
- 单独执行 `clone/pull`
- 单独执行上传
- 扫描站点缺失歌曲
- 查看最近日志和最后一次任务结果

## 说明

- 默认上传站点是 `https://taiko.asia`
- 默认本地仓库目录是当前项目下的 `ESE`
- 默认不使用代理；如果需要代理，可在面板里启用 `127.0.0.1:10808`
- `uploaded.json` 会作为初始已上传歌曲清单，并在后续成功上传后继续追加
- 上传失败记录保存在 `upload_failed.json`，失败歌曲会被自动标记并在后续任务中跳过
- 面板日志保存在 `panel.log`

## Ubuntu 部署版

Ubuntu 版推荐使用：

- `gunicorn` 跑 Flask
- `nginx` 监听 `80` 端口并反向代理
- `systemd` 管理开机自启

已提供的部署文件：

- [wsgi.py](d:\DMH_Files\Python_projects\taiko-update2\wsgi.py)
- [setup.sh](d:\DMH_Files\Python_projects\taiko-update2\setup.sh)
- [deploy/ubuntu/install_ubuntu.sh](d:\DMH_Files\Python_projects\taiko-update2\deploy\ubuntu\install_ubuntu.sh)
- [deploy/ubuntu/taiko-sync-panel.service](d:\DMH_Files\Python_projects\taiko-update2\deploy\ubuntu\taiko-sync-panel.service)
- [deploy/ubuntu/taiko-sync-panel.nginx.conf](d:\DMH_Files\Python_projects\taiko-update2\deploy\ubuntu\taiko-sync-panel.nginx.conf)

### Ubuntu 快速部署

先把项目传到 Ubuntu 服务器任意目录，比如 `/root/taiko-update2`，然后执行：

```bash
cd /root/taiko-update2
chmod +x setup.sh
./setup.sh
```

脚本会自动：

- 安装 `python3`、`venv`、`git`、`nginx`
- 把项目部署到 `/opt/taiko-update2`
- 创建虚拟环境并安装依赖
- 安装 `systemd` 服务
- 安装 `nginx` 配置
- 启动面板并占用 `80` 端口

### setup.sh 可选环境变量

如果你要改默认部署位置或域名，可以这样执行：

```bash
APP_DIR=/opt/taiko-update2 \
NGINX_SERVER_NAME=example.com \
SITE_URL=https://taiko.asia \
REPO_URL=https://ese.tjadataba.se/ESE/ESE.git \
DAILY_TIME=03:00 \
./setup.sh
```

### Ubuntu 常用命令

查看服务状态：

```bash
sudo systemctl status taiko-sync-panel
```

重启服务：

```bash
sudo systemctl restart taiko-sync-panel
sudo systemctl restart nginx
```

查看实时日志：

```bash
sudo journalctl -u taiko-sync-panel -f
```

### Ubuntu 配置说明

- 默认项目部署目录是 `/opt/taiko-update2`
- `gunicorn` 监听 `127.0.0.1:8000`
- `nginx` 对外监听 `80`
- 面板保存的业务配置仍在 `panel_config.json`
- 如果你有自己的域名，可以把 `taiko-sync-panel.nginx.conf` 里的 `server_name _;` 改成你的域名
