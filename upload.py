import argparse
import json
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional
from urllib.parse import urljoin

import requests


ProgressCallback = Optional[Callable[[str], None]]


@dataclass
class UploadSummary:
    scanned: int = 0
    uploaded: int = 0
    skipped_uploaded: int = 0
    skipped_missing_tja: int = 0
    skipped_missing_ogg: int = 0
    failed: int = 0
    missing_on_server: int = 0
    mode: str = "upload"

    def to_dict(self) -> Dict[str, int | str]:
        return {
            "scanned": self.scanned,
            "uploaded": self.uploaded,
            "skipped_uploaded": self.skipped_uploaded,
            "skipped_missing_tja": self.skipped_missing_tja,
            "skipped_missing_ogg": self.skipped_missing_ogg,
            "failed": self.failed,
            "missing_on_server": self.missing_on_server,
            "mode": self.mode,
        }


KNOWN_TYPES = {
    "01 Pop",
    "02 Anime",
    "03 Vocaloid",
    "04 Children and Folk",
    "05 Variety",
    "06 Classical",
    "07 Game Music",
    "08 Live Festival Mode",
    "09 Namco Original",
    "10 Taiko Towers",
    "11 Dan Dojo",
}


def _emit(message: str, progress: ProgressCallback = None) -> None:
    if progress is not None:
        progress(message)
    else:
        print(message)


def _get_basedir() -> str:
    try:
        root = pathlib.Path(__file__).resolve().parent
        sys.path.insert(0, str(root / "taiko-web2"))
        import config  # type: ignore

        base = getattr(config, "BASEDIR", "/")
        if not isinstance(base, str):
            return "/"
        if not base.endswith("/"):
            base += "/"
        return base
    except Exception:
        return "/"


def _classify_name(name: str) -> int:
    if not name:
        return 2
    first = name[0]
    if "0" <= first <= "9":
        return 0
    if "A" <= first <= "Z" or "a" <= first <= "z":
        return 1
    return 2


def _find_first_with_ext(dir_path: pathlib.Path, ext: str) -> Optional[pathlib.Path]:
    for entry in dir_path.iterdir():
        if entry.is_file() and entry.name.lower().endswith(ext):
            return entry
    return None


def _get_proxies() -> Dict[str, str]:
    return {
        "http": "http://127.0.0.1:10808",
        "https": "http://127.0.0.1:10808",
    }


def _uploaded_file_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / "uploaded.json"


