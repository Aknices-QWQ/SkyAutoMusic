"""Estimate large-scale sections from symbolic Sky/MIDI note onsets.

Uses self-similarity and novelty (Foote, 2000), as documented by MSAF and
libfmp/FMP chapter 4. This is an independent implementation for symbolic
notes, not their audio models. A/B describe similar material; a chorus cannot
be established from note density alone. All inferred labels are estimates.
"""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re

import numpy as np


_KEY = re.compile(r"^[12]Key(\d{1,2})$")
_MAJOR = np.array([0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24])


@dataclass(frozen=True)
class SongSection:
    name: str
    start_ms: int
    end_ms: int
    estimated: bool = True

    def to_dict(self):
        return asdict(self)


def note_groups(notes):
    groups = defaultdict(set)
    for note in notes:
        if not isinstance(note, dict):
            continue
        try:
            raw_time = float(note["time"])
            match = _KEY.fullmatch(str(note["key"]))
            if not math.isfinite(raw_time) or raw_time < 0 or not match:
                continue
            pitch = int(match[1])
            if pitch < 15:
                groups[int(raw_time)].add(pitch)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    return [(time, tuple(sorted(keys))) for time, keys in sorted(groups.items())]


def score_end_ms(groups):
    if not groups:
        return 0
    gaps = np.diff([t for t, _ in groups])
    tail = int(np.clip(np.median(gaps) if len(gaps) else 500, 100, 2000))
    return groups[-1][0] + tail


def score_fingerprint(notes):
    data = json.dumps(note_groups(notes), separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def validate_sections(rows, groups):
    """Accept non-overlapping [start, end) ranges containing playable notes."""
    if not isinstance(rows, list) or not groups:
        raise ValueError("请选择有音符的曲谱")
    times = [t for t, _ in groups]
    limit = score_end_ms(groups)
    result = []
    for row in rows:
        try:
            name = str(row["name"]).strip()
            start, end = float(row["start_ms"]), float(row["end_ms"])
            if not all(math.isfinite(v) and v.is_integer() for v in (start, end)):
                raise ValueError
            start, end = int(start), int(end)
            if not name or not 0 <= start < end <= limit:
                raise ValueError
            if bisect_left(times, start) >= bisect_left(times, end):
                raise ValueError
            result.append(SongSection(name[:80], start, end, bool(row.get("estimated", False))))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("段落名称不能为空，起止时间须在曲谱范围内，且至少包含一组音符") from None
    result.sort(key=lambda section: section.start_ms)
    if any(a.end_ms > b.start_ms for a, b in zip(result, result[1:])):
        raise ValueError("段落不能重叠；上一段的结束时间可以等于下一段的开始时间")
    return result


def _frame_features(groups, frame_ms, first, size):
    # Absolute pitch retains register; chroma retains harmonic similarities.
    features = np.zeros((size, 29), dtype=float)
    activity = np.zeros(size)
    for time, pitches in groups:
        index = min(size - 1, int((time - first) / frame_ms))
        for pitch in pitches:
            features[index, pitch] += 1
            features[index, 15 + _MAJOR[pitch] % 12] += .6
        features[index, 27] += max(pitches) / 14
        features[index, 28] += len(pitches) / 3
        activity[index] += 1
    features /= np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-10)
    return features, activity


def _novelty(features, activity, frame_ms):
    similarity = features @ features.T
    size = len(features)
    novelty = np.zeros(size)
    # Analyze at multiple scales; these windows are not fixed musical sections.
    for seconds in (4, 8):
        width = max(2, round(seconds * 1000 / frame_ms))
        for i in range(width, size - width):
            left = similarity[i-width:i, i-width:i].mean()
            right = similarity[i:i+width, i:i+width].mean()
            across = similarity[i-width:i, i:i+width].mean()
            density_a, density_b = activity[i-width:i].mean(), activity[i:i+width].mean()
            density_change = abs(density_a - density_b) / max(1, density_a + density_b)
            novelty[i] = max(novelty[i], .5 * (left + right) - across + .15 * density_change)
    return novelty


