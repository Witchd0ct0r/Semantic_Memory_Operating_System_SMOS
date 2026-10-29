from __future__ import annotations

import threading
import time

from smos.memory.rwlock import RWLock


def test_two_readers_hold_the_lock_together() -> None:
    lock, meet, errors = RWLock(), threading.Barrier(2, timeout=5), []

    def reader() -> None:
        try:
            with lock.read():
                meet.wait()  # both readers must be inside at once, or this times out
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []


def test_writer_waits_for_readers_and_blocks_new_ones() -> None:
    lock, events = RWLock(), []
    first_in, release_first = threading.Event(), threading.Event()

    def first_reader() -> None:
        with lock.read():
            events.append("first reader in")
            first_in.set()
            release_first.wait(5)
        events.append("first reader out")

    def writer() -> None:
        with lock.write():
            events.append("writer in")
            time.sleep(0.05)
        events.append("writer out")

    def late_reader() -> None:
        with lock.read():
            events.append("late reader in")

    t1 = threading.Thread(target=first_reader)
    t1.start()
    first_in.wait(5)
    t2 = threading.Thread(target=writer)
    t2.start()
    time.sleep(0.1)  # the writer is now waiting
    t3 = threading.Thread(target=late_reader)
    t3.start()
    time.sleep(0.1)
    assert events == ["first reader in"]  # neither the writer nor the late reader got in
    release_first.set()
    for t in (t1, t2, t3):
        t.join(5)
    assert events == ["first reader in", "first reader out", "writer in", "writer out", "late reader in"]


def test_write_is_exclusive() -> None:
    lock, inside, overlap = RWLock(), [0], []

    def writer() -> None:
        for _ in range(50):
            with lock.write():
                inside[0] += 1
                if inside[0] != 1:
                    overlap.append(inside[0])
                time.sleep(0.0005)
                inside[0] -= 1

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert overlap == []
