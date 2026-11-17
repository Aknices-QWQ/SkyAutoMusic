"""Small, defensive persistence helpers for user-created song playlists."""

from __future__ import annotations

import json
from pathlib import Path


def normalize_playlists(value) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    playlists = {}
    seen_names = set()
    for raw_name, raw_songs in value.items():
        name = str(raw_name or "").strip()
        key = name.casefold()
        if not name or len(name) > 48 or key in seen_names or not isinstance(raw_songs, list):
            continue
        seen_names.add(key)
        songs, seen_songs = [], set()
        for raw_song in raw_songs:
            song = str(raw_song or "").strip()
            if song and Path(song).name == song and song.casefold() not in seen_songs:
                songs.append(song)
                seen_songs.add(song.casefold())
        playlists[name] = songs
    return playlists


def load_playlists(path: str | Path) -> dict[str, list[str]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return normalize_playlists(data)


def save_playlists(path: str | Path, playlists: dict[str, list[str]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_playlists(playlists)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def create_playlist(playlists: dict[str, list[str]], name: str, song: str | None = None):
    name = str(name or "").strip()
    if not name:
        raise ValueError("歌单名称不能为空")
    if len(name) > 48:
        raise ValueError("歌单名称最多 48 个字符")
    if any(existing.casefold() == name.casefold() for existing in playlists):
        raise ValueError("已存在同名歌单")
    result = normalize_playlists(playlists)
    result[name] = [song] if song else []
    return result


def rename_playlist(playlists: dict[str, list[str]], old_name: str, new_name: str):
    old_name = str(old_name or "")
    new_name = str(new_name or "").strip()
    if old_name not in playlists:
        raise ValueError("找不到要重命名的歌单")
    if not new_name:
        raise ValueError("歌单名称不能为空")
    if len(new_name) > 48:
        raise ValueError("歌单名称最多 48 个字符")
    if new_name.casefold() != old_name.casefold() and any(
            existing.casefold() == new_name.casefold() for existing in playlists):
        raise ValueError("已存在同名歌单")
    return {new_name if name == old_name else name: list(songs)
            for name, songs in playlists.items()}


def set_song_membership(playlists: dict[str, list[str]], name: str, song: str, included: bool):
    if name not in playlists:
        raise ValueError("找不到指定歌单")
    result = normalize_playlists(playlists)
    songs = result[name]
    songs[:] = [item for item in songs if item.casefold() != song.casefold()]
    if included:
        songs.append(song)
    return result
