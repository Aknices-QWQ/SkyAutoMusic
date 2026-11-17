"""Search and cache Apple catalog artwork for local songs."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


SEARCH_URL = "https://itunes.apple.com/search"
USER_AGENT = "SkyAutoMusic/1.0"
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
NO_MATCH_TTL_SECONDS = 24 * 60 * 60
ERROR_TTL_SECONDS = 10 * 60


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in value if character.isalnum())


def _cache_key(title: str, artist: str) -> str:
    key = f"{normalize(title)}\n{normalize(artist)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _open_bytes(url: str, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json,image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    })
    openers = (
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
        urllib.request.build_opener(),
    )
    errors = []
    for opener in openers:
        try:
            with opener.open(request, timeout=12) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise ValueError("封面文件过大")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("响应内容过大")
                return data
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 - network providers vary by platform
            errors.append(exc)
    detail = str(errors[-1]) if errors else "网络请求失败"
    raise RuntimeError(f"无法连接 Apple 搜索服务：{detail}")


def _read_json(url: str) -> dict:
    try:
        return json.loads(_open_bytes(url, 2 * 1024 * 1024).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Apple 搜索服务返回了无效数据") from exc


def _title_score(query: str, candidate: str) -> float:
    left, right = normalize(query), normalize(candidate)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def _match_score(title: str, artist: str, item: dict) -> float:
    title_score = _title_score(title, item.get("trackName", ""))
    if not artist:
        return title_score
    artist_score = _title_score(artist, item.get("artistName", ""))
    return title_score * 0.84 + artist_score * 0.16


def _read_cache(metadata_path: Path, image_path: Path, now: float):
    try:
        cached = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    age = now - float(cached.get("checked_at", 0) or 0)
    if cached.get("image") and image_path.is_file():
        cached["image_path"] = str(image_path)
        return cached
    ttl = ERROR_TTL_SECONDS if cached.get("error") else NO_MATCH_TTL_SECONDS
    if age < ttl:
        return cached
    return None


def _write_cache(metadata_path: Path, image_path: Path, data: dict) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(data)
    if image_path.is_file():
        record["image"] = image_path.name
    record["checked_at"] = time.time()
    temporary = metadata_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(metadata_path)


def _artwork_urls(url: str):
    match = re.search(r"/\d+x\d+bb(?=\.)", url)
    if match:
        yield url[:match.start()] + "/600x600bb" + url[match.end():]
    if not match or url != url[:match.start()] + "/600x600bb" + url[match.end():]:
        yield url


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    raise RuntimeError("Apple 搜索服务返回的封面格式无法识别")


def find_album_art(title: str, artist: str = "", cache_dir: str | Path = ".", *, force: bool = False):
    """Return the best title/artist artwork match and cache it on disk.

    A ``None`` result means the search completed but no plausible cover was
    found. Network failures raise ``RuntimeError`` and are briefly cached.
    """
    title = str(title or "").strip()
    artist = str(artist or "").strip()
    if not normalize(title):
        return None

    root = Path(cache_dir)
    key = _cache_key(title, artist)
    metadata_path = root / f"{key}.json"
    now = time.time()
    if not force:
        cached = _read_cache(metadata_path, root / f"{key}.img", now)
        if cached is not None:
            return cached if cached.get("image_path") else None

    term = " ".join(part for part in (artist, title) if part)
    query = urllib.parse.urlencode({"term": term, "entity": "song", "limit": 25})
    try:
        payload = _read_json(f"{SEARCH_URL}?{query}")
        candidates = [
            item for item in payload.get("results", [])
            if item.get("trackName") and item.get("artworkUrl100")
        ]
        candidates.sort(key=lambda item: _match_score(title, artist, item), reverse=True)
        best = candidates[0] if candidates else None
        score = _match_score(title, artist, best) if best else 0.0
        if not best or _title_score(title, best.get("trackName", "")) < 0.68:
            _write_cache(metadata_path, root / f"{key}.img", {})
            return None

        artwork_url = best["artworkUrl100"]
        image_data = None
        last_error = None
        for candidate_url in _artwork_urls(artwork_url):
            try:
                image_data = _open_bytes(candidate_url)
                break
            except Exception as exc:  # noqa: BLE001 - retry Apple's 100px URL if needed
                last_error = exc
        if image_data is None:
            raise RuntimeError(f"封面下载失败：{last_error or '未知网络错误'}")

        image_path = root / f"{key}.img"
        root.mkdir(parents=True, exist_ok=True)
        temporary = image_path.with_suffix(".download")
        temporary.write_bytes(image_data)
        _image_extension(image_data)
        temporary.replace(image_path)
        result = {
            "title": best.get("trackName", title),
            "artist": best.get("artistName", ""),
            "album": best.get("collectionName", ""),
            "score": score,
            "image_path": str(image_path),
        }
        _write_cache(metadata_path, image_path, result)
        return result
    except Exception as exc:
        _write_cache(metadata_path, root / f"{key}.img", {"error": str(exc)})
        raise
