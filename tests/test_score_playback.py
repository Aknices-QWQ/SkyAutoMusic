import threading

from score_playback import PlaybackSession


def make_session(**kwargs):
    return PlaybackSession((0, 500, 1000, 1500),
                           {t: [f"1Key{i}"] for i, t in enumerate((0, 500, 1000, 1500))},
                           1, 3, 1000, **kwargs)


def run(session, emit, play=lambda keys: None, release=lambda keys: None, factor=lambda: 0):
    session.run(emit, play, release, lambda key: key, factor, lambda: 0)


def test_section_end_excludes_next_sections_note():
    events = []
    session = make_session(preview=True)
    run(session, lambda kind, payload: events.append((kind, payload)))
    assert [p["index"] for k, p in events if k == "progress"] == [1, 2]
    assert [p for k, p in events if k == "notes"] == [["1Key1"], ["1Key2"]]
    assert events[-1] == ("finished", False)


def test_loop_restarts_at_section_start_and_stop_cancels():
    session = make_session(preview=True, loop=True)
    seen = []
    def emit(kind, payload):
        if kind == "progress":
            seen.append(payload["index"])
            if len(seen) == 5:
                session.stop.set()
    run(session, emit)
    assert seen == [1, 2, 1, 2, 1]


def test_countdown_can_be_cancelled_without_sending_keys_and_immediate_has_none():
    session = make_session()
    status, played = [], []
    def emit(kind, payload):
        if kind == "status":
            status.append(payload)
            session.stop.set()
    run(session, emit, played.append)
    assert len(status) == 1 and status[0].startswith("3")
    assert played == []
    events = []
    immediate = make_session(countdown=0)
    run(immediate, lambda kind, payload: events.append((kind, payload)), played.append)
    assert not any(k == "status" for k, _ in events)
    assert played == [["1Key1"], ["1Key2"]]


def test_seek_is_clamped_to_section_and_interrupts_a_long_wait():
    session = make_session(preview=True)
    session.seek(999)
    indices = []
    run(session, lambda k, p: indices.append(p["index"]) if k == "progress" else None)
    assert indices == [2]
    session = make_session(preview=True)
    session.seek(-10)
    assert session.seek_index == 1


def test_stop_interrupts_wait_and_releases_keys_on_error():
    session = make_session(countdown=0)
    entered = threading.Event()
    released = []
    def play(keys):
        entered.set()
        raise RuntimeError("failed")
    events = []
    run(session, lambda k, p: events.append((k, p)), play, released.append)
    assert entered.is_set()
    assert released == [["1Key1"]]
    assert ("error", "failed") in events
    assert events[-1] == ("finished", True)


def test_one_note_loop_waits_and_stops_promptly():
    session = PlaybackSession((0,), {0: ["1Key0"]}, 0, 1, 1000,
                              preview=True, loop=True)
    entered, events = threading.Event(), []
    def emit(kind, payload):
        events.append((kind, payload))
        if kind == "progress":
            entered.set()
    worker = threading.Thread(target=lambda: run(session, emit, factor=lambda: 1))
    worker.start()
    assert entered.wait(1)
    session.stop.set()
    worker.join(1)
    assert not worker.is_alive()
    assert len([k for k, _ in events if k == "progress"]) == 1
