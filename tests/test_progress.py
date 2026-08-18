"""Progress display behaviour."""

import pytest
from rich.console import Console

from canvas_archive.tui.progress import (
    NullProgress,
    PlainProgress,
    RichProgress,
    human,
    make_progress,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0.0 B"),
        (512, "512.0 B"),
        (1024, "1.0 KB"),
        (73_430_147, "70.0 MB"),
        (1_932_735_283, "1.8 GB"),
    ],
)
def test_human_sizes(value, expected):
    assert human(value) == expected


def test_non_tty_never_uses_ansi(monkeypatch):
    """ANSI escapes in a redirected log file are noise, not progress."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert isinstance(make_progress(force_plain=True), PlainProgress)


def _drive(progress):
    """Exercise the whole interface the archiver actually calls."""
    with progress:
        progress.start_courses(3)
        a = progress.add_course("Course A")
        b = progress.add_course("Course B")
        progress.set_step(a, "modules")
        progress.add_files(a, 4)
        for _ in range(4):
            progress.file_done(a)
        progress.advance_bytes(2048)
        progress.note("rate limited", 5)
        progress.start_retry(2)
        progress.finish_course(a)
        progress.finish_course(b)


def test_null_progress_accepts_everything():
    _drive(NullProgress())


def test_plain_progress_accepts_everything(capsys):
    _drive(PlainProgress())
    out = capsys.readouterr().out
    assert "Archiving 3 courses" in out
    assert "[1/3] Course A" in out and "[2/3] Course B" in out
    assert "\x1b[" not in out  # no ANSI


def test_rich_progress_shows_a_line_per_active_course():
    console = Console(force_terminal=True, width=90, record=True)
    progress = RichProgress(console=console)
    with progress:
        progress.start_courses(4)
        a = progress.add_course("09 Corporate Finance II")
        b = progress.add_course("12 Customer Centricity")
        progress.add_files(a, 41)
        for _ in range(17):
            progress.file_done(a)
        progress.set_step(b, "submissions")
        progress.advance_bytes(487_000_000)
        progress.refresh()
        text = console.export_text()

    assert "All courses" in text
    assert "09 Corporate Finance II" in text
    assert "12 Customer Centricity" in text
    assert "17/41 files" in text  # per-course file counter
    assert "submissions" in text  # per-course step label


def test_finished_courses_stay_on_screen_completed():
    """A finished course keeps its line, in green, with a full bar.

    Removing it kept the display short but erased the evidence that the work
    happened, which reads as unsettling rather than tidy on a long run.
    """
    console = Console(force_terminal=True, width=90, record=True)
    progress = RichProgress(console=console)
    with progress:
        progress.start_courses(2)
        a = progress.add_course("Finished Course")
        progress.add_files(a, 7)
        for _ in range(7):
            progress.file_done(a)
        progress.finish_course(a)

        task = next(t for t in progress._progress.tasks if t.id == a)
        assert task.completed == task.total  # bar reaches the end
        assert task.finished
        assert "green" in task.description  # and is styled as done
        assert "Finished Course" in task.description


def test_a_course_with_no_files_still_completes_its_bar():
    """Courses that only produce JSON have no file count to fill the bar with."""
    console = Console(force_terminal=True, width=90, record=True)
    progress = RichProgress(console=console)
    with progress:
        progress.start_courses(1)
        a = progress.add_course("JSON Only")  # total stays None
        progress.finish_course(a)
        task = next(t for t in progress._progress.tasks if t.id == a)
        assert task.total and task.completed == task.total


def test_overall_bar_advances_as_courses_finish():
    console = Console(force_terminal=True, width=90, record=True)
    progress = RichProgress(console=console)
    with progress:
        progress.start_courses(3)
        for _ in range(2):
            progress.finish_course(progress.add_course("C"))
        overall = next(t for t in progress._progress.tasks if t.description == "All courses")
        assert overall.completed == 2
        assert overall.total == 3


def test_long_course_names_are_truncated():
    console = Console(force_terminal=True, width=90, record=True)
    progress = RichProgress(console=console)
    with progress:
        progress.start_courses(1)
        progress.add_course("X" * 120)
        progress.refresh()
        text = console.export_text()
    assert "..." in text
    assert "X" * 60 not in text


def test_rich_falls_back_when_construction_fails(monkeypatch):
    import canvas_archive.tui.progress as mod

    class Boom(mod.RichProgress):
        def __init__(self, *a, **k):
            raise RuntimeError("no terminal")

    monkeypatch.setattr(mod, "RichProgress", Boom)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    assert isinstance(mod.make_progress(), PlainProgress)


# --- byte accounting ---------------------------------------------------------


class RecordingProgress(NullProgress):
    """Captures what the archiver reports, so the bar's inputs can be asserted."""

    def __init__(self):
        self.total = 0
        self.advanced = 0

    def add_bytes_total(self, count):
        self.total += count

    def advance_bytes(self, count):
        self.advanced += count


def test_rich_bytes_bar_learns_its_total():
    console = Console(force_terminal=True, width=90, record=True)
    progress = RichProgress(console=console)
    with progress:
        progress.start_courses(1)
        progress.add_bytes_total(1000)
        progress.add_bytes_total(500)
        progress.advance_bytes(1200)
        task = next(t for t in progress._bytes_progress.tasks if t.description == "Downloaded")
        assert task.total == 1500
        assert task.completed == 1200
        progress.refresh()
        text = console.export_text()
    # A real proportion, not "0/? bytes".
    assert "/?" not in text
