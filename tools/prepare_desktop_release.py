"""Prepare Windows release files without copying personal configuration."""

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
LITE_SHEETS = ('最后一吻.json', 'Call of Silence.json')
DEFAULT_CONFIG = {
    'width': 1080, 'height': 720, 'speed': 1.0, 'speed_logic_version': 2,
    'random_delay_percent': 0, 'wrong_key_percent': 0, 'speed_presets': {},
    'overlay_music_mode': '全部', 'theme': 'carbon', 'update_mirror': '',
    'admin_mode': False, 'admin_tip_count': 0, 'sky_sound_id': 'specy:Harp',
    'sky_location': '遇境', 'sky_location_pitches': {}, 'score_input_mode': 'piano',
    'overlay_show_score': True, 'overlay_opacity': 100,
}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def metadata(release_tag=''):
    installer = (ROOT / 'installer.iss').read_text(encoding='utf-8-sig')
    match = re.search(r'^#define MyAppVersion "(\d+\.\d+\.\d+)"$', installer, re.M)
    if not match:
        raise ValueError('installer.iss must declare a three-part MyAppVersion')
    version = match[1]
    if not re.search(rf'^VersionInfoVersion={re.escape(version)}\.0$', installer, re.M):
        raise ValueError('Installer version fields do not match')
    updater = ROOT / 'app_updater.py'
    if updater.exists():
        app_version = re.search(r'^APP_VERSION = "(\d+\.\d+\.\d+)"$', updater.read_text(encoding='utf-8-sig'), re.M)
        if not app_version or app_version[1] != version:
            raise ValueError('APP_VERSION and installer version do not match')
    if release_tag and release_tag != f'v{version}':
        raise ValueError(f'Tag {release_tag!r} does not match source version v{version}')
    for name in LITE_SHEETS:
        if not (ROOT / 'Sheet Music' / name).is_file():
            raise ValueError(f'Missing Lite score: {name}')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    return {'version': version, 'source_commit': commit}


def included_sheet(path):
    return path.suffix.lower() == '.json' and not any(
        marker in path.name.lower() for marker in ('.bak', '.wrong-base-', '.space-rebuild.')
    )


def extract_library(archive_path, destination, expected_count):
    used = set()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename.replace('\\', '/'))
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe path in sheet archive')
            if member.is_dir() or not included_sheet(path):
                continue
            if re.search(r'[<>:"/\\|?*\x00-\x1f]', path.name) or path.name.endswith((' ', '.')):
                raise ValueError(f'Unsupported sheet filename: {path.name}')
            filename = path.name
            counter = 1
            while filename.casefold() in used:
                filename = f'{path.stem} ({counter}){path.suffix}'
                counter += 1
            used.add(filename.casefold())
            with archive.open(member) as source, (destination / filename).open('wb') as target:
                shutil.copyfileobj(source, target)
    if len(used) != expected_count:
        raise ValueError(f'Sheet archive count mismatch: expected {expected_count}, got {len(used)}')
    return len(used)


def copy_licenses(destination):
    notices = destination / 'THIRD_PARTY_NOTICES'
    notices.mkdir()
    for package in ('PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'numpy', 'PyAudioWPatch', 'playwright', 'keyboard', 'greenlet', 'pyee'):
        try:
            distribution = importlib.metadata.distribution(package)
        except importlib.metadata.PackageNotFoundError:
            continue
        for item in distribution.files or ():
            if '.dist-info' in str(item) and any(token in item.name.upper() for token in ('LICENSE', 'COPYING', 'NOTICE')):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    folder = notices / package
                    folder.mkdir(exist_ok=True)
                    shutil.copyfile(source, folder / item.name)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, notices / 'Python-LICENSE.txt')


