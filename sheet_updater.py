import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import zipfile
from collections import deque
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse


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
    updates = sheets.get("updates", [])
    if not isinstance(updates, list):
        raise RuntimeError("增量更新清单格式不正确")
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


def _safe_filename(filename):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(filename)).strip(" .")
    if not name.lower().endswith(".json"):
        name += ".json"
    stem = Path(name).stem[:180].rstrip(" .") or "导入乐谱"
    return stem + ".json"


def _unique_filename(filename, used_names):
    filename = _safe_filename(filename)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = filename
    number = 2
    while candidate.casefold() in used_names:
        candidate = f"{stem} ({number}){suffix}"
        number += 1
    used_names.add(candidate.casefold())
    return candidate, candidate != filename


def _decode_sheet(payload):
    last_error = None
    for encoding in ("utf-8", "utf-8-sig", "gbk", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            data = json.loads(payload.decode(encoding))
            break
        except Exception as exc:
            last_error = exc
    else:
        raise RuntimeError(f"JSON 解析失败：{last_error}")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        meta = data[0]
    elif isinstance(data, dict):
        meta = data
    else:
        raise RuntimeError("乐谱格式不正确")
    if not isinstance(meta.get("songNotes"), list):
        raise RuntimeError("乐谱缺少 songNotes")
    return data


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
        filename, renamed = _unique_filename(path.name, seen_names)
        members.append((member, filename, renamed))
    if not members:
        raise RuntimeError("曲库压缩包中没有 JSON 乐谱")
    return members


def _safe_removal_names(names):
    safe_names = []
    for name in names or []:
        path = PurePosixPath(str(name).replace("\\", "/"))
        if path.is_absolute() or len(path.parts) != 1 or path.suffix.lower() != ".json":
            raise RuntimeError(f"增量包包含不安全的删除路径：{name}")
        safe_names.append(path.name)
    return safe_names


def _find_incremental_path(current_version, target_version, updates):
    queue = deque([(current_version, [])])
    visited = {current_version}
    while queue:
        version, path = queue.popleft()
        for update in updates:
            if not isinstance(update, dict) or update.get("from") != version:
                continue
            required = ("from", "to", "asset_url", "sha256")
            if any(not update.get(key) for key in required):
                continue
            next_version = update["to"]
            next_path = path + [update]
            if next_version == target_version:
                return next_path
            if next_version not in visited:
                visited.add(next_version)
                queue.append((next_version, next_path))
    return None


def _install_archive(
    archive_path,
    sheet_dir,
    removals=None,
    preserve_existing=False,
    validate_members=False,
):
    installed = 0
    renamed = 0
    skipped = 0
    existing_names = {path.name.casefold() for path in sheet_dir.glob("*.json")}
    with zipfile.ZipFile(archive_path) as archive:
        members = _safe_sheet_members(archive)
        for member, filename, archive_renamed in members:
            with archive.open(member) as source:
                payload = source.read()
            if validate_members:
                try:
                    _decode_sheet(payload)
                except Exception:
                    skipped += 1
                    continue
            if preserve_existing:
                exact_target = sheet_dir / filename
                if exact_target.exists() and exact_target.read_bytes() == payload:
                    skipped += 1
                    continue
                target_name, target_renamed = _unique_filename(filename, existing_names)
            else:
                target_name = filename
                target_renamed = archive_renamed
                existing_names.add(target_name.casefold())
            renamed += int(archive_renamed or target_renamed)
            target = sheet_dir / target_name
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(payload)
            os.replace(temporary, target)
            installed += 1

    removed = 0
    for filename in _safe_removal_names(removals):
        target = sheet_dir / filename
        if target.exists():
            target.unlink()
            removed += 1
    return installed, removed, renamed, skipped


def import_sheet_files(app_dir, paths):
    sheet_dir = Path(app_dir) / "Sheet Music"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    used_names = {path.name.casefold() for path in sheet_dir.glob("*.json")}
    installed = 0
    renamed = 0
    skipped = 0
    errors = []
    for value in paths:
        source_path = Path(value)
        if not source_path.is_file() or source_path.suffix.lower() not in (".json", ".zip"):
            skipped += 1
            continue
        try:
            if source_path.suffix.lower() == ".zip":
                archive_installed, _, archive_renamed, archive_skipped = _install_archive(
                source_path,
                sheet_dir,
                preserve_existing=True,
                validate_members=True,
                )
                installed += archive_installed
                renamed += archive_renamed
                skipped += archive_skipped
                used_names = {path.name.casefold() for path in sheet_dir.glob("*.json")}
                continue
            payload = source_path.read_bytes()
            _decode_sheet(payload)
            safe_name = _safe_filename(source_path.name)
            exact_target = sheet_dir / safe_name
            if exact_target.exists() and exact_target.read_bytes() == payload:
                skipped += 1
                continue
            target_name, was_renamed = _unique_filename(safe_name, used_names)
            target = sheet_dir / target_name
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(payload)
            os.replace(temporary, target)
            installed += 1
            renamed += int(was_renamed)
        except Exception as exc:
            skipped += 1
            errors.append(f"{source_path.name}: {exc}")
    return {
        "updated": installed > 0,
        "version": read_local_state(app_dir).get("version", "本地曲库"),
        "count": len(list(sheet_dir.glob("*.json"))),
        "changed": installed,
        "renamed": renamed,
        "skipped": skipped,
        "errors": errors,
        "mode": "manual",
    }


def install_external_source(app_dir, url, progress_callback=None):
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("请输入有效的 HTTP 或 HTTPS 下载地址")
    source_name = _safe_filename(unquote(Path(parsed.path).name or "下载乐谱.json"))
    temp_dir = Path(tempfile.mkdtemp(prefix="skyautomusic-source-"))
    try:
        download_path = temp_dir / source_name
        _download(url, download_path, progress_callback)
        sheet_dir = Path(app_dir) / "Sheet Music"
        sheet_dir.mkdir(parents=True, exist_ok=True)
        if zipfile.is_zipfile(download_path):
            installed, removed, renamed, skipped = _install_archive(
                download_path,
                sheet_dir,
                preserve_existing=True,
                validate_members=True,
            )
            return {
                "updated": installed > 0,
                "version": read_local_state(app_dir).get("version", "本地曲库"),
                "count": len(list(sheet_dir.glob("*.json"))),
                "changed": installed,
                "removed": removed,
                "renamed": renamed,
                "skipped": skipped,
                "mode": "external",
                "source": url,
            }
        result = import_sheet_files(app_dir, [download_path])
        result["mode"] = "external"
        result["source"] = url
        return result
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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

    local_version = local_state.get("version")
    if local_version:
        packages = _find_incremental_path(
            local_version,
            sheets["version"],
            sheets.get("updates", []),
        )
        if not packages:
            raise RuntimeError(
                f"没有从 {local_version} 到 {sheets['version']} 的增量包，请下载安装最新版"
            )
        update_mode = "incremental"
    else:
        packages = [sheets]
        update_mode = "bootstrap"

    temp_dir = Path(tempfile.mkdtemp(prefix="skyautomusic-update-"))
    try:
        sheet_dir = app_dir / "Sheet Music"
        sheet_dir.mkdir(parents=True, exist_ok=True)
        installed = 0
        removed = 0
        renamed = 0
        skipped = 0
        actual_hash = ""
        for index, package in enumerate(packages):
            archive_path = temp_dir / f"update-{index}.zip"
            download_url = apply_github_mirror(package["asset_url"], mirror_prefix)

            def package_progress(percent, downloaded, total):
                if not progress_callback:
                    return
                overall = int(((index + percent / 100) / len(packages)) * 100) if percent else 0
                progress_callback(overall, downloaded, total)

            _download(download_url, archive_path, package_progress)
            actual_hash = _sha256(archive_path)
            if actual_hash.lower() != str(package["sha256"]).lower():
                raise RuntimeError("曲库校验失败，请切换镜像后重试")
            changed, deleted, renamed_count, skipped_count = _install_archive(
                archive_path,
                sheet_dir,
                package.get("remove", []),
            )
            installed += changed
            removed += deleted
            renamed += renamed_count
            skipped += skipped_count

        state = {
            "version": sheets["version"],
            "count": int(sheets["count"]),
            "sha256": str(sheets["sha256"]),
            "source": packages[-1]["asset_url"],
        }
        (app_dir / LOCAL_STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "updated": True,
            "version": sheets["version"],
            "count": int(sheets["count"]),
            "changed": installed,
            "removed": removed,
            "renamed": renamed,
            "skipped": skipped,
            "mode": update_mode,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
