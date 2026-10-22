"""Which lesson `nety watch` waits for, and when."""

import datetime as dt


def window(lesson, day, buffer):
    """(start, end) datetimes during which to poll: from `buffer` before the listed start."""
    midnight = dt.datetime.combine(day, dt.time())
    return (midnight + dt.timedelta(minutes=lesson.start - buffer),
            midnight + dt.timedelta(minutes=lesson.end))


def pick_lesson(lessons, now):
    """The earliest unsigned lesson that has not ended, or None."""
    timed = [lesson for lesson in lessons if lesson.start is not None and lesson.end is not None]
    seen = set()
    for lesson in sorted(timed, key=lambda lesson: lesson.start):
        # A lesson may be listed twice (one entry per resource).
        if lesson.code in seen or lesson.signed:
            continue
        seen.add(lesson.code)
        if window(lesson, now.date(), 0)[1] > now:
            return lesson
    return None


def wait_seconds(now, until, interval):
    """Seconds to sleep before the next poll: `interval`, cut short at `until`."""
    return max(0.0, min(interval, (until - now).total_seconds()))
