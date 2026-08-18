"""The offline HTML viewer."""

import pytest

from canvas_archive.render.html import build_site, markdown_to_html, page


@pytest.mark.parametrize(
    "md,expected",
    [
        ("# Title", "<h1>Title</h1>"),
        ("### Deep", "<h3>Deep</h3>"),
        ("plain text", "<p>plain text</p>"),
        ("**bold**", "<p><strong>bold</strong></p>"),
        ("*slant*", "<p><em>slant</em></p>"),
        ("`code`", "<p><code>code</code></p>"),
        ("---", "<hr>"),
    ],
)
def test_basic_blocks(md, expected):
    assert expected in markdown_to_html(md)


def test_links_and_images():
    out = markdown_to_html("[label](./x.html) and ![alt](./y.png)")
    assert '<a href="./x.html">label</a>' in out
    assert '<img alt="alt" src="./y.png"' in out


def test_lists_nest():
    out = markdown_to_html("- outer\n  - inner\n- back")
    assert out.count("<ul>") == 2 and out.count("</ul>") == 2
    assert "<li>inner</li>" in out


def test_ordered_list():
    out = markdown_to_html("1. one\n2. two")
    assert "<ol>" in out and "<li>one</li>" in out


def test_table_renders_with_scroll_wrapper():
    out = markdown_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
    assert '<div class="tablewrap">' in out  # wide tables must not break the page
    assert "<th>A</th>" in out and "<td>1</td>" in out


def test_nested_blockquotes_for_reply_threads():
    out = markdown_to_html("> **Ann**\n> \n> Hi\n\n> > **Bob**\n> > \n> > Reply")
    assert out.count("<blockquote>") >= 3


def test_html_in_markdown_is_escaped():
    """Content came from the web; it must never inject markup into the viewer."""
    out = markdown_to_html("A <script>alert(1)</script> B")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_page_shell_is_self_contained():
    out = page("T", "<p>x</p>")
    assert "<style>" in out  # CSS inlined, never fetched
    assert "http://" not in out and "https://" not in out
    assert "<title>T</title>" in out


def test_page_escapes_its_title():
    assert "&lt;b&gt;" in page("<b>", "<p>x</p>")


def test_build_site_generates_pages_and_rewrites_links(tmp_path):
    (tmp_path / "README.md").write_text("# Archive\n\n[open](./courses/C__1/README.md)\n")
    course = tmp_path / "courses" / "C__1"
    course.mkdir(parents=True)
    (course / "README.md").write_text("# C\n\n[grades](./grades/grades.md)\n")
    (course / "grades").mkdir()
    (course / "grades" / "grades.md").write_text("# Grades\n\n| A |\n|---|\n| 1 |\n")

    count = build_site(tmp_path)

    assert count == 3
    assert (tmp_path / "index.html").exists()
    assert (course / "index.html").exists()
    assert (course / "grades" / "grades.html").exists()

    # README.md links become index.html; other .md become .html.
    assert 'href="./courses/C__1/index.html"' in (tmp_path / "index.html").read_text()
    assert 'href="./grades/grades.html"' in (course / "index.html").read_text()


def test_course_index_links_resolve(tmp_path):
    """A crumb pointing at a nonexistent courses/index.html is the easy mistake."""
    import re
    import urllib.parse

    (tmp_path / "README.md").write_text("# Archive\n")
    course = tmp_path / "courses" / "C__1"
    (course / "grades").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "grades" / "grades.md").write_text("# Grades\n")
    build_site(tmp_path)

    for html_file in tmp_path.rglob("*.html"):
        for href in re.findall(r'href="(\.{1,2}/[^"]+)"', html_file.read_text()):
            target = (html_file.parent / urllib.parse.unquote(href)).resolve()
            assert target.exists(), f"{html_file.name} -> {href}"


@pytest.mark.parametrize(
    "folder,expected",
    [
        ("09 Corporate Finance II__PT_COFIN2_26__843", "09 Corporate Finance II"),
        ("Leadership Series__EXEC VOICES__942", "Leadership Series"),
        ("no-suffix", "no-suffix"),
        ("__weird__1", "__weird__1"),
    ],
)
def test_course_label_strips_disk_only_suffixes(folder, expected):
    from canvas_archive.render.html import course_label

    assert course_label(folder) == expected
