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
    assert 'href="./Lecture%20slides.view.html"' in html  # PDFs open in the viewer
    assert 'href="./notes.docx"' in html  # other types open directly
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
    # A PDF routes through the in-page viewer, which is navigation: same tab.
    assert '<a href="./paper.view.html">' in html
    assert '<a href="./sub/index.html">' in html  # the folder


# --- layout ------------------------------------------------------------------


def test_page_is_wide_but_prose_keeps_a_measure():
    """A 44rem column wasted most of a wide screen and wrapped the course names."""
    from canvas_archive.render.html import CSS

    assert "max-width:64rem" in CSS  # the page itself
    assert "max-width:46rem" in CSS  # running text only


def test_word_breaking_does_not_shrink_tables():
    """`overflow-wrap:anywhere` is used for min-content sizing, so tables collapse."""
    from canvas_archive.render.html import CSS

    assert "overflow-wrap:anywhere" not in CSS
    assert "overflow-wrap:break-word" in CSS


# --- reading experience ------------------------------------------------------


def test_raw_json_and_markdown_are_hidden_from_listings(tmp_path):
    """The payloads are the archival record, not something to browse."""
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "files").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "paper.pdf").write_bytes(b"x")
    (course / "files" / "files.json").write_text("[]")
    build_site(tmp_path)

    html = (course / "files" / "index.html").read_text()
    assert "paper.pdf" in html
    assert "files.json" not in html
    assert (course / "files" / "files.json").exists()  # still on disk


def test_a_rendered_section_is_not_replaced_by_a_file_listing(tmp_path):
    """Clicking Grades must land on grades.html, not on a folder of JSON."""
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "grades").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "grades" / "grades.md").write_text("# Grades\n\nYour score.\n")
    (course / "grades" / "grades.json").write_text("{}")
    build_site(tmp_path)

    assert not (course / "grades" / "index.html").exists()
    assert "Your score." in (course / "grades" / "grades.html").read_text()
    # And the course page links to the rendered page.
    assert "grades/grades.html" in (course / "index.html").read_text()


def test_pdfs_get_an_in_page_viewer(tmp_path):
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "files").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "Syllabus.pdf").write_bytes(b"%PDF-1.4" + b"x" * 900)
    build_site(tmp_path)

    viewer = course / "files" / "Syllabus.view.html"
    assert viewer.exists()
    html = viewer.read_text()
    assert '<object class="pdf" data="./Syllabus.pdf"' in html
    assert 'type="application/pdf"' in html
    assert "open directly" in html  # escape hatch
    assert "Open Syllabus.pdf" in html  # fallback when no PDF plugin
    assert "All courses" in html  # still inside the archive


def test_non_pdf_files_do_not_get_a_viewer(tmp_path):
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "files").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "sheet.xlsx").write_bytes(b"x")
    build_site(tmp_path)
    assert not list((course / "files").glob("*.view.html"))


# --- grade scale -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,fill",
    [
        ("1 / 0.0%", 0.0),  # scale floor
        ("6 / 100.0%", 100.0),  # scale ceiling
        ("5.25 / 78.45%", 85.0),
        ("4.75 / 64.5%", 75.0),
        ("3.5 / 50.0%", 50.0),
    ],
)
def test_swiss_mark_is_placed_on_a_one_to_six_scale(text, fill):
    """A bare number means nothing to someone who doesn't know the scale."""
    import re

    from canvas_archive.render.html import grade_cell

    got = re.search(r"width:([0-9.]+)%", grade_cell(text))
    assert got and abs(float(got.group(1)) - fill) < 0.05


def test_percentage_only_grade_uses_the_percentage():
    import re

    from canvas_archive.render.html import grade_cell

    out = grade_cell("100.0%")
    assert re.search(r"width:100\.0%", out)
    assert "g-pct" not in out  # no redundant second number


@pytest.mark.parametrize("text", ["—", "", "pass", "incomplete", "5 of 6"])
def test_ungradeable_values_pass_through_as_text(text):
    from canvas_archive.render.html import grade_cell

    out = grade_cell(text)
    assert "g-track" not in out
    assert text.strip() in out or out == ""


def test_grade_cell_escapes_its_input():
    from canvas_archive.render.html import grade_cell

    assert "<b>" not in grade_cell("<b>A</b>")


def test_out_of_range_marks_are_clamped():
    import re

    from canvas_archive.render.html import grade_cell

    for text in ("0 / 0.0%", "9 / 100.0%"):
        got = re.search(r"width:([0-9.]+)%", grade_cell(text))
        assert got and 0.0 <= float(got.group(1)) <= 100.0


def test_only_the_root_index_gets_grade_bars(tmp_path):
    """A rubric table elsewhere must not have its second column rewritten."""
    import re

    def body(path):
        # The stylesheet defines .g-track on every page; only the markup matters.
        return re.sub(r"<style>.*?</style>", "", path.read_text(), flags=re.S)

    (tmp_path / "README.md").write_text(
        "# Archive\n\n| Course | Grade |\n|---|---|\n"
        "| [C](./courses/C__1/README.md) | 5 / 70.0% |\n"
    )
    course = tmp_path / "courses" / "C__1"
    (course / "grades").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "grades" / "grades.md").write_text(
        "# Grades\n\n| Assignment | Score |\n|---|---|\n| Essay | 5 / 70.0% |\n"
    )
    build_site(tmp_path)

    assert '<span class="g-track">' in body(tmp_path / "index.html")
    assert '<span class="g-track">' not in body(course / "grades" / "grades.html")


def test_rebuilding_regenerates_listings_rather_than_skipping_them(tmp_path):
    """A listing written by an earlier build must not block its own regeneration.

    The old check tested the filesystem, which cannot tell a README-derived page
    from a stale listing, so re-running silently kept the outdated output.
    """
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "files").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "files" / "first.pdf").write_bytes(b"x")
    build_site(tmp_path)
    assert "first.pdf" in (course / "files" / "index.html").read_text()

    (course / "files" / "second.pdf").write_bytes(b"y")
    build_site(tmp_path)
    listing = (course / "files" / "index.html").read_text()
    assert "second.pdf" in listing  # the new file appears
    assert (course / "files" / "second.view.html").exists()


def test_stale_listings_are_removed_from_rendered_folders(tmp_path):
    """An index.html is only legitimate where a README.md produced it."""
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "grades").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "grades" / "grades.md").write_text("# Grades\n")
    # Left behind by an older version of the generator.
    (course / "grades" / "index.html").write_text("<html>stale listing</html>")

    build_site(tmp_path)
    assert not (course / "grades" / "index.html").exists()
    assert (course / "grades" / "grades.html").exists()


def test_section_card_links_to_the_section_page_not_an_arbitrary_one(tmp_path):
    """pages/ holds one file per page; the card must not link to whichever sorts first."""
    (tmp_path / "README.md").write_text("# A\n")
    course = tmp_path / "courses" / "C__1"
    (course / "pages").mkdir(parents=True)
    (course / "README.md").write_text("# C\n")
    (course / "pages" / "pages.md").write_text("# Pages\n\nAll of them.\n")
    (course / "pages" / "Aardvark.html").write_text("<p>an individual page</p>")
    build_site(tmp_path)

    index = (course / "index.html").read_text()
    assert "pages/pages.html" in index
    assert "Aardvark.html" not in index
