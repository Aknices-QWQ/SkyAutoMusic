"""User-managed Sky instrument samples for local previews."""

import os
import hashlib
import urllib.parse
import urllib.request
import urllib.error
import wave
from pathlib import Path


SPECY_INSTRUMENTS = (
    "Aurora", "Bells", "Contrabass", "Drum", "DunDun", "Flute",
    "GrandPiano", "Guitar", "HandPan", "Harp", "Horn", "Kalimba",
    "LightGuitar", "MantaOcarina", "Ocarina", "Panflute", "Piano",
    "Pipa", "ToyUkulele", "Trumpet", "WinterPiano", "Xylophone",
)
SAMPLE_COUNTS = {"Bells": 8, "Drum": 8, "DunDun": 8, "HandPan": 8}
INSTRUMENT_LABELS = {
    "Aurora": "欧若拉之声", "Bells": "铃铛", "Contrabass": "低音提琴",
    "Drum": "鼓", "DunDun": "咚咚鼓", "Flute": "长笛",
    "GrandPiano": "三角钢琴", "Guitar": "吉他", "HandPan": "手碟",
    "Harp": "竖琴", "Horn": "圆号", "Kalimba": "拇指琴",
    "LightGuitar": "轻吉他", "MantaOcarina": "遥鲲陶笛",
    "Ocarina": "陶笛", "Panflute": "排箫", "Piano": "钢琴",
    "Pipa": "琵琶", "ToyUkulele": "玩具尤克里里",
    "Trumpet": "小号", "WinterPiano": "冬日钢琴", "Xylophone": "木琴",
}
# OpenMoji pictograms for instruments with matching Unicode symbols.
INSTRUMENT_ICON_CODES = {
    "Aurora": "1F3A4", "Bells": "1F514", "Contrabass": "1F3BB",
    "Drum": "1F941", "DunDun": "1F941", "Flute": "1FA88",
    "GrandPiano": "1F3B9", "Guitar": "1F3B8", "Harp": "1FA89",
    "Horn": "1F3BA", "LightGuitar": "1F3B8", "Piano": "1F3B9",
    "Trumpet": "1F3BA", "WinterPiano": "1F3B9",
}
INSTRUMENT_CUSTOM_ICONS = {
    "HandPan": "handpan.svg", "Kalimba": "kalimba.svg",
    "MantaOcarina": "ocarina.svg", "Ocarina": "ocarina.svg",
    "Panflute": "panflute.svg", "Pipa": "pipa.svg",
    "ToyUkulele": "ukulele.svg", "Xylophone": "xylophone.svg",
}
PITCHES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")

# Specific spots from https://www.bilibili.com/read/mobile?id=15735140 .
# The realm alone is not a reliable key: background music and timing matter.
LOCATION_PRESETS = (
    ("遇境", "C", ""),
    ("晨岛 · 沙漠", "C", ""),
    ("晨岛 · 终点旁小岛", "Db", ""),
    ("晨岛 · 终点祭坛", "Ab", ""),
    ("晨岛 · 飞往终点的悬崖", "G", ""),
    ("预言山谷 · 中间", "C", ""),
    ("预言山谷 · 入口", "Ab", ""),
    ("云野 · 初始图后方", "D", ""),
    ("云野 · 中央大图", "D", ""),
    ("云野 · 右侧隐藏图", "D", ""),
    ("云野 · 八人图", "Db", ""),
    ("云野 · 三塔终点大塔", "F", ""),
    ("云野 · 三塔第二塔", "Bb", ""),
    ("圣岛", "D", ""),
    ("雨林 · 入口大厅", "G", ""),
    ("雨林 · 第三至四门左亭", "C", ""),
    ("雨林 · 第三至四门右亭", "Eb", ""),
    ("雨林 · 钟塔前三塔", "Db", "背景音乐结束后"),
    ("雨林 · 钟塔最后一塔", "Gb", "点出水母、背景音乐结束后"),
    ("雨林 · 第四门后小金人亭", "Db", ""),
    ("雨林 · 最后一塔", "Gb", ""),
    ("雨林 · 两金人隐藏图", "F", ""),
    ("雨林 · 四金人隐藏图入口", "C", ""),
    ("雨林 · 终点", "C", ""),
    ("雨林至霞谷 · 过渡图", "Bb", ""),
    ("霞谷 · 溜冰场三岔口", "A", ""),
    ("霞谷 · 霞光城", "C", ""),
    ("霞谷 · 飞行赛道城堡", "F", ""),
    ("霞谷 · 赛道终点", "Ab", "背景音乐结束后"),
    ("霞谷 · 终点", "D", ""),
    ("霞谷 · 迷宫", "D", ""),
    ("暮土 · 无龙图", "Eb", ""),
    ("暮土 · 方舟", "Eb", ""),
    ("暮土 · 三龙图", "D", ""),
    ("暮土 · 四龙图", "Eb", ""),
    ("暮土 · 沉船", "Eb", ""),
    ("暮土 · 终点", "D", ""),
    ("禁阁 · 入口至一楼", "D", ""),
    ("禁阁 · 一楼", "F", ""),
    ("禁阁 · 二楼", "D", ""),
    ("禁阁 · 三楼", "Db", ""),
    ("禁阁 · 四楼塔顶", "A", ""),
    ("禁阁 · 终点祭坛", "A", ""),
    ("办公室 · 两入口之间", "F", ""),
    ("暴风眼 · 第一阶段", "C", ""),
    ("重生之路 · 星河", "E", ""),
)
SOURCE_URL = "https://github.com/Specy/genshin-music/tree/main/public/assets/audio/sky"
RAW_BASE_URL = "https://raw.githubusercontent.com/Specy/genshin-music/main/public/assets/audio/sky"
SAMPLE_SUFFIXES = (".wav", ".mp3", ".ogg", ".m4a", ".flac")
MAX_SAMPLE_BYTES = 4 * 1024 * 1024


