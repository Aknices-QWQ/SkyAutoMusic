import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan(directory):
    return {
        path.name: file_hash(path)
        for path in sorted(Path(directory).glob("*.json"), key=lambda item: item.name.casefold())
    }


def main():
    parser = argparse.ArgumentParser(description="Build a SkyAutoMusic incremental sheet package")
    parser.add_argument("old_dir", type=Path)
    parser.add_argument("new_dir", type=Path)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    old_files = scan(args.old_dir)
    new_files = scan(args.new_dir)
    changed = sorted(
        name for name, digest in new_files.items() if old_files.get(name) != digest
    )
    removed = sorted(name for name in old_files if name not in new_files)
    if removed and not changed and new_files:
        # Keep deletion-only patches installable by including one harmless current file.
        changed.append(next(iter(new_files)))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in changed:
            archive.write(args.new_dir / name, name)

    package_hash = file_hash(args.output)
    result = {
        "from": args.from_version,
        "to": args.to_version,
        "asset_url": f"REPLACE_WITH_RELEASE_URL/{args.output.name}",
        "sha256": package_hash,
        "changed": len(changed),
        "remove": removed,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
