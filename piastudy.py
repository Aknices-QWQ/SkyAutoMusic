"""PiaStudy 搜索 + MIDI / space.json 转 SkyAutoMusic 曲谱的纯标准库实现。

不依赖第三方库（mido 等），可直接被桌面程序导入，也可从源码运行。
"""

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# PiaStudy 搜索页的反爬 cookie（初始值；失效时会从挑战脚本自动重解）。
PIASTUDY_COOKIES = "__tst_status=1948513005#; EO_Bot_Ssid=1775239168"
_current_cookie = PIASTUDY_COOKIES

SKY_SCALE = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24]

KEY_TONICS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10,
    "B": 11, "CB": 11, "B#": 0, "E#": 5, "FB": 4,
}

# MIDI key_signature sf -> 大调名（相对小调共用同一调号，转调结果一致）。
MAJOR_KEYS_BY_SF = {
    -7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F",
    0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
}


# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

def _validate_page_url(page_url):
    """Validate and normalize a PiaStudy detail-page URL."""
    value = str(page_url or "").strip()
    if not value:
        raise ValueError("请输入 PiaStudy 歌曲详情页链接")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower().split(":", 1)[0] not in {
        "piastudy.com", "www.piastudy.com"
    }:
        raise ValueError("链接必须是 piastudy.com 的歌曲详情页")
    if not parsed.path or parsed.path == "/" or parsed.path.lower().startswith("/search"):
        raise ValueError("请输入 PiaStudy 歌曲详情页链接，而不是搜索页链接")
    return value

