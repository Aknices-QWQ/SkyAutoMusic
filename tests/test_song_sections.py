from bisect import bisect_left

import pytest

from song_sections import (
    estimate_sections, note_groups, score_end_ms, score_fingerprint, validate_sections,
)


def repeated_score():
    notes = []
    for offset, pitches in ((3000, range(7)), (27000, range(8, 15)), (51000, range(7))):
        pitches = list(pitches)
        for i in range(48):
            notes.append({"time": offset+i*500, "key": f"1Key{pitches[i % len(pitches)]}"})
    return notes


def test_repeated_a_b_a_material_is_grouped_and_boundaries_cover_all_notes():
    notes = repeated_score()
    sections = estimate_sections(notes)
    assert [section.name.split(" ")[0] for section in sections] == ["A", "B", "A"]
    assert [section.start_ms for section in sections] == [3000, 27000, 51000]
    assert all(section.estimated for section in sections)
    groups = note_groups(notes)
    times = [t for t, _ in groups]
    covered = [index for section in sections
               for index in range(bisect_left(times, section.start_ms), bisect_left(times, section.end_ms))]
    assert covered == list(range(len(times)))
    assert sections[-1].end_ms > times[-1]


def test_bad_notes_and_short_scores_do_not_invent_song_form():
    notes = [None, {}, {"time": "nan", "key": "1Key0"},
             {"time": -1, "key": "1Key1"}, {"time": 0, "key": "1Key99"},
             {"time": 0, "key": "junkKey1"}, {"time": 1000, "key": "1Key1"},
             {"time": 1000, "key": "2Key1"}]
    groups = note_groups(notes)
    assert groups == [(1000, (1,))]
    assert [(s.name, s.start_ms, s.end_ms) for s in estimate_sections(notes)] == [("全曲", 1000, 1500)]
    assert estimate_sections([]) == []


def test_long_gap_produces_a_section_boundary_without_using_header_bpm():
    notes = [{"time": i*500, "key": f"1Key{i % 7}"} for i in range(48)]
    notes += [{"time": 30000+i*500, "key": f"1Key{i % 7}"} for i in range(48)]
    sections = estimate_sections(notes)
    assert len(sections) == 2
    assert sections[1].start_ms == 30000
    assert sections[0].name.startswith("A") and sections[1].name.startswith("A")


def test_fingerprint_is_stable_for_order_and_duplicate_hands_but_not_score_edits():
    notes = repeated_score()
    assert score_fingerprint(notes) == score_fingerprint(list(reversed(notes)) + notes)
    changed = [dict(note) for note in notes]
    changed[0]["key"] = "1Key10"
    assert score_fingerprint(notes) != score_fingerprint(changed)


def test_manual_ranges_are_half_open_and_cannot_overlap_or_be_empty():
    groups = note_groups(repeated_score())
    rows = [{"name": "主歌", "start_ms": 3000, "end_ms": 27000},
            {"name": "副歌", "start_ms": 27000, "end_ms": score_end_ms(groups)}]
    sections = validate_sections(rows, groups)
    assert not any(s.estimated for s in sections)
    for bad in (
        [{"name": "", "start_ms": 3000, "end_ms": 6000}],
        [{"name": "空", "start_ms": 0, "end_ms": 500}],
        [{"name": "坏时间", "start_ms": 3000, "end_ms": float("inf")}],
        [{"name": "小数毫秒", "start_ms": 3000.5, "end_ms": 6000}],
        [dict(rows[0], end_ms=28000), rows[1]],
    ):
        with pytest.raises(ValueError):
            validate_sections(bad, groups)