def _load_uploaded_set(path: pathlib.Path) -> set[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        uploaded = payload.get("uploaded")
        if isinstance(uploaded, list):
            return {str(item) for item in uploaded}
    except Exception:
        pass
    return set()


def _save_uploaded_set(path: pathlib.Path, uploaded: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump({"uploaded": sorted(set(uploaded))}, handle, ensure_ascii=False, indent=2)


def _build_upload_url(base_url: str) -> str:
    if not base_url:
        base = _get_basedir()
        return f"http://127.0.0.1{base}api/upload"
    normalized = base_url.strip()
    if not normalized.lower().startswith(("http://", "https://")):
        normalized = "http://" + normalized
    if not normalized.endswith("/"):
        normalized += "/"
    return urljoin(normalized, "api/upload")


def _build_song_api_url(base_url: str) -> str:
    if not base_url:
        base = _get_basedir()
        return f"http://127.0.0.1{base}api/songs"
    normalized = base_url.strip()
    if not normalized.lower().startswith(("http://", "https://")):
        normalized = "http://" + normalized
    if not normalized.endswith("/"):
        normalized += "/"
    return urljoin(normalized, "api/songs")


def _upload_song(
    url: str,
    song_type: str,
    tja_path: pathlib.Path,
    music_path: pathlib.Path,
    use_proxy: bool,
) -> tuple[bool, str]:
    proxies = _get_proxies() if use_proxy else None
    max_attempts = 3
    wait_seconds = 10

    for attempt in range(1, max_attempts + 1):
        try:
            with tja_path.open("rb") as tja_handle, music_path.open("rb") as music_handle:
                files = {
                    "file_tja": ("main.tja", tja_handle.read(), "text/plain"),
                    "file_music": ("music.ogg", music_handle.read(), "audio/ogg"),
                }
                data = {"song_type": song_type}
                response = requests.post(url, files=files, data=data, timeout=60, proxies=proxies)

            if response.status_code != 200:
                return False, f"http_status_{response.status_code}"

            payload = response.json()
            if payload.get("success") is True:
                return True, "ok"
            return False, str(payload.get("error") or "unknown_error")
        except (requests.exceptions.ProxyError,
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            if attempt < max_attempts:
                time.sleep(wait_seconds)
                continue
            return False, f"network_error:{exc}"
        except Exception as exc:
            return False, f"error:{exc}"

    return False, "unknown_error"


def fetch_server_songs(base_url: str, use_proxy: bool = True) -> Optional[set[str]]:
    proxies = _get_proxies() if use_proxy else None
    api_url = _build_song_api_url(base_url)
    try:
        response = requests.get(api_url, proxies=proxies, timeout=30)
        response.raise_for_status()
        data = response.json()
        songs: set[str] = set()
        if not isinstance(data, list):
            return songs
        for song in data:
            if not isinstance(song, dict):
                continue
            category = song.get("category") or song.get("song_type")
            title = song.get("title")
            if category and title:
                songs.add(f"{category}/{title}")
        return songs
    except Exception:
        return None


def _valid_type(name: str) -> bool:
    return bool(re.match(r"^\d{2}\s", name)) or (name in KNOWN_TYPES)


def run_upload(
    ese_path: str | os.PathLike[str] | None = None,
    site_url: str = "https://taiko.asia",
    use_proxy: bool = False,
    mode: str = "upload",
    progress: ProgressCallback = None,
) -> Dict[str, object]:
    summary = UploadSummary(mode=mode)
    upload_url = _build_upload_url(site_url)
    uploaded_path = _uploaded_file_path()
    uploaded_set = _load_uploaded_set(uploaded_path)

    ese_dir = pathlib.Path(ese_path) if ese_path else pathlib.Path(__file__).resolve().parent / "ESE"
    if not ese_dir.exists() or not ese_dir.is_dir():
        raise FileNotFoundError(f"ESE 目录不存在: {ese_dir}")

    server_songs: Optional[set[str]] = None
    if mode == "scan":
        _emit("正在获取站点歌曲列表...", progress)
        server_songs = fetch_server_songs(site_url, use_proxy=use_proxy)
        if server_songs is None:
            raise RuntimeError("无法获取站点歌曲列表。")
        _emit(f"站点当前歌曲数量: {len(server_songs)}", progress)

    type_dirs = [
        path for path in ese_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".") and _valid_type(path.name)
    ]
    if not type_dirs:
        raise RuntimeError("ESE 目录下没有识别到合法的歌曲分类目录。")

    for type_dir in sorted(type_dirs, key=lambda path: (_classify_name(path.name), path.name)):
        song_type = type_dir.name
        song_dirs = [path for path in type_dir.iterdir() if path.is_dir()]
        song_dirs.sort(key=lambda path: (_classify_name(path.name), path.name))

        for song_dir in song_dirs:
            summary.scanned += 1
            key = f"{song_type}/{song_dir.name}"

            if mode == "scan":
                if server_songs is not None and key not in server_songs:
                    summary.missing_on_server += 1
                    _emit(f"[缺失] {key}", progress)
                continue

            if key in uploaded_set:
                summary.skipped_uploaded += 1
                _emit(f"[跳过] 已上传: {key}", progress)
                continue

            tja_path = _find_first_with_ext(song_dir, ".tja")
            if tja_path is None:
                summary.skipped_missing_tja += 1
                _emit(f"[跳过] 缺少 TJA: {key}", progress)
                continue

            music_path = _find_first_with_ext(song_dir, ".ogg")
            if music_path is None:
                summary.skipped_missing_ogg += 1
                _emit(f"[跳过] 缺少 OGG: {key}", progress)
                continue

            ok, message = _upload_song(upload_url, song_type, tja_path, music_path, use_proxy)
            if ok:
                uploaded_set.add(key)
                summary.uploaded += 1
                _emit(f"[成功] 已上传: {key}", progress)
            else:
                summary.failed += 1
                _emit(f"[失败] {key} -> {message}", progress)

    if mode != "scan":
        _save_uploaded_set(uploaded_path, uploaded_set)

    _emit(f"任务完成: {json.dumps(summary.to_dict(), ensure_ascii=False)}", progress)
    return summary.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description="上传 ESE 歌曲到 Taiko 站点。")
    parser.add_argument("ese_path", nargs="?", help="ESE 目录路径")
    parser.add_argument("site_url", nargs="?", help="站点 URL，例如 https://taiko.asia")
    parser.add_argument("proxy", nargs="?", help="是否使用代理，y/n")
    parser.add_argument("mode", nargs="?", help="模式：upload 或 scan，也兼容 1/2")
    args = parser.parse_args()

    if args.ese_path:
        ese_input = args.ese_path
    else:
        try:
            ese_input = input("请输入 ESE 目录路径（默认当前目录下 ESE）: ").strip()
        except EOFError:
            ese_input = ""

    if args.site_url:
        site_url = args.site_url
    else:
        try:
            site_url = input("请输入上传站点 URL（默认 https://taiko.asia）: ").strip()
        except EOFError:
            site_url = ""

    if args.proxy:
        proxy_input = args.proxy.strip().lower()
    else:
        try:
            proxy_input = input("是否使用代理？(y/N): ").strip().lower()
        except EOFError:
            proxy_input = ""

    if args.mode:
        mode_input = args.mode.strip().lower()
    else:
        try:
            mode_input = input("请选择模式（1=上传，2=扫描缺失，默认1）: ").strip().lower()
        except EOFError:
            mode_input = ""

    mode_map = {"1": "upload", "2": "scan", "upload": "upload", "scan": "scan"}
    mode = mode_map.get(mode_input or "1", "upload")
    use_proxy = proxy_input == "y"

    summary = run_upload(
        ese_path=ese_input or None,
        site_url=site_url or "https://taiko.asia",
        use_proxy=use_proxy,
        mode=mode,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
