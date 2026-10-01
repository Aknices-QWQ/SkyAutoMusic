import argparse
import hashlib
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


REPOSITORY = "Ai-Vonie/Sky1984-Sheets-Collection"
BRANCH = "master"
RAW_BASE = f"https://raw.githubusercontent.com/{REPOSITORY}/{BRANCH}/"
ARCHIVE_URL = f"https://codeload.github.com/{REPOSITORY}/zip/refs/heads/{BRANCH}"
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Output title, source path.  When the collection has several arrangements,
# prefer the complete, credited arrangement with the unqualified song title.
SHEETS = [
    (
        "Komorebi",
        "Songs/K/Komorebi (木漏れ日) OST Yuuki Yuuna is a Hero (by 凌月Ustinian).txt",
    ),
    ("Sacred Play Secret Place", "Songs/S/scared play secret place.txt"),
    ("爱情讯息", "Songs/Chinese/A/爱情讯息.txt"),
    ("知我", "Songs/Chinese/Z/知我.txt"),
    ("雨爱", "Songs/Chinese/Y/雨爱.txt"),
    ("天下", "Songs/Chinese/T/天下.txt"),
    ("唯一", "Songs/Chinese/W/唯一.txt"),
    ("第57次取消发送", "Songs/Chinese/D/第57次取消发送.txt"),
    ("春娇与志明", "Songs/Chinese/C/春娇与志明.txt"),
    ("西厢寻他", "Songs/Chinese/X/西厢寻他.txt"),
]


def raw_url(source_path: str) -> str:
    encoded_path = "/".join(urllib.parse.quote(part) for part in source_path.split("/"))
    return RAW_BASE + encoded_path


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "SkyAutoMusic-Codex"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def download_archive(destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        ARCHIVE_URL, headers={"User-Agent": "SkyAutoMusic-Codex"}
    )
    downloaded = 0
    next_report = 16 * 1024 * 1024
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_report:
                total_text = f"/{total / 1024 / 1024:.1f} MB" if total else ""
                print(f"archive: {downloaded / 1024 / 1024:.1f} MB{total_text}", flush=True)
                next_report += 16 * 1024 * 1024
    partial.replace(destination)
    print(f"archive saved: {destination} ({downloaded / 1024 / 1024:.1f} MB)", flush=True)


