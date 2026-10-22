import datetime as dt

from nety.client import Lesson
from nety.watch import pick_lesson, wait_seconds, window

DAY = dt.date(2026, 10, 19)


def at(hour, minute=0):
    return dt.datetime.combine(DAY, dt.time(hour, minute))


MORNING = Lesson(1, "Maths", start=8 * 60 + 30, end=12 * 60 + 30)
AFTERNOON = Lesson(2, "Info", start=14 * 60, end=18 * 60)


def test_window_starts_buffer_early():
    assert window(AFTERNOON, DAY, 45) == (at(13, 15), at(18))


def test_pick_skips_signed_and_ended():
    signed = Lesson(1, start=MORNING.start, end=MORNING.end, signed=True)
    assert pick_lesson([signed, AFTERNOON], at(9)) == AFTERNOON
    assert pick_lesson([MORNING, AFTERNOON], at(13)) == AFTERNOON


def test_pick_prefers_earliest_while_windows_overlap():
    # 12h00: the morning lesson still runs while the afternoon one is being watched for.
    assert pick_lesson([AFTERNOON, MORNING], at(12)) == MORNING


def test_pick_skips_lessons_without_times():
    assert pick_lesson([Lesson(9), AFTERNOON], at(9)) == AFTERNOON


def test_pick_dedupes_by_code():
    twin = Lesson(1, start=MORNING.start, end=MORNING.end)
    assert pick_lesson([MORNING, twin], at(9)) == MORNING


def test_pick_none_after_last_lesson():
    assert pick_lesson([MORNING, AFTERNOON], at(18)) is None
    assert pick_lesson([], at(9)) is None


def test_wait_seconds_cut_at_deadline():
    assert wait_seconds(at(17, 59), at(18), 120) == 60
    assert wait_seconds(at(17), at(18), 120) == 120
    assert wait_seconds(at(18, 1), at(18), 120) == 0
