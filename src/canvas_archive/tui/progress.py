"""Progress display.

Two implementations behind one interface: a `rich` live display for terminals, and a
quiet line-based one for pipes, CI, and log files. The archiver only ever calls the
interface, so it never has to care which is active.
"""

from __future__ import annotations

import sys
import time


def human(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


class NullProgress:
    """No-op sink, used when output is redirected."""

    def start_courses(self, total: int) -> None: ...
    def start_course(self, name: str, index: int, total: int) -> None: ...
    def finish_course(self) -> None: ...
    def start_file(self, name: str, size: int | None) -> None: ...
    def advance_bytes(self, count: int) -> None: ...
    def finish_file(self, failed: bool = False) -> None: ...
    def start_retry(self, count: int) -> None: ...
    def note(self, message: str, seconds: float) -> None: ...
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class PlainProgress(NullProgress):
    """One line per course. Safe to pipe into a file."""

    def __init__(self) -> None:
        self._files = 0
        self._bytes = 0

    def start_course(self, name: str, index: int, total: int) -> None:
        print(f"[{index}/{total}] {name}", flush=True)

    def advance_bytes(self, count: int) -> None:
        self._bytes += count

    def finish_file(self, failed: bool = False) -> None:
        if not failed:
            self._files += 1

    def start_retry(self, count: int) -> None:
        print(f"Retrying {count} file(s) that failed...", flush=True)

    def note(self, message: str, seconds: float) -> None:
        print(f"  {message} - waiting {seconds:.0f}s", flush=True)


class RichProgress(NullProgress):
    """Live display: overall course bar, byte bar, and the current file.

    The state line matters as much as the bars. A rate-limited run is slow for a good
    reason, and saying so is what stops someone killing it thinking it has hung.
    """

    def __init__(self) -> None:
        from rich.console import Console
        from rich.progress import (
            BarColumn,
            DownloadColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeRemainingColumn,
            TransferSpeedColumn,
        )

        self.console = Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=None),
            TextColumn("{task.percentage:>3.0f}%"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            transient=False,
        )
        self._courses = None
        self._bytes = None
        self._status_until = 0.0

    def __enter__(self):
        self._progress.start()
        return self

    def __exit__(self, *exc):
        self._progress.stop()
        return False

    def start_courses(self, total: int) -> None:
        self._courses = self._progress.add_task("Courses", total=total)
        self._bytes = self._progress.add_task("Downloading", total=None)

    def start_course(self, name: str, index: int, total: int) -> None:
        short = name if len(name) <= 40 else name[:37] + "..."
        self._progress.update(self._courses, description=f"{short}")

    def finish_course(self) -> None:
        if self._courses is not None:
            self._progress.advance(self._courses)

    def start_file(self, name: str, size: int | None) -> None:
        if time.time() < self._status_until:
            return
        short = name if len(name) <= 46 else name[:43] + "..."
        self._progress.update(self._bytes, description=short)

    def advance_bytes(self, count: int) -> None:
        if self._bytes is not None:
            self._progress.advance(self._bytes, count)

    def start_retry(self, count: int) -> None:
        self.console.print(
            f"[yellow]Retrying {count} file(s) that didn't make it the first time...[/yellow]"
        )

    def note(self, message: str, seconds: float) -> None:
        # Hold the message visible so it isn't overwritten by the next filename.
        self._status_until = time.time() + seconds
        if self._bytes is not None:
            self._progress.update(
                self._bytes, description=f"[yellow]{message} - waiting {seconds:.0f}s[/yellow]"
            )


def make_progress(force_plain: bool = False):
    """Pick a display. A non-TTY must never emit ANSI control codes."""
    if force_plain or not sys.stdout.isatty():
        return PlainProgress()
    try:
        return RichProgress()
    except Exception:
        return PlainProgress()