def decode_json(payload: bytes):
    last_error = None
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return json.loads(payload.decode(encoding))
        except (UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
    raise ValueError(f"cannot decode JSON: {last_error}")


def sanitize_filename(value: str, limit: int = 170) -> str:
    value = unicodedata.normalize("NFC", value)
    value = INVALID_FILENAME_CHARS.sub("_", value).strip(" .")
    value = re.sub(r"\s+", " ", value)
    if not value:
        value = "Untitled"
    if len(value) > limit:
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
        value = value[: limit - 12].rstrip(" .") + "__" + digest
    return value


def normalize_song_notes(notes):
    if not isinstance(notes, list) or not notes:
        return None
    normalized = []
    seen = set()
    for note in notes:
        if not isinstance(note, dict) or "time" not in note or "key" not in note:
            return None
        try:
            time_ms = int(note["time"])
            match = re.fullmatch(r"(\d+)Key(\d+)", str(note["key"]))
            if match is None:
                return None
            layer = int(match.group(1))
            key_index = int(match.group(2))
        except (TypeError, ValueError):
            return None
        if time_ms < 0 or layer < 1 or not 0 <= key_index <= 14:
            return None
        identity = (time_ms, key_index)
        if identity in seen:
            continue
        seen.add(identity)
        item = dict(note)
        item["time"] = time_ms
        item["key"] = f"1Key{key_index}"
        if layer > 1 and "layer" not in item:
            item["layer"] = layer
        normalized.append(item)
    normalized.sort(key=lambda item: item["time"])
    return normalized


def same_notes(path: Path, notes: list[dict]) -> bool:
    try:
        existing = decode_json(path.read_bytes())
        if isinstance(existing, list) and existing and isinstance(existing[0], dict):
            return existing[0].get("songNotes") == notes
    except (OSError, ValueError):
        pass
    return False


def compatible_sheets(payload: bytes, source_path: str) -> tuple[list[dict], list[str]]:
    source = decode_json(payload)
    if isinstance(source, dict):
        source = [source]
    if not isinstance(source, list):
        raise ValueError("top-level JSON value is not a sheet list")

    sheets = []
    reasons = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            reasons.append(f"part {index + 1}: not an object")
            continue
        notes = normalize_song_notes(item.get("songNotes"))
        if notes is None:
            reasons.append(f"part {index + 1}: missing or invalid songNotes")
            continue
        meta = dict(item)
        meta["songNotes"] = notes
        meta["source"] = raw_url(source_path)
        meta["sourceRepository"] = REPOSITORY
        sheets.append(meta)
    return sheets, reasons


def output_name(source_path: str, sheet: dict, index: int, count: int) -> str:
    source_stem = sanitize_filename(Path(source_path).stem)
    if count > 1:
        part_name = sanitize_filename(str(sheet.get("name") or f"Part {index + 1}"), 100)
        if part_name.casefold() == source_stem.casefold():
            part_name = f"Part {index + 1}"
        source_stem = sanitize_filename(f"{source_stem} - {part_name}")
    return f"Sky1984 - {source_stem}.json"


def sync_all(root: Path, refresh: bool) -> int:
    source_dir = root / "sources"
    output_dir = root / "Sheet Music"
    source_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = source_dir / f"Sky1984-Sheets-Collection-{BRANCH}.zip"
    manifest_path = source_dir / "sky1984-sync-manifest.json"

    if refresh or not archive.exists() or archive.stat().st_size == 0:
        download_archive(archive)
    else:
        print(f"using existing archive: {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MB)")

    written = 0
    reused = 0
    skipped_parts = 0
    failed_files = 0
    processed_files = 0
    manifest = []

    with zipfile.ZipFile(archive) as bundle:
        members = [
            info
            for info in bundle.infolist()
            if not info.is_dir()
            and info.filename.lower().endswith(".txt")
            and any(
                marker in info.filename
                for marker in ("/Songs/", "/Multi-Sheet Songs/")
            )
        ]
        print(f"processing {len(members):,} source files", flush=True)
        for info in members:
            processed_files += 1
            source_path = info.filename.split("/", 1)[1]
            try:
                payload = bundle.read(info)
                sheets, reasons = compatible_sheets(payload, source_path)
                skipped_parts += len(reasons)
                if not sheets:
                    failed_files += 1
                    manifest.append(
                        {"source": source_path, "status": "skipped", "reasons": reasons}
                    )
                    continue

                for index, sheet in enumerate(sheets):
                    filename = output_name(source_path, sheet, index, len(sheets))
                    destination = output_dir / filename
                    if destination.exists() and same_notes(destination, sheet["songNotes"]):
                        reused += 1
                        status = "reused"
                    else:
                        if destination.exists():
                            identity = f"{source_path}#{index}".encode("utf-8")
                            suffix = hashlib.sha1(identity).hexdigest()[:10]
                            destination = output_dir / (
                                sanitize_filename(Path(filename).stem, 155)
                                + f"__{suffix}.json"
                            )
                        if destination.exists() and same_notes(destination, sheet["songNotes"]):
                            reused += 1
                            status = "reused"
                        else:
                            destination.write_text(
                                json.dumps([sheet], ensure_ascii=False, separators=(",", ":")),
                                encoding="utf-8",
                            )
                            written += 1
                            status = "written"
                    manifest.append(
                        {
                            "source": source_path,
                            "part": index + 1,
                            "title": sheet.get("name", ""),
                            "output": destination.name,
                            "notes": len(sheet["songNotes"]),
                            "status": status,
                        }
                    )
            except Exception as exc:
                failed_files += 1
                manifest.append(
                    {"source": source_path, "status": "error", "error": str(exc)}
                )

            if processed_files % 500 == 0:
                print(
                    f"progress: {processed_files:,}/{len(members):,} files, "
                    f"written={written:,}, reused={reused:,}, failed={failed_files:,}",
                    flush=True,
                )

    summary = {
        "repository": REPOSITORY,
        "branch": BRANCH,
        "archive": archive.name,
        "sourceFiles": processed_files,
        "writtenSheets": written,
        "reusedSheets": reused,
        "skippedParts": skipped_parts,
        "failedFiles": failed_files,
        "entries": manifest,
    }
    manifest_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"complete: files={processed_files:,}, written={written:,}, reused={reused:,}, "
        f"skipped_parts={skipped_parts:,}, failed_files={failed_files:,}",
        flush=True,
    )
    print(f"manifest: {manifest_path}")
    return 0 if failed_files == 0 else 2


