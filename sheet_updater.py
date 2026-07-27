import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


MANIFEST_URL = "https://sky.xxlab.dev/downloads.json"
LOCAL_STATE_FILE = "sheets-version.json"
USER_AGENT = "SkyAutoMusic-Updater/1.0"

GITHUB_MIRRORS = [
    ("国内镜像（ghfast.top）", "https://ghfast.top/"),
    ("备用镜像（gh-proxy.com）", "https://gh-proxy.com/"),
    ("GitHub 直连", ""),
]


def read_local_state(app_dir):
    path = Path(app_dir) / LOCAL_STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _request_json(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_release_manifest():
    manifest = _request_json(MANIFEST_URL)
    sheets = manifest.get("sheets")
    if not isinstance(sheets, dict):
        raise RuntimeError("更新清单缺少 sheets 信息")
    required = ("version", "asset_url", "sha256", "count")
    missing = [key for key in required if not sheets.get(key)]
    if missing:
        raise RuntimeError("更新清单字段不完整：" + ", ".join(missing))
    return manifest


def apply_github_mirror(url, mirror_prefix):
    if not mirror_prefix or not url.startswith("https://github.com/"):
        return url
    return mirror_prefix.rstrip("/") + "/" + url


def _download(url, destination, progress_callback=None):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if progress_callback:
                percent = int(downloaded * 100 / total) if total else 0
                progress_callback(percent, downloaded, total)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_sheet_members(archive):
    members = []
    seen_names = set()
    for member in archive.infolist():
        if member.is_dir():
            continue
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError("曲库压缩包包含不安全路径")
        if path.suffix.lower() != ".json":
            continue
        filename = path.name
        if filename in seen_names:
            raise RuntimeError(f"曲库压缩包存在重名文件：{filename}")
        seen_names.add(filename)
        members.append((member, filename))
    if not members:
        raise RuntimeError("曲库压缩包中没有 JSON 乐谱")
    return members


def install_sheet_update(app_dir, mirror_prefix="", progress_callback=None):
    app_dir = Path(app_dir)
    manifest = fetch_release_manifest()
    sheets = manifest["sheets"]
    local_state = read_local_state(app_dir)
    if local_state.get("version") == sheets["version"]:
        return {
            "updated": False,
            "version": sheets["version"],
            "count": int(sheets["count"]),
        }

    download_url = apply_github_mirror(sheets["asset_url"], mirror_prefix)
    temp_dir = Path(tempfile.mkdtemp(prefix="skyautomusic-update-"))
    archive_path = temp_dir / "sheets.zip"
    try:
        _download(download_url, archive_path, progress_callback)
        actual_hash = _sha256(archive_path)
        if actual_hash.lower() != str(sheets["sha256"]).lower():
            raise RuntimeError("曲库校验失败，请切换镜像后重试")

        sheet_dir = app_dir / "Sheet Music"
        sheet_dir.mkdir(parents=True, exist_ok=True)
        installed = 0
        with zipfile.ZipFile(archive_path) as archive:
            members = _safe_sheet_members(archive)
            for member, filename in members:
                target = sheet_dir / filename
                temporary = target.with_suffix(target.suffix + ".part")
                with archive.open(member) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.replace(temporary, target)
                installed += 1

        state = {
            "version": sheets["version"],
            "count": installed,
            "sha256": actual_hash,
            "source": sheets["asset_url"],
        }
        (app_dir / LOCAL_STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"updated": True, "version": sheets["version"], "count": installed}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
