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

    assert count >= 3
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


# --- file browser ------------------------------------------------------------


def test_folders_of_files_get_a_generated_index(tmp_path):
    """A browser will not draw a directory index for file:// -- Chrome refuses."""
    (tmp_path / "README.md").write_text("# Archive\n")
    course = tmp_path / "courses" / "C__1"
    files = course / "files"
    files.mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (files / "Lecture slides.pdf").write_bytes(b"x" * 2048)
    (files / "notes.docx").write_bytes(b"y" * 500)

    build_site(tmp_path)
    index = files / "index.html"
    assert index.exists()

    html = index.read_text()
    assert 'href="./Lecture%20slides.pdf"' in html  # spaces encoded
    assert 'href="./notes.docx"' in html
    assert "2.0 KB" in html  # human sizes
    assert 'href="#i-doc"' in html  # per-type icon, not a text label


def test_file_listing_links_are_followable(tmp_path):
    import re
    import urllib.parse

    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    sub = course / "submissions" / "Essay 1"
    sub.mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (sub / "my essay (final).pdf").write_bytes(b"z")

    build_site(tmp_path)
    for html_file in tmp_path.rglob("*.html"):
        for href in re.findall(r'href="(\./[^"]+)"', html_file.read_text()):
            target = html_file.parent / urllib.parse.unquote(href)
            assert target.exists(), f"{html_file.name} -> {href}"


def test_nested_folders_are_navigable(tmp_path):
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "submissions" / "Essay 1").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "submissions" / "Essay 1" / "essay.pdf").write_bytes(b"z")

    build_site(tmp_path)
    parent = (course / "submissions" / "index.html").read_text()
    assert 'href="./Essay%201/index.html"' in parent
    assert "1 item" in parent


def test_empty_folders_do_not_get_an_index(tmp_path):
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "empty").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    build_site(tmp_path)
    assert not (course / "empty" / "index.html").exists()


def test_markdown_derived_pages_are_not_overwritten_by_a_listing(tmp_path):
    """grades/ holds grades.md; its page must stay the rendered Markdown."""
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "grades").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "grades" / "grades.md").write_text("# Grades\n\nYour score.\n")
    build_site(tmp_path)
    assert "Your score." in (course / "grades" / "grades.html").read_text()


# --- visual design -----------------------------------------------------------


def test_pages_are_fully_self_contained(tmp_path):
    """No webfont, no icon CDN, no analytics -- it must work from a USB stick."""
    (tmp_path / "README.md").write_text("# A\n\n[x](./courses/C__1/README.md)\n")
    course = tmp_path / "courses" / "C__1"
    (course / "files").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "a.pdf").write_bytes(b"x")
    build_site(tmp_path)

    for html_file in tmp_path.rglob("*.html"):
        text = html_file.read_text()
        assert "http://" not in text
        assert "https://" not in text
        assert "@import" not in text
        assert "<link" not in text  # no external stylesheet
        assert "<script" not in text  # no JS at all


def test_icons_referenced_are_actually_defined(tmp_path):
    """A <use href="#i-x"> with no matching symbol renders as nothing at all."""
    import re

    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    for section in ("files", "grades", "submissions"):
        (course / section).mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "a.pdf").write_bytes(b"x")
    (course / "grades" / "grades.md").write_text("# Grades\n")
    (course / "submissions" / "S1").mkdir()
    (course / "submissions" / "S1" / "b.docx").write_bytes(b"y")
    build_site(tmp_path)

    for html_file in tmp_path.rglob("*.html"):
        text = html_file.read_text()
        defined = set(re.findall(r'<g id="i-([a-z]+)"', text))
        used = set(re.findall(r'<use href="#i-([a-z]+)"', text))
        assert used <= defined, f"{html_file.name}: undefined icons {used - defined}"


def test_dark_mode_is_defined():
    from canvas_archive.render.html import CSS, page

    assert "prefers-color-scheme:dark" in CSS
    html = page("T", "<p>x</p>")
    assert "--bg:" in html and "--fg:" in html


def test_no_external_font_is_requested():
    from canvas_archive.render.html import CSS

    assert "fonts.googleapis" not in CSS and "@font-face" not in CSS
    assert "-apple-system" in CSS  # system stack only


# --- link targets ------------------------------------------------------------


@pytest.mark.parametrize(
    "target,new_tab",
    [
        ("./notes.pdf", True),
        ("../files/Slides%20v2.pptx", True),
        ("./recording.mp4", True),
        ("./sheet.xlsx", True),
        ("./index.html", False),  # navigation stays in place
        ("./pages/pages.html", False),
        ("./sub/", False),  # a folder is navigation
        ("https://example.com", True),
    ],
)
def test_documents_open_in_a_new_tab_but_pages_do_not(target, new_tab):
    """Clicking a PDF should not replace the page you were reading."""
    from canvas_archive.render.html import _link_attrs

    assert ('target="_blank"' in _link_attrs(target)) is new_tab


def test_external_links_get_noreferrer():
    from canvas_archive.render.html import _link_attrs

    assert "noreferrer" in _link_attrs("https://example.com")
    assert "noreferrer" not in _link_attrs("./local.pdf")  # no referrer to leak


def test_markdown_document_links_open_in_a_new_tab():
    out = markdown_to_html("[Slides](../files/Slides.pdf) then [Grades](./grades.html)")
    assert '<a href="../files/Slides.pdf" target="_blank" rel="noopener">' in out
    assert '<a href="./grades.html">' in out


def test_file_browser_rows_open_in_a_new_tab(tmp_path):
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "files").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "paper.pdf").write_bytes(b"x")
    (course / "files" / "sub").mkdir()
    (course / "files" / "sub" / "inner.pdf").write_bytes(b"y")
    build_site(tmp_path)

    html = (course / "files" / "index.html").read_text()
    assert '<a href="./paper.pdf" target="_blank"' in html  # the document
    assert '<a href="./sub/index.html">' in html  # the folder
