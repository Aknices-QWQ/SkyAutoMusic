import argparse
import json
from collections import Counter
from pathlib import Path

import mido


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
        edge_penalty = 0
        for note in notes:
            idx, error = nearest_sky_index(note, base)
            total_error += error
            if note < base or note > base + 24:
                range_penalty += 12
            if idx in (0, 14):
                edge_penalty += 1.5
        score = total_error + range_penalty + edge_penalty
        if best is None or score < best[0]:
            best = (score, base)
    return best[1]


def midi_to_note_events(midi_path):
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


def filter_melody(events, minimum_note=43, maximum_note=82):
    events = [(t, n, v) for t, n, v in events if minimum_note <= n <= maximum_note]
    if not events:
        return []
    velocities = [v for _, _, v in events]
    velocity_floor = max(1, sorted(velocities)[len(velocities) // 6] - 1)
    events = [(t, n, v) for t, n, v in events if v >= velocity_floor]

    # Keep the highest note at near-simultaneous starts. Vocal MIDI often contains octave artifacts.
    buckets = {}
    for t, n, v in events:
        bucket = int(round(t / 80) * 80)
        old = buckets.get(bucket)
        if old is None or (n, v) > (old[1], old[2]):
            buckets[bucket] = (t, n, v)
    return sorted(buckets.values(), key=lambda item: item[0])


def convert(midi_path, output_path, song_name, author, transcribed_by, bpm, min_note, max_note):
    events = midi_to_note_events(midi_path)
    filtered = filter_melody(events, min_note, max_note)
    if not filtered:
        raise RuntimeError("No usable melody notes found")
    base = choose_base([n for _, n, _ in filtered])

    notes = []
    for time_ms, note, _ in filtered:
        idx, _ = nearest_sky_index(note, base)
        notes.append({"time": time_ms, "key": f"1Key{idx}"})

    unique = []
    seen = set()
    for item in sorted(notes, key=lambda item: (item["time"], item["key"])):
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
        "source": str(midi_path),
        "conversionBaseMidiNote": base,
        "sourceNoteRange": [min_note, max_note],
        "songNotes": unique,
    }]
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(unique), base, Counter(item["key"] for item in unique).most_common(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("midi")
    parser.add_argument("output")
    parser.add_argument("--song-name", required=True)
    parser.add_argument("--author", default="")
    parser.add_argument("--transcribed-by", default="用户提供 MIDI 转换")
    parser.add_argument("--bpm", type=int, default=180)
    parser.add_argument("--min-note", type=int, default=43)
    parser.add_argument("--max-note", type=int, default=82)
    args = parser.parse_args()
    count, base, common = convert(
        Path(args.midi),
        Path(args.output),
        args.song_name,
        args.author,
        args.transcribed_by,
        args.bpm,
        args.min_note,
        args.max_note,
    )
    print(f"wrote {count} notes, base={base}, common={common}")


if __name__ == "__main__":
    main()
