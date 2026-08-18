import pytest

from canvas_archive.sanitize import DEFAULT_MAX_BYTES, safe_component, unique_component


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),          # reserved with an extension, case-insensitive
        ("LPT9", "_LPT9"),
        ("nul.tar.gz", "_nul.tar.gz"),
        ("  padded  ", "padded"),
        ("trailing...", "trailing"),
        ("trailing   ", "trailing"),
        ("a/b\\c:d*e?f", "a-b-c-d-e-f"),
        ("", "untitled"),
        (None, "untitled"),
        ("...", "untitled"),
        ("normal name.pdf", "normal name.pdf"),
    ],
)
def test_safe_component(raw, expected):
    assert safe_component(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["Ökonomie & Führung", "文件名", "Привет мир", "café.pdf", "naïve"],
)
def test_non_ascii_is_preserved(raw):
    """Upstream's ASCII whitelist erased these entirely."""
    result = safe_component(raw)
    assert result != "untitled"
    assert any(ch.isalpha() for ch in result)


@pytest.mark.parametrize(
    "raw",
    ["x" * 500, "文" * 500, "CON", "", "a.b.c", "report.pdf", "  ..  ", "Ö" * 200],
)
def test_idempotent(raw):
    """Resume correctness depends on this: re-sanitising must be a no-op."""
    once = safe_component(raw)
    assert safe_component(once) == once


@pytest.mark.parametrize("raw", ["x" * 500, "文" * 500, "é" * 300])
def test_truncates_on_bytes_not_chars(raw):
    assert len(safe_component(raw).encode("utf-8")) <= DEFAULT_MAX_BYTES


def test_extension_survives_truncation():
    result = safe_component("y" * 400 + ".pdf")
    assert result.endswith(".pdf")
    assert len(result.encode("utf-8")) <= DEFAULT_MAX_BYTES


def test_no_illegal_characters_remain():
    result = safe_component('a<b>c:d"e/f\\g|h?i*j')
    assert not set(result) & set('<>:"/\\|?*')


def test_collisions_use_canvas_id_not_sequence():
    taken: set[str] = set()
    assert unique_component("notes.pdf", 11, taken) == "notes.pdf"
    assert unique_component("notes.pdf", 22, taken) == "notes-22.pdf"
    # Deterministic regardless of encounter order -- a sequence number would not be.
    other: set[str] = set()
    assert unique_component("notes.pdf", 99, other) == "notes.pdf"
    assert unique_component("notes.pdf", 22, other) == "notes-22.pdf"


def test_collision_detection_is_case_insensitive():
    taken: set[str] = set()
    unique_component("Notes.pdf", 1, taken)
    assert unique_component("notes.pdf", 2, taken) == "notes-2.pdf"
