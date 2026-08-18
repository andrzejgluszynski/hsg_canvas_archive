"""Markdown rendering: the layer a human actually reads."""

import pytest

from canvas_archive.render.html2md import html_to_markdown as h
from canvas_archive.render.markdown import (
    announcements_md,
    archive_index,
    grades_md,
    modules_md,
    submission_md,
)


@pytest.mark.parametrize(
    "html,expected",
    [
        ("<p>Hello <strong>world</strong></p>", "Hello **world**"),
        ("<em>slanted</em>", "*slanted*"),
        ("<h3>Heading</h3>", "### Heading"),
        ('<a href="https://x">link</a>', "[link](https://x)"),
        ("<ul><li>a</li><li>b</li></ul>", "- a\n- b"),
        ("<ol><li>a</li><li>b</li></ol>", "1. a\n2. b"),
        ("<p>a&nbsp;&amp;&nbsp;b</p>", "a & b"),
        ("<p>&lt;escaped&gt;</p>", "<escaped>"),
        ("plain text", "plain text"),
        ("", ""),
        (None, ""),
    ],
)
def test_html_to_markdown(html, expected):
    assert h(html) == expected


def test_table_becomes_markdown_table():
    out = h("<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>")
    assert "| A | B |" in out and "| --- | --- |" in out and "| 1 | 2 |" in out


def test_pipes_in_cells_are_escaped():
    assert "\\|" in h("<table><tr><td>a|b</td></tr></table>")


def test_script_and_style_content_is_dropped():
    out = h("<p>keep</p><script>var x=1;</script><style>p{}</style>")
    assert "keep" in out and "var x" not in out and "p{}" not in out


def test_unknown_tags_keep_their_text():
    """Never silently lose content to a tag we didn't anticipate."""
    assert "important" in h("<marquee><custom-el>important</custom-el></marquee>")


def test_malformed_html_falls_back_instead_of_raising():
    assert "text" in h("<p>text<<<>>")


def test_nested_lists_indent():
    out = h("<ul><li>outer<ul><li>inner</li></ul></li></ul>")
    assert "- outer" in out and "  - inner" in out


def test_submission_md_includes_score_feedback_and_rubric():
    submission = {
        "score": 17.5,
        "grade": "A-",
        "submitted_at": "2026-03-01T09:00:00Z",
        "late": True,
        "assignment": {
            "name": "Case study",
            "points_possible": 20,
            "rubric": [{"id": "c1", "description": "Analysis"}],
        },
        "submission_comments": [
            {
                "author": {"display_name": "Dr Legge"},
                "created_at": "2026-03-02T10:00:00Z",
                "comment": "Well argued.",
            }
        ],
        "rubric_assessment": {"c1": {"points": 9, "comments": "Clear"}},
        "attachments": [{"display_name": "case.pdf"}],
    }
    out = submission_md(submission)
    assert "# Case study" in out
    assert "**Score:** 17.5 / 20" in out
    assert "**Late**" in out
    assert "Dr Legge" in out and "Well argued." in out
    assert "| Analysis | 9 | Clear |" in out  # rubric id resolved to its label
    assert "[case.pdf](./case.pdf)" in out


def test_submission_md_survives_missing_fields():
    assert submission_md({}).startswith("# Submission")


def test_grades_md_builds_a_table():
    out = grades_md(
        "Finance",
        {"current_score": 68.5, "current_grade": "4.75", "final_score": 68.5},
        [
            {
                "score": 18,
                "graded_at": "2026-03-01T00:00:00Z",
                "assignment": {"name": "Essay", "points_possible": 20},
            }
        ],
    )
    assert "**Overall: 4.75 / 68.5%**" in out
    assert "| Essay | 18 | 20 |" in out.replace(" 01 Mar 2026 |", "")


def test_announcements_are_newest_first():
    out = announcements_md(
        "C",
        [
            {"title": "Older", "posted_at": "2026-01-01T00:00:00Z", "message": "<p>a</p>"},
            {"title": "Newer", "posted_at": "2026-06-01T00:00:00Z", "message": "<p>b</p>"},
        ],
    )
    assert out.index("Newer") < out.index("Older")


def test_modules_md_renders_structure_with_links():
    out = modules_md(
        "C",
        [
            {
                "name": "Week 1",
                "items": [
                    {"type": "SubHeader", "title": "Readings"},
                    {"type": "File", "title": "Slides.pdf"},
                    {"type": "ExternalUrl", "title": "Video", "external_url": "https://v/1"},
                ],
            }
        ],
    )
    assert "## Week 1" in out
    assert "**Readings**" in out
    assert "*file* · Slides.pdf" in out  # no local copy -> plain text
    assert "[Video](https://v/1)" in out


def test_archive_index_lists_courses_with_grades():
    out = archive_index(
        {"name": "Student"},
        "https://canvas.test",
        [{"name": "Finance", "folder": "Finance__F__1", "grade": "4.75 / 68.5%"}],
    )
    assert "**Student**" in out
    assert "| Finance | 4.75 / 68.5% |" in out
    assert "Finance__F__1/README.md" in out


def test_attachment_links_are_url_encoded():
    """Filenames with spaces would otherwise produce links no viewer can follow."""
    out = submission_md(
        {
            "assignment": {"name": "A"},
            "attachments": [{"display_name": "My Essay (final) v2.pdf"}],
        }
    )
    assert "(./My%20Essay%20%28final%29%20v2.pdf)" in out
    assert "(./My Essay" not in out


def test_course_folder_links_are_encoded():
    out = archive_index(
        {"name": "S"},
        "h",
        [
            {
                "name": "08 Corporate Finance I",
                "folder": "08 Corporate Finance I__X__835",
                "grade": "",
            }
        ],
    )
    assert "08%20Corporate%20Finance%20I__X__835/README.md" in out


def test_no_triple_blank_lines_anywhere():
    """Sections are appended independently; the seams must still read cleanly."""
    out = submission_md(
        {
            "assignment": {"name": "A", "description": "<p>d</p>"},
            "body": "<p>b</p>",
            "attachments": [{"display_name": "f.pdf"}],
            "submission_comments": [{"author": {"display_name": "X"}, "comment": "c"}],
        }
    )
    assert "\n\n\n" not in out
    assert out.endswith("\n") and not out.endswith("\n\n")