def stage(output, release_tag):
    info = metadata(release_tag)
    dist = output / 'play_music_qt.dist'
    if not (dist / 'SkyAutoMusic.exe').is_file():
        raise ValueError('Compiled SkyAutoMusic.exe is missing')
    staging = output / 'staging'
    if staging.exists():
        raise ValueError('Staging directory already exists; use a fresh output directory')
    descriptor = json.loads((ROOT / 'site' / 'downloads.json').read_text(encoding='utf-8-sig'))['sheets']
    url = descriptor['asset_url']
    if not url.startswith('https://github.com/') or not re.fullmatch(r'[0-9a-f]{64}', descriptor['sha256']):
        raise ValueError('Invalid pinned sheet archive descriptor')
    archive_path = output / 'sheet-library.zip'
    request = urllib.request.Request(url, headers={'User-Agent': 'SkyAutoMusic-GitHub-Actions'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as source, archive_path.open('wb') as target:
        shutil.copyfileobj(source, target)
    if sha256(archive_path) != descriptor['sha256']:
        raise ValueError('Sheet archive SHA-256 does not match the pinned manifest')
    if archive_path.stat().st_size != descriptor['size']:
        raise ValueError('Sheet archive size does not match the pinned manifest')

    editions = {}
    for edition in ('Lite', 'Full'):
        destination = staging / edition
        shutil.copytree(dist, destination)
        for name in ('README.md', 'LICENSE', 'RELEASE_NOTES.md'):
            shutil.copyfile(ROOT / name, destination / name)
        if (ROOT / 'docs').is_dir():
            shutil.copytree(ROOT / 'docs', destination / 'docs')
        copy_licenses(destination)
        write_json(destination / 'config.json', DEFAULT_CONFIG)
        write_json(destination / 'favorites.json', [])
        sheets = destination / 'Sheet Music'
        sheets.mkdir()
        if edition == 'Full':
            extract_library(archive_path, sheets, int(descriptor['count']))
            for source in (ROOT / 'Sheet Music').glob('*.json'):
                if included_sheet(source):
                    shutil.copyfile(source, sheets / source.name)
            write_json(destination / 'sheets-version.json', {
                'version': descriptor['version'], 'count': descriptor['count'],
                'sha256': descriptor['sha256'], 'source': url,
            })
        else:
            for filename in LITE_SHEETS:
                shutil.copyfile(ROOT / 'Sheet Music' / filename, sheets / filename)
        forbidden = {'Backups', 'sources', 'search-index.json', 'recognition-index.json.gz', 'python.exe'}
        if forbidden & {path.name for path in destination.iterdir()}:
            raise ValueError('Unexpected private data or development files in release')
        if (ROOT / 'mobile' / 'web' / 'index.html').exists() and not (destination / 'mobile' / 'web' / 'index.html').exists():
            raise ValueError('Mobile web resource is missing from compilation')
        samples = ROOT / 'assets' / 'sky_sounds'
        if samples.exists():
            source_files = {p.relative_to(samples) for p in samples.rglob('*') if p.is_file()}
            bundled_files = {p.relative_to(destination / 'assets' / 'sky_sounds') for p in (destination / 'assets' / 'sky_sounds').rglob('*') if p.is_file()}
            if source_files != bundled_files:
                raise ValueError('Bundled Sky sound assets are incomplete')
        editions[edition] = {'sheet_count': len(list(sheets.glob('*.json')))}
    packages = {}
    for package in ('Nuitka', 'PySide6', 'numpy', 'PyAudioWPatch', 'playwright', 'keyboard'):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    info.update({'platform': 'Windows x64', 'python': sys.version.split()[0],
                 'compiler': 'Nuitka standalone + MSVC + Inno Setup', 'packages': packages,
                 'sheet_archive': descriptor, 'editions': editions})
    write_json(output / 'build-info.json', info)
    print(json.dumps(info, ensure_ascii=False, indent=2))


def finalize(output, release_tag):
    source = metadata(release_tag)
    info = json.loads((output / 'build-info.json').read_text(encoding='utf-8'))
    if any(info[key] != source[key] for key in ('version', 'source_commit')):
        raise ValueError('Source changed during compilation')
    installers = output / 'installers'
    checksums = []
    assets = []
    for edition in ('Lite', 'Full'):
        name = f"SkyAutoMusic-v{source['version']}-{edition}-Setup.exe"
        path = installers / name
        if not path.is_file() or path.stat().st_size < 1024 * 1024:
            raise ValueError(f'Missing or incomplete installer: {name}')
        digest = sha256(path)
        checksums.append(f'{digest}  {name}')
        assets.append({'name': name, 'size': path.stat().st_size, 'sha256': digest})
    (installers / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    info['assets'] = assets
    write_json(installers / 'build-info.json', info)
    notes = (ROOT / 'RELEASE_NOTES.md').read_text(encoding='utf-8-sig')
    heading = f"# SkyAutoMusic v{source['version']}"
    start = notes.find(heading + '\n')
    if start < 0:
        raise ValueError('Release notes for this version are missing')
    section = notes[start:]
    end = section.find('\n# ', len(heading))
    if end >= 0:
        section = section[:end]
    section += ('\n\n## 安装包\n\n'
                '- Lite：内置《最后一吻》和《Call of Silence》。\n'
                f"- Full：内置 {info['editions']['Full']['sheet_count']:,} 首曲谱。\n"
                '- 支持 Windows x64；无需安装 Python。升级保留已有配置与收藏。\n'
                '- SHA256SUMS.txt 提供安装包校验值，build-info.json 记录构建来源。\n')
    (installers / 'release-notes.md').write_text(section, encoding='utf-8')
    print(json.dumps(assets, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('metadata', 'stage', 'finalize'))
    parser.add_argument('--release-tag', default='')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'windows')
    parser.add_argument('--github-output', type=Path)
    args = parser.parse_args()
    if args.operation == 'metadata':
        info = metadata(args.release_tag)
        if args.github_output:
            with args.github_output.open('a', encoding='utf-8') as stream:
                stream.write(f"version={info['version']}\n")
        print(json.dumps(info, ensure_ascii=False))
    elif args.operation == 'stage':
        stage(args.output.resolve(), args.release_tag)
    else:
        finalize(args.output.resolve(), args.release_tag)


if __name__ == '__main__':
    main()
