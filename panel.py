import json
import pathlib
import subprocess
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from flask import Flask, jsonify, redirect, render_template, request, url_for

from upload import fetch_server_songs, get_failed_songs, get_uploaded_songs, run_upload


BASE_DIR = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "panel_config.json"
LOG_PATH = BASE_DIR / "panel.log"
FAILED_PATH = BASE_DIR / "upload_failed.json"


@dataclass
class PanelConfig:
    repo_url: str = "https://ese.tjadataba.se/ESE/ESE.git"
    repo_dir: str = str(BASE_DIR / "ESE")
    site_url: str = "https://taiko.asia"
    use_proxy: bool = False
    daily_time: str = "03:00"
    listen_host: str = "0.0.0.0"
    listen_port: int = 80


class SyncService:
    def __init__(self, config: PanelConfig) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.logs: deque[str] = deque(maxlen=500)
        self.state: dict[str, Any] = {
            "started_at": self._now(),
            "job_running": False,
            "current_job": "",
            "last_job_started_at": "",
            "last_job_finished_at": "",
            "last_job_result": "",
            "last_job_summary": {},
            "last_error": "",
            "last_git_sync_at": "",
            "last_auto_run_date": "",
        }
        self._log("服务已启动。")
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _log(self, message: str) -> None:
        line = f"[{self._now()}] {message}"
        self.logs.appendleft(line)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def snapshot(self) -> dict[str, Any]:
        repo_path = pathlib.Path(self.config.repo_dir)
        server_status = self.get_server_status()
        uploaded_songs = get_uploaded_songs()
        failed_songs = get_failed_songs()
        return {
            "config": asdict(self.config),
            "state": dict(self.state),
            "logs": list(self.logs),
            "repo_exists": repo_path.exists(),
            "repo_is_git": (repo_path / ".git").exists(),
            "server_status": server_status,
            "uploaded_songs": uploaded_songs,
            "failed_songs": failed_songs,
        }

    def update_config(self, form: dict[str, str]) -> None:
        self.config.repo_url = (form.get("repo_url") or self.config.repo_url).strip()
        self.config.repo_dir = (form.get("repo_dir") or self.config.repo_dir).strip()
        self.config.site_url = (form.get("site_url") or self.config.site_url).strip()
        self.config.daily_time = (form.get("daily_time") or self.config.daily_time).strip()
        self.config.listen_host = (form.get("listen_host") or self.config.listen_host).strip()
        self.config.listen_port = int((form.get("listen_port") or self.config.listen_port))
        self.config.use_proxy = form.get("use_proxy") == "on"
        save_config(self.config)
        self._log("配置已保存。")

    def _run_command(self, args: list[str], cwd: pathlib.Path | None = None) -> None:
        self._log(f"执行命令: {' '.join(args)}")
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.stdout.strip():
            for line in completed.stdout.splitlines():
                self._log(f"stdout: {line}")
        if completed.stderr.strip():
            for line in completed.stderr.splitlines():
                self._log(f"stderr: {line}")
        if completed.returncode != 0:
            raise RuntimeError(f"命令失败，退出码 {completed.returncode}: {' '.join(args)}")

    def get_server_status(self) -> dict[str, Any]:
        try:
            songs = fetch_server_songs(self.config.site_url, use_proxy=self.config.use_proxy)
            if songs is None:
                return {"ok": False, "message": "站点不可达或歌曲接口不可用", "song_count": 0}
            return {"ok": True, "message": "站点连接正常", "song_count": len(songs)}
        except Exception as exc:
            return {"ok": False, "message": str(exc), "song_count": 0}

    def ensure_repo_ready(self) -> None:
        repo_dir = pathlib.Path(self.config.repo_dir)
        parent_dir = repo_dir.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        if not repo_dir.exists():
            self._log(f"本地仓库不存在，开始 clone 到 {repo_dir}")
            self._run_command(["git", "clone", self.config.repo_url, str(repo_dir)])
        elif not (repo_dir / ".git").exists():
            raise RuntimeError(f"目录存在但不是 git 仓库: {repo_dir}")
        else:
            self._log(f"开始 pull 仓库: {repo_dir}")
            self._run_command(["git", "-C", str(repo_dir), "pull", "--ff-only"])

        self.state["last_git_sync_at"] = self._now()

    def _begin_job(self, name: str) -> bool:
        locked = self.lock.acquire(blocking=False)
        if not locked:
            self._log(f"请求的任务 {name} 被跳过，因为已有任务正在运行。")
            return False

        self.state["job_running"] = True
        self.state["current_job"] = name
        self.state["last_job_started_at"] = self._now()
        self.state["last_job_result"] = ""
        self.state["last_error"] = ""
        self.state["last_job_summary"] = {}
        self._log(f"任务开始: {name}")
        return True

    def _finish_job(self, name: str, result: str, summary: dict[str, Any] | None = None, error: str = "") -> None:
        self.state["job_running"] = False
        self.state["current_job"] = ""
        self.state["last_job_finished_at"] = self._now()
        self.state["last_job_result"] = result
        self.state["last_job_summary"] = summary or {}
        self.state["last_error"] = error
        self._log(f"任务结束: {name} -> {result}")
        if error:
            self._log(f"错误详情: {error}")
        self.lock.release()

    def run_sync_and_upload(self, automatic: bool = False) -> bool:
        job_name = "自动同步上传" if automatic else "手动同步上传"
        if not self._begin_job(job_name):
            return False

        try:
            self.ensure_repo_ready()
            summary = run_upload(
                ese_path=self.config.repo_dir,
                site_url=self.config.site_url,
                use_proxy=self.config.use_proxy,
                mode="upload",
                progress=self._log,
            )
            if automatic:
                self.state["last_auto_run_date"] = datetime.now().strftime("%Y-%m-%d")
            self._finish_job(job_name, "success", summary=summary)
            return True
        except Exception as exc:
            if automatic:
                self.state["last_auto_run_date"] = datetime.now().strftime("%Y-%m-%d")
            self._finish_job(job_name, "failed", error=str(exc))
            return False

    def run_pull_only(self) -> bool:
        job_name = "手动仓库同步"
        if not self._begin_job(job_name):
            return False

        try:
            self.ensure_repo_ready()
            self._finish_job(job_name, "success", summary={"git_sync": "ok"})
            return True
        except Exception as exc:
            self._finish_job(job_name, "failed", error=str(exc))
            return False

    def run_upload_only(self) -> bool:
        job_name = "手动上传"
        if not self._begin_job(job_name):
            return False

        try:
            self.ensure_repo_ready()
            summary = run_upload(
                ese_path=self.config.repo_dir,
                site_url=self.config.site_url,
                use_proxy=self.config.use_proxy,
                mode="upload",
                progress=self._log,
            )
            self._finish_job(job_name, "success", summary=summary)
            return True
        except Exception as exc:
            self._finish_job(job_name, "failed", error=str(exc))
            return False

    def run_scan_only(self) -> bool:
        job_name = "扫描站点缺失歌曲"
        if not self._begin_job(job_name):
            return False

        try:
            self.ensure_repo_ready()
            summary = run_upload(
                ese_path=self.config.repo_dir,
                site_url=self.config.site_url,
                use_proxy=self.config.use_proxy,
                mode="scan",
                progress=self._log,
            )
            self._finish_job(job_name, "success", summary=summary)
            return True
        except Exception as exc:
            self._finish_job(job_name, "failed", error=str(exc))
            return False

    def trigger_background(self, target: str) -> bool:
        mapping = {
            "sync_upload": self.run_sync_and_upload,
            "pull": self.run_pull_only,
            "upload": self.run_upload_only,
            "scan": self.run_scan_only,
        }
        action = mapping[target]
        thread = threading.Thread(target=action, daemon=True)
        thread.start()
        return True

    def clear_failed_marks(self) -> None:
        FAILED_PATH.write_text('{"failed": {}}\n', encoding="utf-8")
        self._log("已清空失败标记，后续可以重新尝试上传失败歌曲。")

    def _scheduler_loop(self) -> None:
        while True:
            try:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")
                current_hm = now.strftime("%H:%M")
                if current_hm >= self.config.daily_time and self.state["last_auto_run_date"] != today:
                    self._log("到达每日计划时间，开始自动同步上传。")
                    self.run_sync_and_upload(automatic=True)
            except Exception as exc:
                self._log(f"定时器异常: {exc}")
            time.sleep(30)


def load_config() -> PanelConfig:
    if not CONFIG_PATH.exists():
        config = PanelConfig()
        save_config(config)
        return config

    with CONFIG_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return PanelConfig(**payload)


def save_config(config: PanelConfig) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(asdict(config), handle, ensure_ascii=False, indent=2)


app = Flask(__name__)
service = SyncService(load_config())


@app.route("/")
def index():
    snapshot = service.snapshot()
    return render_template("index.html", **snapshot)


@app.route("/api/status")
def api_status():
    return jsonify(service.snapshot())


@app.post("/action/save-config")
def save_config_route():
    service.update_config(request.form.to_dict())
    return redirect(url_for("index"))


@app.post("/action/run/<target>")
def run_action(target: str):
    if target not in {"sync_upload", "pull", "upload", "scan"}:
        return jsonify({"success": False, "error": "unknown_target"}), 404

    accepted = service.trigger_background(target)
    return redirect(url_for("index")) if accepted else redirect(url_for("index"))


@app.post("/action/clear-failed")
def clear_failed():
    service.clear_failed_marks()
    return redirect(url_for("index"))


if __name__ == "__main__":
    config = load_config()
    app.run(host=config.listen_host, port=config.listen_port, threaded=True)
