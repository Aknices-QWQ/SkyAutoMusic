import argparse
import json
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKY_SCALE = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24]

# Pitch class of each tonic (0 = C). Enharmonic spellings map to the same class.
KEY_TONICS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "F": 5,
    "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10,
    "B": 11, "CB": 11, "B#": 0, "E#": 5, "FB": 4,
}


def parse_key(key):
    """Split a key name such as ``Ab``, ``F#m`` or ``Emin`` into (root, mode)."""
    key = key.strip()
    if key.endswith("min"):
        return key[:-3].strip().upper(), "minor"
    if key.endswith("m") and len(key) > 1:
        return key[:-1].strip().upper(), "minor"
    return key.strip().upper(), "major"


def transposition_semitones(key):
    """Semitone shift that moves a major key to C major or a minor key to A minor.

    Sky's 15 keys are exactly the white notes of C major / A minor, so a
    chromatic key must be transposed (not note-by-note rounded) to keep the
    melody's intervals intact.
    """
    root, mode = parse_key(key)
    tonic = KEY_TONICS.get(root)
    if tonic is None:
        raise ValueError(f"无法识别的调号: {key!r}")
    target = 9 if mode == "minor" else 0  # A for minor, C for major
    delta = (target - tonic) % 12
    if delta > 6:
        delta -= 12
    return delta


def fold_note_to_window(note, base):
    """Fold a pitch by octaves into the two-octave Sky keyboard window."""
    while note < base:
        note += 12
    while note > base + 24:
        note -= 12
    return note


def nearest_sky_index(note, base):
    relative = note - base
    return min(
        range(len(SKY_SCALE)),
        key=lambda index: (abs(SKY_SCALE[index] - relative), index),
    )


def iter_note_timestamp_items(space_data, root_name):
    for page in space_data[root_name]:
        for system in page.get("systemTimesTamp", []):
            for timestamp in system.get("noteTimestamp", []):
                yield timestamp


def load_pitch_map(space_data, all_hands=False):
    """Read note pitches from notesPositionInfo, which has the original score data.

    PiaStudy's ``key`` field is ``[null, rightHandPitches, leftHandPitches, ...]``:
    each list slot is one hand/voice. With ``all_hands`` every list slot is merged
    so chords spanning both hands are kept.
    """
    pitch_map = {}
    for timestamp in iter_note_timestamp_items(space_data, "notesPositionInfo"):
        name = timestamp.get("noteName")
        key_data = timestamp.get("key")
        if not name or not isinstance(key_data, list):
            continue
        if all_hands:
            pitches = []
            for slot in key_data:
                if isinstance(slot, list):
                    pitches.extend(pitch for pitch in slot if isinstance(pitch, int))
        else:
            pitches = key_data[1] if len(key_data) > 1 and isinstance(key_data[1], list) else []
        if not pitches:
            continue
        pitch_map[name] = pitches
    if not pitch_map:
        raise RuntimeError("space.json 中没有找到 notesPositionInfo 音高数据")
    return pitch_map


def load_timeline(space_data, pitch_map, strict=True):
    """Expand the original PiaStudy timeline, including its explicit repeats.

    With ``strict`` any timeline note missing from the pitch map raises; without
    it (melody-only mode) notes that only exist in another hand are skipped.
    """
    events = []
    missing = []
    for timestamp in iter_note_timestamp_items(space_data, "timesTamp"):
        for note_info in timestamp.get("notesInfo", []):
            name = note_info.get("noteName")
            time_ms = note_info.get("startTime")
            if name not in pitch_map or not isinstance(time_ms, (int, float)):
                missing.append(name)
                continue
            events.append((int(round(time_ms)), pitch_map[name]))
    if strict and missing:
        raise RuntimeError(f"timesTamp 中有 {len(missing)} 个音符找不到原始音高")
    if not events:
        raise RuntimeError("space.json 中没有找到可演奏音符")
    return events


# ---------------------------------------------------------------------------
# Auto-detection helpers
# ---------------------------------------------------------------------------

