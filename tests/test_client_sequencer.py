from j5_macro.client import EventSequencer
from j5_macro.client.stderr import StderrTail
from j5_macro.protocol import Event


def ev(seq: int, run_id: str | None = None) -> Event:
    return Event(type="log", seq=seq, ts="2026-10-12T00:00:00.000Z", t_ms=0, run_id=run_id)


def test_streams_are_tracked_independently() -> None:
    seq = EventSequencer()
    assert seq.gap_start(ev(1)) is None
    assert seq.accept(ev(1))
    assert seq.gap_start(ev(1, "r-1")) is None
    assert seq.accept(ev(1, "r-1"))
    assert seq.last(None) == 1 and seq.last("r-1") == 1


def test_gap_and_duplicate_detection() -> None:
    seq = EventSequencer()
    assert seq.gap_start(ev(3, "r")) == 1
    assert seq.accept(ev(3, "r"))
    assert seq.is_duplicate(ev(2, "r")) and seq.is_duplicate(ev(3, "r"))
    assert not seq.accept(ev(3, "r"))
    assert seq.gap_start(ev(4, "r")) is None
    assert seq.gap_start(ev(7, "r")) == 4


def test_stderr_tail_handles_partial_lines_and_bad_bytes() -> None:
    tail = StderrTail(max_lines=3)
    tail.feed(b"one\r\ntw")
    assert tail.lines == ["one", "tw"]
    tail.feed(b"o\nthree\nfour\n\xff")
    assert tail.lines == ["three", "four", "�"]