def sample_files(folder, required_count=15):
    """Find a complete zero-based set in one instrument directory."""
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    found = {}
    for index in range(required_count):
        for suffix in SAMPLE_SUFFIXES:
            path = folder / f"{index}{suffix}"
            if path.is_file() and path.stat().st_size > 0:
                found[index] = path
                break
    return found if len(found) == required_count else {}


def instrument_folders(root):
    """Accept an instrument folder or its parent, as found in source archives."""
    root = Path(root)
    if sample_files(root, SAMPLE_COUNTS.get(root.name, 15)):
        return [(root.name, root)]
    if not root.is_dir():
        return []
    return [
        (child.name, child)
        for child in sorted(root.iterdir(), key=lambda path: path.name.lower())
        if child.is_dir() and sample_files(child, SAMPLE_COUNTS.get(child.name, 15))
    ]


def pitch_semitones(pitch):
    return PITCHES.index(pitch) if pitch in PITCHES else 0


def pitched_wave(source, pitch, cache_dir):
    """Change a PCM sample's playback pitch without decoding during each note."""
    source = Path(source)
    if pitch_semitones(pitch) == 0:
        return source
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    stat = source.stat()
    identity = f"{source.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{pitch}"
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    target = cache_dir / f"sky-{source.stem}-{pitch}-{digest}.wav"
    if target.is_file() and target.stat().st_size > 44:
        return target
    with wave.open(str(source), "rb") as reader:
        if reader.getcomptype() != "NONE" or reader.getsampwidth() != 2:
            raise ValueError("音色需要 16 位 PCM WAV")
        channels = reader.getnchannels()
        rate = round(reader.getframerate() * (2 ** (pitch_semitones(pitch) / 12)))
        frames = reader.readframes(reader.getnframes())
    partial = target.with_suffix(".wav.part")
    try:
        with wave.open(str(partial), "wb") as writer:
            writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
            writer.writeframes(frames)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
    return target


def download_specy_instrument(name, destination, progress=None):
    """Repair missing bundled samples only after the user requests it."""
    if name not in SPECY_INSTRUMENTS:
        raise ValueError("未知的光遇音色。")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    required_count = SAMPLE_COUNTS.get(name, 15)
    # Prefer a direct connection, then honor the system proxy once if the
    # network blocks direct GitHub access.
    openers = [urllib.request.build_opener(urllib.request.ProxyHandler({})),
               urllib.request.build_opener()]
    preferred_opener = 0
    for index in range(required_count):
        target = destination / f"{index}.mp3"
        if target.is_file() and target.stat().st_size > 100:
            if progress:
                progress(index + 1, required_count)
            continue
        url = f"{RAW_BASE_URL}/{urllib.parse.quote(name)}/{index}.mp3"
        partial = target.with_suffix(".mp3.part")
        try:
            last_error = None
            for opener_index in range(preferred_opener, len(openers)):
                try:
                    with openers[opener_index].open(url, timeout=20) as response:
                        data = response.read(MAX_SAMPLE_BYTES + 1)
                    preferred_opener = opener_index
                    break
                except (OSError, urllib.error.URLError) as error:
                    last_error = error
            else:
                raise last_error
            if len(data) < 100 or len(data) > MAX_SAMPLE_BYTES or not (
                data.startswith(b"ID3") or data[:1] == b"\xff"
            ):
                raise ValueError(f"音色文件 {index}.mp3 无效。")
            partial.write_bytes(data)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        if progress:
            progress(index + 1, required_count)
    if not sample_files(destination, required_count):
        raise ValueError("音色文件不完整，请重试下载。")
    return destination