def _descriptor(groups):
    pitches = np.array([max(keys) for _, keys in groups], dtype=float)
    sampled = np.interp(np.linspace(0, len(pitches)-1, 32), np.arange(len(pitches)), pitches) / 14
    hist = np.bincount([key for _, keys in groups for key in keys], minlength=15).astype(float)
    hist /= max(1, hist.sum())
    return sampled, hist


def estimate_sections(notes):
    groups = note_groups(notes)
    if not groups:
        return []
    first, end = groups[0][0], score_end_ms(groups)
    duration = end - first
    if len(groups) < 24 or duration < 16000:
        return [SongSection("全曲", first, end)]
    times = [t for t, _ in groups]
    # Bound the similarity matrix for long scores (at most 600 x 600).
    frame_ms = max(1000, math.ceil(duration / 600))
    size = math.ceil(duration / frame_ms)
    features, activity = _frame_features(groups, frame_ms, first, size)
    novelty = _novelty(features, activity, frame_ms)
    minimum = max(6000, min(16000, duration * .055))
    threshold = max(.10, float(np.median(novelty) + 1.5 * np.std(novelty)))
    candidates = []
    for i in range(1, size - 1):
        if novelty[i] >= threshold and novelty[i] >= novelty[i-1] and novelty[i] > novelty[i+1]:
            index = bisect_left(times, first + i * frame_ms)
            if index < len(times):
                candidates.append((float(novelty[i]), times[index]))
    typical_gap = float(np.median(np.diff(times)))
    for i in range(1, len(groups)):
        if times[i] - times[i-1] >= max(2000, typical_gap * 3.5):
            candidates.append((1.0, times[i]))
    boundaries = [first, end]
    for strength, time in sorted(candidates, reverse=True):
        if min(abs(time - bound) for bound in boundaries) < minimum:
            continue
        bounds = sorted(boundaries)
        lo = max(b for b in bounds if b < time)
        hi = min(b for b in bounds if b > time)
        if min(bisect_left(times, time) - bisect_left(times, lo),
               bisect_left(times, hi) - bisect_left(times, time)) < 8:
            continue
        boundaries.append(time)
        if len(boundaries) >= 18:
            break
    boundaries.sort()
    if len(boundaries) == 2:
        return [SongSection("全曲", first, end)]
    prototypes, labels, densities = [], [], []
    for lo, hi in zip(boundaries, boundaries[1:]):
        excerpt = groups[bisect_left(times, lo):bisect_left(times, hi)]
        melody, hist = _descriptor(excerpt)
        matches = [(.6 * np.mean(np.abs(melody - old_melody)) +
                    .4 * np.sum(np.abs(hist - old_hist)), index)
                   for index, (old_melody, old_hist) in enumerate(prototypes)]
        best = min(matches) if matches else (1, 0)
        if best[0] < .17:
            label = best[1]
        else:
            label = len(prototypes)
            prototypes.append((melody, hist))
        labels.append(label)
        densities.append(sum(len(keys) for _, keys in excerpt) / max(1, (hi-lo) / 1000))
    # Only a repeated, distinctly denser group gets a tentative climax label.
    climax = None
    baseline = float(np.median(densities))
    for label in set(labels):
        values = [density for tag, density in zip(labels, densities) if tag == label]
        if len(values) > 1 and len(set(labels)) > 1 and np.mean(values) > baseline * 1.3:
            if climax is None or np.mean(values) > climax[0]:
                climax = (float(np.mean(values)), label)
    result = []
    for i, (lo, hi) in enumerate(zip(boundaries, boundaries[1:])):
        label = labels[i]
        name = f"{chr(65 + label)} 段"
        occurrence = labels[:i+1].count(label)
        if occurrence > 1:
            name += f"（重复 {occurrence}）"
        if climax and label == climax[1]:
            name += " · 高潮候选"
        result.append(SongSection(name, lo, hi))
    return result