def convert(title: str, source_path: str, payload: bytes) -> list[dict]:
    source = json.loads(payload.decode("utf-8-sig"))
    if not isinstance(source, list) or not source or not isinstance(source[0], dict):
        raise ValueError("expected a non-empty Sky Music Nightly sheet list")

    meta = source[0]
    columns = meta.get("columns")
    bpm = meta.get("bpm")
    if not isinstance(columns, list) or not isinstance(bpm, (int, float)) or bpm <= 0:
        raise ValueError("only composed sheets with columns and a positive bpm are supported")

    # Sky Music Nightly advances one grid column per 60000 / bpm milliseconds.
    # Its legacy exporter used integer intervals and a short lead-in.
    interval_ms = int(60000 / bpm)
    lead_in_ms = 400
    notes = []
    seen = set()

    for column_index, column in enumerate(columns):
        if not isinstance(column, list) or len(column) < 2 or not isinstance(column[1], list):
            raise ValueError(f"invalid column at index {column_index}")
        time_ms = lead_in_ms + column_index * interval_ms
        for note in column[1]:
            if not isinstance(note, list) or not note or not isinstance(note[0], int):
                raise ValueError(f"invalid note in column {column_index}")
            key_index = note[0]
            if not 0 <= key_index <= 14:
                raise ValueError(f"key index out of range in column {column_index}: {key_index}")
            identity = (time_ms, key_index)
            if identity in seen:
                continue
            seen.add(identity)
            item = {"time": time_ms, "key": f"1Key{key_index}"}
            if len(note) > 1:
                try:
                    instrument = int(note[1])
                except (TypeError, ValueError):
                    instrument = 1
                if instrument > 1:
                    item["l"] = instrument
            notes.append(item)

    output_meta = {
        "name": title,
        "author": meta.get("author", ""),
        "transcribedBy": meta.get("transcribedBy", ""),
        "isComposed": True,
        "bpm": bpm,
        "bitsPerPage": 16,
        "pitchLevel": 0,
        "isEncrypted": False,
        "pitch": meta.get("pitch", ""),
        "source": raw_url(source_path),
        "sourceRepository": REPOSITORY,
        "songNotes": notes,
    }
    return [output_meta]


def sync_selected(root: Path) -> int:
    source_dir = root / "sources" / "sky1984"
    output_dir = root / "Sheet Music"
    source_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    for title, source_path in SHEETS:
        try:
            url = raw_url(source_path)
            payload = download(url)
            source_file = source_dir / f"{title}.txt"
            source_file.write_bytes(payload)

            converted = convert(title, source_path, payload)
            output_file = output_dir / f"{title}.json"
            output_file.write_text(
                json.dumps(converted, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            print(
                f"{title}: {len(converted[0]['songNotes'])} notes -> "
                f"{output_file.relative_to(root)}"
            )
        except Exception as exc:
            failures.append((title, str(exc)))
            print(f"ERROR {title}: {exc}", file=sys.stderr)

    if failures:
        print(f"Failed to convert {len(failures)} sheet(s).", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all", action="store_true", help="download and convert the complete collection archive"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="download a fresh archive even if one exists"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    if args.all:
        return sync_all(root, args.refresh)
    return sync_selected(root)


if __name__ == "__main__":
    raise SystemExit(main())
