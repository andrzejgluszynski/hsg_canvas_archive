"""Filesystem and path handling that behaves the same on all three platforms.

Windows is the awkward one: a 260-character limit that still bites in 2026, a console
that is not UTF-8 when redirected, and a path separator that is invalid inside a URL.
Each is handled once, here, rather than being rediscovered at every call site.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

# Leave room for a filename to be appended to a directory we are probing.
WINDOWS_SAFE_LEN = 240
LONG_PREFIX = "\\\\?\\"
# Worst-case relative path length seen in a real archive, used to warn early.
LONGEST_OBSERVED_RELATIVE = 210

IS_WINDOWS = os.name == "nt"


def as_url_path(path: str | Path) -> str:
    """Convert a filesystem path to a form usable in a link.

    `os.path.relpath` yields backslashes on Windows, and a backslash in an href is not
    a separator -- it is a literal character. Links must always use forward slashes,
    whatever platform generated them.
    """
    return PurePosixPath(str(path).replace("\\", "/")).as_posix()


def fs_path(path: Path) -> Path:
    """Return a path safe to open, even past the Windows 260-character limit.

    The `\\\\?\\` prefix opts into the Unicode APIs that ignore MAX_PATH. It needs no
    registry change and no manifest, so it works on a locked-down university laptop
    where enabling LongPathsEnabled is not an option.
    """
    if not IS_WINDOWS:
        return path
    text = os.path.abspath(str(path))
    if len(text) < WINDOWS_SAFE_LEN or text.startswith(LONG_PREFIX):
        return path
    return Path(LONG_PREFIX + text)


def configure_console() -> None:
    """Make stdout/stderr tolerate any character the archive contains.

    When output is redirected on Windows, Python encodes with the legacy code page, so
    printing a course name containing an em dash or CJK raises UnicodeEncodeError and
    kills the run. Nothing about archiving should depend on the console's code page.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def check_path_budget(root: Path) -> str | None:
    """Warn *before* a long run if deep paths will fail.

    Failing at file 3,000 of 3,500 with a cryptic OS error is the worst possible way to
    discover this, so it is checked up front and reported in plain language.
    """
    if not IS_WINDOWS:
        return None
    base = len(os.path.abspath(str(root)))
    # Measured against a real 25-course archive: the longest path *below* the archive
    # root was 207 characters (a submission attachment with a long assignment name and
    # a long filename). Anything under that budget is comfortable.
    if base + LONGEST_OBSERVED_RELATIVE < 260:
        return None
    return (
        f"Your archive folder path is {base} characters long, which leaves little room "
        f"for long course and file names on Windows.\n"
        f"  Long paths are handled automatically, but a shorter folder is safer:\n"
        f"      canvas-archive -o C:\\CanvasArchive"
    )