def _fetch(url, referer=None, attempts=4):
    """Fetch bytes, retrying transient TLS/connection drops.

    The PiaStudy CDN (i.insstudy.com) often closes the connection during the TLS
    handshake when requests arrive back to back, which surfaces as
    ``SSL: UNEXPECTED_EOF_WHILE_READING``. A short backoff makes it succeed.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "close",
    }
    if referer:
        headers["Referer"] = referer
    if "piastudy.com" in urllib.parse.urlparse(url).netloc:
        headers["Cookie"] = _current_cookie
    last_error = None
    for attempt in range(attempts):
        try:
            # urllib.request rejects non-ASCII Cookie header values on some
            # Python builds; quote the query only, cookies remain ASCII.
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - retry any transport error
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.7 * (attempt + 1))
    raise last_error


def _fetch_text(url, referer=None):
    return _fetch(url, referer=referer).decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 可选无头浏览器
# ---------------------------------------------------------------------------

def _browser_install_dir():
    """Return a writable per-user directory for the optional Chromium build."""
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    if root:
        return Path(root) / "SkyAutoMusic" / "playwright-browsers"
    return Path.home() / ".cache" / "SkyAutoMusic" / "playwright-browsers"


def _playwright_env():
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(_browser_install_dir())
    return env


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("当前版本没有包含无头浏览器组件，请更新到最新版本") from exc
    return sync_playwright


def browser_status():
    """Return optional browser availability without downloading anything."""
    try:
        sync_playwright = _load_playwright()
        with sync_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
        return {
            "available": True,
            "installed": executable.is_file(),
            "path": str(executable),
        }
    except Exception as exc:  # noqa: BLE001 - status is best effort for the UI
        return {"available": False, "installed": False, "path": "", "error": str(exc)}


def install_browser(progress=None):
    """Download Chromium on demand using the bundled Playwright driver."""
    sync_playwright = _load_playwright()
    _browser_install_dir().mkdir(parents=True, exist_ok=True)
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver_executable, driver_cli = compute_driver_executable()
    except Exception as exc:  # pragma: no cover - depends on optional package layout
        raise RuntimeError("无法启动无头浏览器下载器") from exc

    env = get_driver_env()
    env.update(_playwright_env())
    command = [driver_executable, driver_cli, "install", "chromium"]
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = []
    if process.stdout:
        for line in process.stdout:
            line = line.strip()
            if line:
                output.append(line)
                if progress:
                    progress(line)
    code = process.wait()
    if code:
        detail = output[-1] if output else f"退出码 {code}"
        raise RuntimeError(f"无头浏览器下载失败：{detail}")
    status = browser_status()
    if not status.get("installed"):
        raise RuntimeError("无头浏览器下载完成，但没有找到 Chromium 可执行文件")
    return status


@contextmanager
def _browser_session():
    """Open one browser context so page cookies are reused for CDN resources."""
    sync_playwright = _load_playwright()
    status = browser_status()
    if not status.get("installed"):
        raise RuntimeError("请先点击“下载无头浏览器”，再启用浏览器扒谱")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="zh-CN",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
        )
        try:
            yield context
        finally:
            context.close()
            browser.close()


def _browser_fetch_page(context, url, referer=None):
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30_000, referer=referer)
        # The challenge and the song payload are populated by page scripts.
        page.wait_for_timeout(900)
        return page.content()
    finally:
        page.close()


def _browser_fetch_bytes(context, url, referer=None):
    headers = {"Referer": referer} if referer else None
    response = context.request.get(url, headers=headers, timeout=30_000)
    if not response.ok:
        raise RuntimeError(f"浏览器下载资源失败（HTTP {response.status}）")
    return response.body()


def _extract_cookies_from_challenge(js):
    """从反爬挑战脚本里解出 cookie（脚本是自包含的常量运算）。"""
    cookies = {}
    # 新旧挑战脚本都把 cookie 值作为数字常量写入脚本。优先读取
    # location.href 重定向前的赋值附近数字，兼容压缩后无 var e 的版本。
    match = re.search(r'EO_Bot_Ssid=', js)
    if match:
        numbers = re.findall(r"\d{6,}", js[match.end():match.end() + 500])
        if numbers:
            # 挑战函数按固定顺序把第一个常量拼到 EO_Bot_Ssid= 后面；
            # 后续数字属于 __tst_status 的计算，不能误取。
            cookies["EO_Bot_Ssid"] = numbers[0]
    # __tst_status：var e={...} 对象里所有数值项之和。
    match = re.search(r"(?:var\s+)?e\s*=\s*\{([^}]*)\}", js)
    if match:
        total = 0
        for _, value in re.findall(r"([A-Za-z_$][\w$]*)\s*:\s*(\d+)", match.group(1)):
            total += int(value)
        if total:
            cookies["__tst_status"] = f"{total}#"
    # 压缩挑战（例如 `var e={...}` 被改名或嵌入函数）仍会包含一组
    # 10 位左右的常量，且 __tst_status 是这些常量的和。根据脚本中
    # `t+=...` 的数值表达式求和，避免依赖变量名和格式化方式。
    if "__tst_status" not in cookies:
        assigns = re.findall(r"t\s*\+=\s*[^;]*?(\d{7,})", js)
        if len(assigns) >= 2:
            total = sum(int(value) for value in assigns)
            if total:
                cookies["__tst_status"] = f"{total}#"
    if cookies.get("EO_Bot_Ssid") and cookies.get("__tst_status"):
        return f"__tst_status={cookies['__tst_status']}; EO_Bot_Ssid={cookies['EO_Bot_Ssid']}"
    return None


def _fetch_search_html(url, referer=None):
    """抓取搜索页；被反爬拦截（挑战脚本）时自动重解 cookie 并退避重试。"""
    global _current_cookie
    text = ""
    for _ in range(4):
        text = _fetch_text(url, referer=referer)
        if not ("EO_Bot_Ssid" in text and len(text) < 5000):
            return text
        renewed = _extract_cookies_from_challenge(text)
        if renewed:
            _current_cookie = renewed
        time.sleep(1.5)
    return text


def search(query, page=1):
    """搜索 PiaStudy，返回结果列表。"""
    if not query:
        return []
    path = f"/searchResults/{int(page)}/{urllib.parse.quote(query, safe='')}/0/0/1"
    html = _fetch_search_html(f"https://piastudy.com{path}", referer="https://piastudy.com/")
    return _parse_search_html(html)


def _parse_search_html(html):
    results = []
    # class 名称会随前端构建变化，按详情页链接切块更稳定。
    blocks = re.split(r'(?=<a[^>]+href="/[A-Za-z]+/[A-Za-z0-9]+")', html)
    for block in blocks:
        match = re.search(r'href="(/[A-Za-z]+/[A-Za-z0-9]+)"', block)
        if not match:
            continue
        path = match.group(1)
        title = _strip_tags(re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S))
        author = _strip_tags(re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S))

        def field(label):
            found = re.search(label + r"\s*[:：]\s*([^<]+)", block)
            return found.group(1).strip() if found else ""

        results.append({
            "title": title,
            "author": author.replace("\n", " ").strip(),
            "keynote": field("调性"),
            "difficulty": field("难度"),
            "heat": field("热度"),
            "path": path,
            "page_url": "https://piastudy.com" + path,
        })
    return results


def _strip_tags(match):
    if not match:
        return ""
    text = re.sub(r"<[^>]+>", "", match.group(1))
    return text.replace("&amp;", "&").strip()


def detail(page_url, use_browser=False, _browser_context=None):
    """解析详情页，返回标题/演唱者/作曲/调性/资源地址。"""
    import html as html_lib
    page_url = _validate_page_url(page_url)
    if _browser_context is not None:
        text = _browser_fetch_page(_browser_context, page_url, referer="https://piastudy.com/")
    elif use_browser:
        with _browser_session() as context:
            return detail(page_url, use_browser=True, _browser_context=context)
    else:
        text = _fetch_text(page_url, referer="https://piastudy.com/")

    if "EO_Bot_Ssid" in text and len(text) < 5000:
        raise RuntimeError("PiaStudy 返回了反爬挑战，请启用无头浏览器后重试")

    midi_match = re.search(r'&quot;midiUrl&quot;:\[0,&quot;(.*?)&quot;\]', text)
    space_match = re.search(r'&quot;spaceUrl&quot;:\[0,&quot;(.*?)&quot;\]', text)
    midi_url = midi_match.group(1) if midi_match else ""
    space_url = space_match.group(1) if space_match else ""
    # 歌曲自身字段出现在 midiUrl 之前；页面后段的推荐曲列表也有 title 字段，
    # 因此只在 midiUrl 之前的区段里取最后一次匹配。
    end = midi_match.start() if midi_match else (space_match.start() if space_match else 0)

    def last_field(field, numeric=False):
        if numeric:
            pattern = re.compile(r'&quot;' + field + r'&quot;:\[0,([0-9]+)\]')
        else:
            pattern = re.compile(r'&quot;' + field + r'&quot;:\[0,&quot;(.*?)&quot;\]')
        last = ""
        for match in pattern.finditer(text, 0, end):
            last = match.group(1)
        return html_lib.unescape(last)

    return {
        "title": last_field("title"),
        "songName": last_field("songName"),
        "artist": last_field("artist"),
        "compose": last_field("compose"),
        "keynote": last_field("keynote"),
        "difficulty": last_field("difficulty", numeric=True),
        "midi_url": html_lib.unescape(midi_url),
        "space_url": html_lib.unescape(space_url),
    }


# ---------------------------------------------------------------------------
# MIDI 解析（纯标准库）
# ---------------------------------------------------------------------------

def _read_varint(data, pos):
    value = 0
    while True:
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return value, pos


def parse_midi_data(data):
    """解析 MIDI 字节，返回 {bpm, key, notes:[(ms,note,vel)], duration_ms}。"""
    if data[:4] != b"MThd":
        raise RuntimeError("不是有效的 MIDI 文件")
    pos = 4
    header_len = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
    fmt = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    ntracks = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    division = int.from_bytes(data[pos:pos + 2], "big"); pos += 2
    pos += max(0, header_len - 6)
    ticks_per_beat = division if (division & 0x8000) == 0 else 480

    raw = []  # (abs_tick, kind, value)
    for _ in range(ntracks):
        if data[pos:pos + 4] != b"MTrk":
            break
        pos += 4
        track_len = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
        track_end = pos + track_len
        abs_tick = 0
        running = 0
        while pos < track_end:
            delta, pos = _read_varint(data, pos)
            abs_tick += delta
            status = data[pos]
            if status == 0xFF:
                pos += 1
                meta_type = data[pos]; pos += 1
                length, pos = _read_varint(data, pos)
                payload = data[pos:pos + length]; pos += length
                if meta_type == 0x51 and length >= 3:
                    raw.append((abs_tick, "tempo", int.from_bytes(payload[:3], "big")))
                elif meta_type == 0x59 and length >= 2:
                    sf = payload[0]
                    raw.append((abs_tick, "key", sf - 256 if sf > 127 else sf))
            elif status & 0x80:
                running = status
                pos += 1
                kind = status & 0xF0
                if kind == 0x90:
                    raw.append((abs_tick, "note", (data[pos], data[pos + 1])))
                    pos += 2
                elif kind in (0xC0, 0xD0):
                    pos += 1
                else:
                    pos += 2
            else:
                pos += 1
                kind = running & 0xF0
                if kind == 0x90:
                    raw.append((abs_tick, "note", (status, data[pos])))
                    pos += 1
                elif kind in (0xC0, 0xD0):
                    pass
                else:
                    pos += 1

    tempos = sorted((t, v) for t, k, v in raw if k == "tempo")
    if not tempos:
        tempos = [(0, 500000)]
    key_sf = next((v for t, k, v in raw if k == "key"), None)

    def tick_to_ms(tick):
        elapsed = 0.0
        prev_tick = 0
        prev_tempo = 500000
        for t, tempo in tempos:
            if t >= tick:
                break
            elapsed += (t - prev_tick) * prev_tempo / ticks_per_beat / 1000
            prev_tick = t
            prev_tempo = tempo
        elapsed += (tick - prev_tick) * prev_tempo / ticks_per_beat / 1000
        return elapsed

    notes = []
    for t, k, v in raw:
        if k == "note" and v[1] > 0:
            notes.append((int(round(tick_to_ms(t))), v[0], v[1]))
    notes.sort()

    start_tempos = [tempo for t, tempo in tempos if t == 0]
    tempo = start_tempos[-1] if start_tempos else tempos[0][1]
    bpm = round(60_000_000 / tempo)

    return {
        "bpm": bpm,
        "key": MAJOR_KEYS_BY_SF.get(key_sf) if key_sf is not None else None,
        "notes": notes,
        "duration_ms": notes[-1][0] if notes else 0,
    }


def parse_midi(midi_path):
    """从文件路径解析 MIDI。"""
    return parse_midi_data(Path(midi_path).read_bytes())


# ---------------------------------------------------------------------------
# 转调与 Sky 键位
# ---------------------------------------------------------------------------

def parse_key(key):
    key = key.strip()
    if key.endswith("min"):
        return key[:-3].strip().upper(), "minor"
    if key.endswith("m") and len(key) > 1:
        return key[:-1].strip().upper(), "minor"
    return key.strip().upper(), "major"


def transposition_semitones(key):
    if not key:
        return 0
    root, mode = parse_key(key)
    tonic = KEY_TONICS.get(root)
    if tonic is None:
        return 0
    target = 9 if mode == "minor" else 0
    delta = (target - tonic) % 12
    return delta - 12 if delta > 6 else delta


def fold_note_to_window(note, base):
    while note < base:
        note += 12
    while note > base + 24:
        note -= 12
    return note


def nearest_sky_index(note, base):
    relative = note - base
    return min(range(len(SKY_SCALE)), key=lambda i: (abs(SKY_SCALE[i] - relative), i))


def auto_base(pitches):
    if not pitches:
        return 60
    median = sorted(pitches)[len(pitches) // 2]
    return 60 if median >= 60 else 48


# ---------------------------------------------------------------------------
# 转换
# ---------------------------------------------------------------------------

def _iter_timestamp_items(space_data, root_name):
    """Yield noteTimestamp dicts, tolerating the nulls PiaStudy sometimes emits."""
    if not isinstance(space_data, dict):
        return
    pages = space_data.get(root_name)
    if not isinstance(pages, list):
        return
    for page in pages:
        if not isinstance(page, dict):
            continue
        for system in page.get("systemTimesTamp") or []:
            if not isinstance(system, dict):
                continue
            for timestamp in system.get("noteTimestamp") or []:
                if isinstance(timestamp, dict):
                    yield timestamp


def _space_pitch_map(space_data, all_hands):
    pitch_map = {}
    for timestamp in _iter_timestamp_items(space_data, "notesPositionInfo"):
        name = timestamp.get("noteName")
        key_data = timestamp.get("key")
        if not name or not isinstance(key_data, list):
            continue
        if all_hands:
            pitches = []
            for slot in key_data:
                if isinstance(slot, list):
                    pitches.extend(p for p in slot if isinstance(p, int))
        else:
            pitches = key_data[1] if len(key_data) > 1 and isinstance(key_data[1], list) else []
        if pitches:
            pitch_map[name] = pitches
    return pitch_map


def _space_has_left_hand(space_data):
    for timestamp in _iter_timestamp_items(space_data, "notesPositionInfo"):
        key_data = timestamp.get("key")
        if isinstance(key_data, list) and len(key_data) > 2 and isinstance(key_data[2], list):
            if any(isinstance(p, int) for p in key_data[2]):
                return True
    return False


def _space_melody_pitches(space_data):
    pitches = []
    for timestamp in _iter_timestamp_items(space_data, "notesPositionInfo"):
        key_data = timestamp.get("key")
        if isinstance(key_data, list) and len(key_data) > 1 and isinstance(key_data[1], list):
            pitches.extend(p for p in key_data[1] if isinstance(p, int))
    return pitches


def convert_space_to_sheet(space_data, midi_meta, song_name, author, transcribed_by, melody=False):
    """space.json + 可选 MIDI 元信息 -> SkyAutoMusic 曲谱 [meta]."""
    all_hands = (not melody) and _space_has_left_hand(space_data)
    pitch_map = _space_pitch_map(space_data, all_hands)
    timeline = []
    for timestamp in _iter_timestamp_items(space_data, "timesTamp"):
        for note_info in timestamp.get("notesInfo", []):
            name = note_info.get("noteName")
            time_ms = note_info.get("startTime")
            if name in pitch_map and isinstance(time_ms, (int, float)):
                timeline.append((int(round(time_ms)), pitch_map[name]))

    key = (midi_meta or {}).get("key")
    transpose = transposition_semitones(key)
    base = auto_base(_space_melody_pitches(space_data))

    notes = []
    seen = set()
    for time_ms, pitches in timeline:
        for pitch in dict.fromkeys(pitches):
            idx = nearest_sky_index(fold_note_to_window(pitch + transpose, base), base)
            if (time_ms, idx) in seen:
                continue
            seen.add((time_ms, idx))
            notes.append({"time": time_ms, "key": f"1Key{idx}"})
    notes.sort(key=lambda n: (n["time"], n["key"]))

    return _sheet(song_name, author, transcribed_by, (midi_meta or {}).get("bpm") or 120,
                  base, transpose, all_hands, notes, "PiaStudy space.json")


def convert_midi_to_sheet(midi_path, song_name, author, transcribed_by):
    """MIDI -> SkyAutoMusic 曲谱 [meta]。"""
    midi = parse_midi(midi_path)
    if not midi["notes"]:
        raise RuntimeError("MIDI 中没有音符")
    transpose = transposition_semitones(midi["key"])
    base = auto_base([n for _, n, _ in midi["notes"]])

    # 聚类近距起音为和弦，映射到不同 Sky 键。
    groups = []
    for time_ms, note, velocity in midi["notes"]:
        if groups and time_ms - groups[-1][0] <= 35:
            groups[-1][1].append((note, velocity))
        else:
            groups.append([time_ms, [(note, velocity)]])

    notes = []
    seen = set()
    for time_ms, chord in groups:
        # 高音优先，保底去重到不同键。
        ordered = sorted(chord, key=lambda nv: (nv[1], nv[0]), reverse=True)
        used = set()
        for note, _ in ordered:
            idx = nearest_sky_index(fold_note_to_window(note + transpose, base), base)
            if idx in used:
                continue
            used.add(idx)
            if (time_ms, idx) in seen:
                continue
            seen.add((time_ms, idx))
            notes.append({"time": time_ms, "key": f"1Key{idx}"})
    notes.sort(key=lambda n: (n["time"], n["key"]))

    return _sheet(song_name, author, transcribed_by, midi["bpm"], base, transpose, False, notes, "MIDI")


def _sheet(song_name, author, transcribed_by, bpm, base, transpose, all_hands, notes, source_format):
    return [{
        "name": song_name,
        "songName": song_name,
        "author": author or "",
        "transcribedBy": transcribed_by,
        "bpm": int(bpm),
        "bitsPerPage": 16,
        "pitchLevel": 0,
        "isComposed": True,
        "conversionBaseMidiNote": base,
        "transposedSemitones": transpose,
        "includeAllHands": all_hands,
        "sourceFormat": source_format,
        "songNotes": notes,
    }]


# ---------------------------------------------------------------------------
# 安装到曲库
# ---------------------------------------------------------------------------

def _safe_stem(name, fallback="导入乐谱"):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "").strip())
    return (name[:180].rstrip(" .") or fallback)


def _write_sheet(sheet_dir, sheet, preferred_name):
    sheet_dir = Path(sheet_dir)
    sheet_dir.mkdir(parents=True, exist_ok=True)
    used = {p.stem.casefold() for p in sheet_dir.glob("*.json")}
    stem = _safe_stem(preferred_name)
    candidate = stem
    number = 2
    while candidate.casefold() in used:
        candidate = f"{stem} ({number})"
        number += 1
    target = sheet_dir / f"{candidate}.json"
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")
    import os
    os.replace(temporary, target)
    return target


def install_from_page(page_url, sheet_dir, melody=False, progress=None, use_browser=False):
    """从 PiaStudy 详情页下载 space.json + mid 并转成曲谱。"""
    page_url = _validate_page_url(page_url)

    def report(text):
        if progress:
            progress(text)

    if use_browser:
        report("正在启动无头浏览器…")
        with _browser_session() as context:
            info = detail(page_url, use_browser=True, _browser_context=context)
            if not info.get("space_url") or not info.get("midi_url"):
                raise RuntimeError("该页面没有可下载的曲谱资源")
            report("正在下载曲谱资源…")
            space_data = json.loads(
                _browser_fetch_bytes(context, info["space_url"], referer=page_url).decode("utf-8")
            )
            midi_meta = parse_midi_data(
                _browser_fetch_bytes(context, info["midi_url"], referer=page_url)
            )
    else:
        info = detail(page_url)
        if not info.get("space_url") or not info.get("midi_url"):
            raise RuntimeError("该页面没有可下载的曲谱资源")
        report("正在下载曲谱资源…")
        space_data = json.loads(_fetch(info["space_url"], referer=page_url).decode("utf-8"))
        # Small gap: the CDN drops back-to-back connections.
        time.sleep(0.4)
        midi_meta = parse_midi_data(_fetch(info["midi_url"], referer=page_url))

    report("正在解析…")
    song_name = (info.get("title") or info.get("songName")
                 or Path(urllib.parse.urlparse(page_url).path).name)
    song_name = re.sub(r"\s+([（(])", r"\1", song_name)
    if melody:
        song_name += "·旋律"
    author = info.get("artist") or info.get("compose") or ""
    sheet = convert_space_to_sheet(
        space_data, midi_meta, song_name, author,
        f"PiaStudy {info.get('keynote') or ''}".strip(), melody=melody,
    )
    if not sheet[0]["songNotes"]:
        raise RuntimeError("这首曲子没有解析出可演奏的音符")
    target = _write_sheet(sheet_dir, sheet, song_name)
    return {
        "updated": True,
        "changed": 1,
        "count": len(list(Path(sheet_dir).glob("*.json"))),
        "filename": target.name,
        "song_name": song_name,
        "author": author,
    }


def install_from_midi(midi_path, sheet_dir, song_name=None, author=None, progress=None):
    """把本地 MIDI 转成曲谱并写入曲库。"""
    midi_path = Path(midi_path)
    song_name = song_name or midi_path.stem
    midi = parse_midi(midi_path)
    sheet = convert_midi_to_sheet(
        midi_path, song_name, author or "", "MIDI 导入",
    )
    target = _write_sheet(sheet_dir, sheet, song_name)
    return {
        "updated": True,
        "changed": 1,
        "count": len(list(Path(sheet_dir).glob("*.json"))),
        "filename": target.name,
        "song_name": song_name,
        "bpm": midi["bpm"],
    }
