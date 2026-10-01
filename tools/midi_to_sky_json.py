import argparse
import json
from collections import Counter
from collections import defaultdict
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
        for note in notes:
            idx, error = nearest_sky_index(note, base)
            total_error += error
            if note < base or note > base + 24:
                range_penalty += 8
        score = total_error + range_penalty
        if best is None or score < best[0]:
            best = (score, base)
    return best[1]


def fold_note_to_window(note, base):
    """Fold a MIDI pitch by octaves into the Sky instrument's two-octave range."""
    while note < base:
        note += 12
    while note > base + 24:
        note -= 12
    return note


def cluster_events(events, window_ms):
    if window_ms <= 0:
        return [(time_ms, [(note, velocity)]) for time_ms, note, velocity in events]
    groups = []
    for time_ms, note, velocity in sorted(events):
        if not groups or time_ms - groups[-1][0] > window_ms:
            groups.append([time_ms, [(note, velocity)]])
        else:
            groups[-1][1].append((note, velocity))
    return groups


def chord_aware_sky_keys(chord, base):
    """Map every chord tone to a distinct Sky key, preserving pitch class first."""
    scale_pitches = [base + offset for offset in SKY_SCALE]
    source_notes = sorted(chord, key=lambda item: (item[1], item[0]), reverse=True)[:15]
    selected = []
    available = set(range(len(scale_pitches)))
    for note, _ in source_notes:
        def cost(idx):
            pitch = scale_pitches[idx]
            pitch_class_distance = min((pitch - note) % 12, (note - pitch) % 12)
            octave_distance = min(abs(pitch - (note + 12 * shift)) for shift in range(-5, 6))
            register_distance = abs(pitch - note)
            return pitch_class_distance * 1000 + octave_distance * 20 + register_distance

        chosen = min(available, key=cost)
        selected.append(chosen)
        available.remove(chosen)
    return sorted(selected)


def midi_to_events(midi_path):
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


def convert(
    midi_path,
    output_path,
    song_name,
    author,
    transcribed_by,
    bpm,
    base_override=None,
    fold_octaves=False,
    keep_all_notes=False,
    chord_aware=False,
    merge_window_ms=0,
    compensate_press_ms=0,
):
    events = midi_to_events(midi_path)
    if not events:
        raise RuntimeError("No note_on events found")

    # Keep the audible melody/chord notes. Very quiet notes are often pedal or ornaments in web demos.
    if keep_all_notes:
        filtered = events
    else:
        velocities = [v for _, _, v in events]
        velocity_floor = max(1, sorted(velocities)[len(velocities) // 5] - 1)
        filtered = [(t, n, v) for t, n, v in events if v >= velocity_floor]
    base = base_override if base_override is not None else choose_base([n for _, n, _ in filtered])

    notes = []
    if chord_aware:
        groups = cluster_events(filtered, merge_window_ms)
        for group_index, (time_ms, chord) in enumerate(groups):
            adjusted_time = time_ms - group_index * compensate_press_ms
            for idx in chord_aware_sky_keys(chord, base):
                notes.append({"time": adjusted_time, "key": f"1Key{idx}"})
    else:
        for time_ms, note, _ in filtered:
            if fold_octaves:
                note = fold_note_to_window(note, base)
            idx, _ = nearest_sky_index(note, base)
            notes.append({"time": time_ms, "key": f"1Key{idx}"})

    # Remove exact duplicate key hits at the same timestamp.
    unique = []
    seen = set()
    for item in sorted(notes, key=lambda item: (item["time"], item["key"])):
        key = (item["time"], item["key"])
        if key not in seen:
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
        "isComposed": True,
        "source": str(midi_path),
        "conversionBaseMidiNote": base,
        "octaveFoldedToSkyRange": fold_octaves,
        "keptAllMidiVelocities": keep_all_notes,
        "chordAwareMapping": chord_aware,
        "mergedOnsetsWithinMs": merge_window_ms,
        "playerPressCompensationMs": compensate_press_ms,
        "songNotes": unique,
    }]
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(unique), base, Counter(item["key"] for item in unique).most_common(5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("midi")
    parser.add_argument("output")
    parser.add_argument("--song-name", default="心墙")
    parser.add_argument("--author", default="郭静")
    parser.add_argument("--transcribed-by", default="Codex MIDI 转换")
    parser.add_argument("--bpm", type=int, default=120)
    parser.add_argument("--base-midi-note", type=int)
    parser.add_argument("--fold-octaves", action="store_true")
    parser.add_argument("--keep-all-notes", action="store_true")
    parser.add_argument("--chord-aware", action="store_true")
    parser.add_argument("--merge-window-ms", type=int, default=0)
    parser.add_argument("--compensate-press-ms", type=int, default=0)
    args = parser.parse_args()
    count, base, common = convert(
        Path(args.midi),
        Path(args.output),
        args.song_name,
        args.author,
        args.transcribed_by,
        args.bpm,
        args.base_midi_note,
        args.fold_octaves,
        args.keep_all_notes,
        args.chord_aware,
        args.merge_window_ms,
        args.compensate_press_ms,
    )
    print(f"wrote {count} notes, base={base}, common={common}")


if __name__ == "__main__":
    main()
