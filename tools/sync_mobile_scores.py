"""Export the desktop JSON library to a standalone Android assets folder."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def title_for(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(value, list):
            value = value[0] if value else {}
        if isinstance(value, dict):
            return str(value.get("songName") or value.get("title") or value.get("name") or path.stem)
    except (OSError, ValueError, TypeError):
        pass
    return path.stem


def main() -> None:
    parser = argparse.ArgumentParser(description="导出桌面曲库到 Android assets")
    parser.add_argument("--source", type=Path, default=ROOT / "Sheet Music")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--version", default=date.today().isoformat())
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    source = args.source.resolve()
    destination = args.destination.resolve()
    files = sorted(
        (path for path in source.glob("*.json")
         if not any(marker in path.name.lower() for marker in (".bak", ".wrong-base-", ".space-rebuild."))),
        key=lambda path: path.name.casefold(),
    )
    if not files:
        raise SystemExit(f"源曲库为空：{source}")
    destination.mkdir(parents=True, exist_ok=True)
    names = {path.name for path in files}
    if args.clean:
        for old in destination.glob("*.json"):
            if old.name not in names:
                old.unlink()
    for path in files:
        shutil.copyfile(path, destination / path.name)
    manifest = {
        "version": str(args.version),
        "count": len(files),
        "files": [{"filename": path.name, "title": title_for(path), "sha256": digest(path)} for path in files],
    }
    (destination.parent / "scores-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"version": manifest["version"], "count": len(files)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
