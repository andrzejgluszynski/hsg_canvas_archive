"""Windows-specific behaviour.

These run on any platform: the Windows-only paths are exercised by patching the
platform flag, because the bugs they guard against are invisible on macOS and Linux
and would otherwise only surface on a classmate's laptop.
"""

import os
from pathlib import Path

import pytest

from canvas_archive import paths
from canvas_archive.paths import LONG_PREFIX, as_url_path, check_path_budget, fs_path
from canvas_archive.sanitize import safe_component


@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(paths, "IS_WINDOWS", True)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../_media/x.png", "../_media/x.png"),
        (r"..\_media\x.png", "../_media/x.png"),
        (r"..\a/b\c.png", "../a/b/c.png"),
        ("plain.png", "plain.png"),
    ],
)
def test_links_always_use_forward_slashes(raw, expected):
    """A backslash in an href is a literal character, not a separator."""
    assert as_url_path(raw) == expected


def test_generated_image_links_have_no_backslashes(on_windows):
    """Regression: os.path.relpath output went straight into a Markdown link."""
    rel = os.path.join("..", "_media", "diagram.png")
    assert "\\" not in as_url_path(rel)


def test_fs_path_is_a_no_op_on_posix():
    p = Path("/tmp/short")
    assert fs_path(p) == p


def test_fs_path_leaves_short_windows_paths_alone(on_windows):
    assert LONG_PREFIX not in str(fs_path(Path("C:/short/file.txt")))


def test_fs_path_prefixes_long_windows_paths(on_windows, monkeypatch):
    monkeypatch.setattr(os.path, "abspath", lambda s: "C:\\" + "x" * 300)
    result = str(fs_path(Path("whatever")))
    assert result.startswith(LONG_PREFIX)


def test_fs_path_does_not_double_prefix(on_windows, monkeypatch):
    already = LONG_PREFIX + "C:\\" + "y" * 300
    monkeypatch.setattr(os.path, "abspath", lambda s: already)
    assert str(fs_path(Path(already))).count(LONG_PREFIX) == 1


def test_path_budget_silent_on_posix():
    assert check_path_budget(Path("/anything")) is None


def test_path_budget_warns_on_a_deep_windows_root(on_windows, monkeypatch):
    monkeypatch.setattr(os.path, "abspath", lambda s: "C:\\Users\\A Very Long Name\\" + "d" * 40)
    warning = check_path_budget(Path("x"))
    assert warning and "shorter folder" in warning


def test_path_budget_quiet_for_a_short_windows_root(on_windows, monkeypatch):
    monkeypatch.setattr(os.path, "abspath", lambda s: "C:\\CanvasArchive")
    assert check_path_budget(Path("x")) is None


@pytest.mark.parametrize(
    "name",
    ["CON", "con.txt", "PRN", "AUX", "NUL", "COM1", "LPT9", "nul.tar.gz", "Com3.pdf"],
)
def test_windows_reserved_device_names_are_escaped(name):
    """Opening a file named CON on Windows talks to the console, not the disk."""
    assert safe_component(name).startswith("_")


@pytest.mark.parametrize("name", ["trailing.", "trailing ", "trailing. ", "a..."])
def test_trailing_dots_and_spaces_removed(name):
    """Windows silently strips these, which would desync resume from what's on disk."""
    result = safe_component(name)
    assert not result.endswith((".", " "))


def test_configure_console_is_safe_to_call():
    from canvas_archive.paths import configure_console

    configure_console()  # must never raise, whatever the stream type


def test_configure_console_survives_a_stream_without_reconfigure(monkeypatch):
    import sys

    class Dumb:
        def write(self, *_):  # no reconfigure attribute at all
            pass

    monkeypatch.setattr(sys, "stdout", Dumb())
    from canvas_archive.paths import configure_console

    configure_console()