def auto_song_name(space_path):
    """Derive a song name from the space.json filename (e.g. ``恋人（原声版）``)."""
    stem = space_path.stem
    for suffix in ("-piastudy-space", "-space", "-piastudy"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def find_sibling_midi(space_path):
    """Locate the matching MIDI next to the space.json (same directory)."""
    stem = space_path.stem.replace("-piastudy-space", "").replace("-space", "")
    candidates = sorted(space_path.parent.glob("*.mid")) + sorted(space_path.parent.glob("*.midi"))
    for path in candidates:
        if path.stem.startswith(stem) or stem in path.stem:
            return path
    return candidates[0] if candidates else None


def melody_pitches(space_data):
    pitches = []
    for timestamp in iter_note_timestamp_items(space_data, "notesPositionInfo"):
        key_data = timestamp.get("key")
        if isinstance(key_data, list) and len(key_data) > 1 and isinstance(key_data[1], list):
            pitches.extend(p for p in key_data[1] if isinstance(p, int))
    return pitches


def detect_all_hands(space_data):
    """True when the score has a left-hand voice (key[2] carries any pitch)."""
    for timestamp in iter_note_timestamp_items(space_data, "notesPositionInfo"):
        key_data = timestamp.get("key")
        if isinstance(key_data, list) and len(key_data) > 2 and isinstance(key_data[2], list):
            if any(isinstance(p, int) for p in key_data[2]):
                return True
    return False


def auto_base(pitches):
    """Choose the C-octave anchor from the melody: C4 for a C4-ish range, else C3."""
    if not pitches:
        return 60
    median = sorted(pitches)[len(pitches) // 2]
    return 60 if median >= 60 else 48


def read_midi_meta(midi_path):
    """Return (key_signature, bpm) read from a MIDI file, or (None, None)."""
    try:
        import mido
    except ImportError:
        return None, None
    try:
        mid = mido.MidiFile(str(midi_path))
    except Exception:
        return None, None
    key = None
    tempos = []
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "key_signature" and key is None:
                key = msg.key
            if msg.type == "set_tempo":
                tempos.append((abs_tick, msg.tempo))
    bpm = None
    if tempos:
        start_tempos = [tempo for tick, tempo in tempos if tick == 0]
        tempo = start_tempos[-1] if start_tempos else tempos[0][1]
        bpm = round(60_000_000 / tempo)
    return key, bpm


def fetch_page_meta(page_url):
    """Fetch the PiaStudy page and return {songName, title, artist, compose, keynote}."""
    import re
    import urllib.request
    out = {}
    try:
        req = urllib.request.Request(
            page_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"},
        )
        html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        for field in ("songName", "title", "artist", "compose", "keynote"):
            match = re.search(r'&quot;' + field + r'&quot;:\[0,&quot;(.*?)&quot;\]', html)
            if match:
                out[field] = match.group(1)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def convert(space_path, output_path, song_name, author, transcribed_by, bpm, base, all_hands=False, transpose=0):
    space_data = json.loads(space_path.read_text(encoding="utf-8"))
    pitch_map = load_pitch_map(space_data, all_hands=all_hands)
    timeline = load_timeline(space_data, pitch_map, strict=all_hands)

    notes = []
    seen = set()
    mapping_errors = Counter()
    for time_ms, pitches in timeline:
        # Repeated pitches in one PiaStudy note are tie/hold markers, not extra hits.
        for pitch in dict.fromkeys(pitches):
            folded = fold_note_to_window(pitch + transpose, base)
            key_index = nearest_sky_index(folded, base)
            mapped_pitch = base + SKY_SCALE[key_index]
            mapping_errors[abs(mapped_pitch - folded)] += 1
            identity = (time_ms, key_index)
            if identity in seen:
                continue
            seen.add(identity)
            notes.append({"time": time_ms, "key": f"1Key{key_index}"})

    notes.sort(key=lambda item: (item["time"], item["key"]))
    meta = {
        "name": song_name,
        "songName": song_name,
        "author": author,
        "transcribedBy": transcribed_by,
        "bpm": bpm,
        "bitsPerPage": 16,
        "pitchLevel": 0,
        "isComposed": True,
        "source": str(space_path),
        "sourceFormat": "PiaStudy space.json",
        "conversionBaseMidiNote": base,
        "octaveFoldedToSkyRange": True,
        "deduplicatedPiaStudyTiePitches": True,
        "preservedPiaStudyTimeline": True,
        "includeAllHands": all_hands,
        "transposedSemitones": transpose,
        "songNotes": notes,
    }
    output_path.write_text(
        json.dumps([meta], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(notes), min(item["time"] for item in notes), max(item["time"] for item in notes), mapping_errors


def main():
    parser = argparse.ArgumentParser(
        description="Convert PiaStudy space.json to SkyAutoMusic JSON. "
                    "BPM/key/base/hands are auto-detected from the sibling MIDI and the score; "
                    "pass only the space.json in the common case."
    )
    parser.add_argument("space_json", help="path to the -piastudy-space.json")
    parser.add_argument("--output", default=None, help="output path (default: Sheet Music/<song-name>.json)")
    parser.add_argument("--midi", default=None, help="MIDI file for key/BPM auto-detection (default: sibling *.mid)")
    parser.add_argument("--page-url", default=None, help="PiaStudy page URL to auto-fill song name / author")

    parser.add_argument("--song-name", default=None)
    parser.add_argument("--author", default=None)
    parser.add_argument("--transcribed-by", default="piastudy space.json / Codex 转 Sky")
    parser.add_argument("--bpm", type=int, default=None)
    parser.add_argument("--base-midi-note", type=int, default=None)
    parser.add_argument("--transpose-key", default=None,
                        help="override source key (e.g. Ab, F, Am); otherwise read from MIDI key signature")
    hands = parser.add_mutually_exclusive_group()
    hands.add_argument("--all-hands", action="store_true", help="force both hands")
    hands.add_argument("--melody", action="store_true", help="force melody only")
    args = parser.parse_args()

    space_path = Path(args.space_json)
    if not space_path.exists():
        parser.error(f"找不到文件: {space_path}")

    space_data = json.loads(space_path.read_text(encoding="utf-8"))

    # --- hands: auto-detect, explicit flags override ---
    auto_hands = detect_all_hands(space_data)
    if args.all_hands:
        all_hands = True
    elif args.melody:
        all_hands = False
    else:
        all_hands = auto_hands

    # --- song name: filename-derived, with a suffix for a forced variant ---
    song_name = args.song_name or auto_song_name(space_path)
    if args.song_name is None:
        if args.melody and auto_hands:
            song_name += "·旋律"
        elif args.all_hands and not auto_hands:
            song_name += "·双手"

    author = args.author
    page_meta = {}
    if args.page_url:
        page_meta = fetch_page_meta(args.page_url)
    if author is None:
        author = page_meta.get("artist") or page_meta.get("author") or ""

    midi_path = Path(args.midi) if args.midi else find_sibling_midi(space_path)
    midi_key, midi_bpm = read_midi_meta(midi_path) if midi_path else (None, None)

    bpm = args.bpm or midi_bpm or 120
    transpose = transposition_semitones(args.transpose_key or midi_key) if (args.transpose_key or midi_key) else 0
    base = args.base_midi_note or auto_base(melody_pitches(space_data))

    output_path = Path(args.output) if args.output else (REPO_ROOT / "Sheet Music" / f"{song_name}.json")

    print(f"检测结果: 曲名={song_name!r} 作者={author!r} 调号={midi_key!r}(转调{transpose:+d}) "
          f"BPM={bpm} 基准音={base} 双手={all_hands}")
    print(f"输出: {output_path}")

    count, first_time, last_time, errors = convert(
        space_path, output_path, song_name, author, args.transcribed_by,
        bpm, base, all_hands=all_hands, transpose=transpose,
    )
    print(
        f"wrote {count} notes, time={first_time}..{last_time} ms, "
        f"transpose={transpose} semitones, "
        f"mapping_error_semitones={dict(sorted(errors.items()))}"
    )


if __name__ == "__main__":
    main()
