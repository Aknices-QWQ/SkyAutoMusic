import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import mido

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


SKY_SCALE = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24]


def nearest_sky_index(note, base):
    rel = note - base
    best_index = min(range(len(SKY_SCALE)), key=lambda idx: abs(SKY_SCALE[idx] - rel))
    return best_index, abs(SKY_SCALE[best_index] - rel)


def choose_base(notes):
    best = None
    for base in range(36, 73):
        total_error = 0
        range_penalty = 0
        for note in notes:
            idx, error = nearest_sky_index(note, base)
            total_error += error
            if note < base or note > base + 24:
                range_penalty += 8
        score = total_error + range_penalty
        if best is None or score < best[0]:
            best = (score, base)
    return best[1]


def midi_events(midi_path):
    midi = mido.MidiFile(midi_path)
    tempo = 500000
    current_ms = 0.0
    events = []
    for msg in mido.merge_tracks(midi.tracks):
        current_ms += mido.tick2second(msg.time, midi.ticks_per_beat, tempo) * 1000
        if msg.type == "set_tempo":
            tempo = msg.tempo
        elif msg.type == "note_on" and msg.velocity > 0:
            events.append((int(round(current_ms)), msg.note, msg.velocity))
    return events


def convert_midi(
    midi_path,
    output_path,
    song_name,
    author,
    transcribed_by,
    bpm,
    merge_window_ms=0,
    source_url="",
):
    events = midi_events(midi_path)
    if not events:
        raise RuntimeError("No MIDI note_on events found.")

    velocities = [v for _, _, v in events]
    velocity_floor = max(1, sorted(velocities)[len(velocities) // 5] - 1)
    filtered = [(t, n, v) for t, n, v in events if v >= velocity_floor]
    base = choose_base([n for _, n, _ in filtered])

    notes = []
    for time_ms, note, _ in filtered:
        idx, _ = nearest_sky_index(note, base)
        notes.append({"time": time_ms, "key": f"1Key{idx}"})

    ordered = sorted(notes, key=lambda item: (item["time"], item["key"]))
    if merge_window_ms > 0:
        cluster_time = None
        for item in ordered:
            if cluster_time is None or item["time"] - cluster_time > merge_window_ms:
                cluster_time = item["time"]
            item["time"] = cluster_time

    unique = []
    seen = set()
    for item in ordered:
        key = (item["time"], item["key"])
        if key in seen:
            continue
        unique.append(item)
        seen.add(key)

    data = [{
        "name": song_name,
        "songName": song_name,
        "author": author,
        "transcribedBy": transcribed_by,
        "bpm": bpm,
        "bitsPerPage": 16,
        "pitchLevel": 0,
        "isComposed": False,
        "source": source_url or str(midi_path),
        "sourceMidi": str(midi_path),
        "conversionBaseMidiNote": base,
        "songNotes": unique,
    }]
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(unique), base, Counter(item["key"] for item in unique).most_common(5)


def find_basic_pitch(project_root):
    local = project_root / ".venv-basic-pitch" / "Scripts" / "basic-pitch.exe"
    if local.exists():
        return local
    return Path("basic-pitch")


def run_basic_pitch(basic_pitch, audio_path, work_dir, args):
    work_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        str(basic_pitch),
        "--save-midi",
        "--save-note-events",
        "--onset-threshold", str(args.onset_threshold),
        "--frame-threshold", str(args.frame_threshold),
        "--minimum-note-length", str(args.minimum_note_length),
        "--minimum-frequency", str(args.minimum_frequency),
        "--maximum-frequency", str(args.maximum_frequency),
        str(work_dir),
        str(audio_path),
    ]
    subprocess.run(cmd, check=True, env=env)
    midi_files = sorted(work_dir.glob("*_basic_pitch.mid"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not midi_files:
        raise RuntimeError("Basic Pitch did not produce a MIDI file.")
    return midi_files[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("output")
    parser.add_argument("--song-name", required=True)
    parser.add_argument("--author", default="")
    parser.add_argument("--transcribed-by", default="Basic Pitch 深度学习扒谱")
    parser.add_argument("--bpm", type=int, default=180)
    parser.add_argument("--onset-threshold", type=float, default=0.35)
    parser.add_argument("--frame-threshold", type=float, default=0.25)
    parser.add_argument("--minimum-note-length", type=float, default=90)
    parser.add_argument("--minimum-frequency", type=float, default=120)
    parser.add_argument("--maximum-frequency", type=float, default=1600)
    parser.add_argument(
        "--merge-window-ms",
        type=int,
        default=0,
        help="merge note onsets within this window into one playable chord",
    )
    parser.add_argument("--source-url", default="")
    parser.add_argument("--work-dir", default="sources/basic_pitch")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    audio_path = Path(args.audio)
    output_path = Path(args.output)
    work_dir = project_root / args.work_dir
    basic_pitch = find_basic_pitch(project_root)
    midi_path = run_basic_pitch(basic_pitch, audio_path, work_dir, args)
    count, base, common = convert_midi(
        midi_path,
        output_path,
        args.song_name,
        args.author,
        args.transcribed_by,
        args.bpm,
        args.merge_window_ms,
        args.source_url,
    )
    print(f"wrote {count} notes, base={base}, midi={midi_path}, common={common}")


if __name__ == "__main__":
    main()
