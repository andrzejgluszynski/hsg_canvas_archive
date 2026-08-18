"""Progress display.

Three implementations behind one interface: a `rich` live display with one line per
in-flight course, a quiet line-based one for pipes and log files, and a no-op.

The interface is handle-based rather than sequential because several courses are
archived at once -- there is no single "current" course to talk about.
"""

from __future__ import annotations

import re
import sys
import threading


def human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


class NullProgress:
    """No-op sink, used when output is redirected or progress is disabled."""

    def start_courses(self, total: int) -> None: ...
    def add_course(self, name: str) -> object:
        return None

    def set_step(self, handle: object, step: str) -> None: ...
    def add_files(self, handle: object, count: int) -> None: ...
    def file_done(self, handle: object) -> None: ...
    def finish_course(self, handle: object) -> None: ...
    def add_bytes_total(self, count: int) -> None: ...
    def advance_bytes(self, count: int) -> None: ...
    def start_retry(self, count: int) -> None: ...
    def note(self, message: str, seconds: float) -> None: ...
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PlainProgress(NullProgress):
    """One line per course as it completes. Safe to pipe into a file."""

    def __init__(self, total: int = 0) -> None:
        self._total = 0
        self._done = 0
        self._lock = threading.Lock()

    def start_courses(self, total: int) -> None:
        self._total = total
        print(f"Archiving {total} courses...", flush=True)

    def add_course(self, name: str) -> object:
        return name

    def finish_course(self, handle: object) -> None:
        with self._lock:
            self._done += 1
            print(f"[{self._done}/{self._total}] {handle}", flush=True)

    def start_retry(self, count: int) -> None:
        print(f"Retrying {count} file(s) that failed...", flush=True)

    def note(self, message: str, seconds: float) -> None:
        print(f"  {message} - waiting {seconds:.0f}s", flush=True)


class RichProgress(NullProgress):
    """Live display: an overall bar, a byte total, and a line per active course.

    Finished courses are removed so the display stays the height of the work actually
    in flight, rather than growing to the length of the whole degree.
    """

    def __init__(self, console=None) -> None:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
            TransferSpeedColumn,
        )

        self.console = console or Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=28),
            TextColumn("{task.fields[note]}"),
            TimeElapsedColumn(),
            console=self.console,
        )
        # Bytes get their own row so the transfer rate is readable at a glance.
        self._bytes_progress = Progress(
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=28),
            DownloadColumn(),
            TransferSpeedColumn(),
            console=self.console,
        )
        self._overall = None
        self._bytes = None
        self._live = None
        self._lock = threading.Lock()

    def __enter__(self):
        from rich.console import Group
        from rich.live import Live

        self._live = Live(
            Group(self._progress, self._bytes_progress),
            console=self.console,
            refresh_per_second=8,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *exc):
        if self._live:
            self._live.__exit__(*exc)
        return False

    def start_courses(self, total: int) -> None:
        self._overall = self._progress.add_task("All courses", total=total, note="")
        self._bytes = self._bytes_progress.add_task("Downloaded", total=0)

    def add_course(self, name: str) -> object:
        short = name if len(name) <= 34 else name[:31] + "..."
        return self._progress.add_task(short, total=None, note="starting")

    def set_step(self, handle: object, step: str) -> None:
        if handle is not None:
            self._progress.update(handle, note=step)

    def add_files(self, handle: object, count: int) -> None:
        if handle is not None and count:
            self._progress.update(handle, total=count, completed=0, note=f"0/{count} files")

    def file_done(self, handle: object) -> None:
        if handle is None:
            return
        with self._lock:
            self._progress.advance(handle)
            task = next((t for t in self._progress.tasks if t.id == handle), None)
            if task and task.total:
                self._progress.update(handle, note=f"{int(task.completed)}/{int(task.total)} files")

    def finish_course(self, handle: object) -> None:
        """Leave the finished course on screen, completed and in green.

        Removing it kept the display short, but it also erased the evidence that the
        work happened -- which is the opposite of reassuring on a long run.
        """
        if handle is not None:
            task = next((t for t in self._progress.tasks if t.id == handle), None)
            total = (task.total if task and task.total else 1) or 1
            label = task.description if task else ""
            # Strip any styling from an earlier update before re-wrapping it.
            plain = re.sub(r"\[/?[a-z ]+\]", "", label)
            self._progress.update(
                handle,
                total=total,
                completed=total,
                description=f"[green]{plain}[/green]",
                note="[green]done[/green]",
            )
        if self._overall is not None:
            self._progress.advance(self._overall)

    def add_bytes_total(self, count: int) -> None:
        """Grow the expected total as file sizes are discovered.

        The total is not knowable up front without a full pre-scan, so the bar learns
        it as it goes. That is better than showing `?` for the whole run.
        """
        if self._bytes is None or not count:
            return
        with self._lock:
            task = next((t for t in self._bytes_progress.tasks if t.id == self._bytes), None)
            current = (task.total or 0) if task else 0
            self._bytes_progress.update(self._bytes, total=current + count)

    def advance_bytes(self, count: int) -> None:
        if self._bytes is not None and count:
            self._bytes_progress.advance(self._bytes, count)

    def start_retry(self, count: int) -> None:
        self.console.print(f"[yellow]Retrying {count} file(s) that didn't make it...[/yellow]")

    def note(self, message: str, seconds: float) -> None:
        if self._overall is not None:
            self._progress.update(
                self._overall, note=f"[yellow]{message}, waiting {seconds:.0f}s[/yellow]"
            )

    def refresh(self) -> None:
        """Force a repaint. The live display is otherwise on a refresh timer."""
        if self._live is not None:
            self._live.refresh()


def make_progress(force_plain: bool = False):
    """Pick a display. A non-TTY must never emit ANSI control codes."""
    if force_plain or not sys.stdout.isatty():
        return PlainProgress()
    try:
        return RichProgress()
    except Exception:
        return PlainProgress()
