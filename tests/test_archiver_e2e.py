"""End-to-end against a fake Canvas shaped like a locked-down instance.

The fixture deliberately mirrors what a real restricted deployment returns: the
/files and /pages indexes are denied, and modules are the only route to content.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from canvas_archive.archiver import Archiver
from canvas_archive.http.client import CanvasClient

BASE = "https://canvas.test"
API = f"{BASE}/api/v1"
PDF = b"%PDF-1.4" + b"z" * 4000


def _mount(
    *,
    file_failures: int = 0,
    submissions=None,
    page_body=None,
    syllabus=None,
    assignments=None,
) -> dict:
    state = {"file_attempts": 0}

    respx.get(f"{API}/users/self").mock(
        return_value=httpx.Response(200, json={"id": 7, "name": "Test Student"})
    )
    respx.get(url__startswith=f"{API}/users/self/enrollments").mock(
        return_value=httpx.Response(
            200, json=[{"course_id": 1, "grades": {"current_score": 88.5, "current_grade": "A"}}]
        )
    )
    respx.get(url__startswith=f"{API}/courses?").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "name": "Strategy 101",
                    "course_code": "STRAT",
                    "syllabus_body": syllabus or "<p>Read everything</p>",
                }
            ]
            if "enrollment_state=active" in str(request.url)
            else [],
        )
    )
    # Locked down, exactly like the real instance.
    respx.get(url__startswith=f"{API}/courses/1/files?").mock(return_value=httpx.Response(403))
    respx.get(url__startswith=f"{API}/courses/1/pages?").mock(
        return_value=httpx.Response(404, json={"message": "That page has been disabled"})
    )
    respx.get(url__startswith=f"{API}/courses/1/modules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "name": "Week 1",
                    "items": [
                        {"id": 100, "type": "File", "content_id": 500, "title": "Slides"},
                        {"id": 101, "type": "Page", "page_url": "intro", "title": "Intro"},
                        {"id": 102, "type": "ExternalUrl", "external_url": "https://example.com"},
                    ],
                }
            ],
        )
    )
    respx.get(f"{API}/courses/1/pages/intro").mock(
        return_value=httpx.Response(
            200,
            json={"title": "Intro", "body": page_body or "<h1>Hello</h1>"},
        )
    )
    respx.get(f"{API}/courses/1/files/500").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 500,
                "display_name": "Slides.pdf",
                "size": len(PDF),
                "url": "https://files.test/500?verifier=secret",
            },
        )
    )
    respx.get(url__startswith=f"{API}/courses/1/assignments").mock(
        return_value=httpx.Response(200, json=assignments or [])
    )
    respx.get(url__startswith=f"{API}/courses/1/discussion_topics").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(url__startswith=f"{API}/courses/1/students/submissions").mock(
        return_value=httpx.Response(200, json=submissions if submissions is not None else [])
    )
    respx.get(url__startswith="https://files.test/900").mock(
        return_value=httpx.Response(200, content=b"my essay")
    )
    # Locked down like the real instance: the quiz index is disabled.
    respx.get(url__startswith=f"{API}/courses/1/quizzes").mock(
        return_value=httpx.Response(404, json={"message": "That page has been disabled"})
    )

    def serve_file(request):
        state["file_attempts"] += 1
        if state["file_attempts"] <= file_failures:
            return httpx.Response(500)
        return httpx.Response(200, content=PDF)

    respx.get(url__startswith="https://files.test/500").mock(side_effect=serve_file)
    return state


@pytest.fixture
def client():
    c = CanvasClient(BASE, "1~tok", concurrency=2, retries=2)
    c.throttle.backoff = lambda *a, **k: _noop()
    return c


async def _noop():
    return None


@respx.mock
async def test_module_first_traversal_gets_content_despite_denied_indexes(client, tmp_path):
    _mount()
    stats = await Archiver(client, tmp_path).run()

    course_dir = next((tmp_path / "courses").iterdir())
    assert course_dir.name == "Strategy 101__STRAT__1"

    assert (course_dir / "files" / "Slides.pdf").read_bytes() == PDF
    intro = (course_dir / "pages" / "Intro.md").read_text()
    assert "# Intro" in intro and "Hello" in intro
    assert "<h1>Hello</h1>" not in intro
    assert not (course_dir / "syllabus.html").exists()
    assert "Read everything" in (course_dir / "README.md").read_text()
    assert (course_dir / "grades" / "grades.json").exists()

    assert stats.files_downloaded == 1
    assert stats.pages == 1
    # The denied index is reported as a normal skip, never as an error.
    assert stats.skipped["/files index denied"] == 1
    assert stats.errors == []
    await client.aclose()


@respx.mock
async def test_verifier_is_stripped_from_persisted_metadata(client, tmp_path):
    _mount()
    await Archiver(client, tmp_path).run()
    files_json = (next((tmp_path / "courses").iterdir()) / "files" / "files.json").read_text()
    assert "verifier" not in files_json
    assert "secret" not in files_json
    await client.aclose()


@respx.mock
async def test_first_pass_failure_is_recovered_by_the_retry_sweep(client, tmp_path):
    """Enough failures to exhaust the first pass, but the calm second pass succeeds."""
    state = _mount(file_failures=2)  # retries=2, so the first pass gives up
    stats = await Archiver(client, tmp_path).run()

    assert state["file_attempts"] > 2
    assert stats.recovered == 1
    assert stats.errors == []
    assert (next((tmp_path / "courses").iterdir()) / "files" / "Slides.pdf").read_bytes() == PDF
    await client.aclose()


@respx.mock
async def test_rerun_skips_completed_files(client, tmp_path):
    _mount()
    await Archiver(client, tmp_path).run()
    stats = await Archiver(client, tmp_path).run()
    assert stats.files_downloaded == 0
    assert stats.files_skipped == 1
    await client.aclose()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://x/files/1?verifier=abc123", "https://x/files/1"),
        (
            "https://x/files/1/download?download_frd=1&verifier=ab_c-1.2&y=2",
            "https://x/files/1/download?download_frd=1&y=2",
        ),
        # The dangerous case: verifiers inline in HTML bodies, not just metadata.
        (
            '<a href="https://x/files/9/preview?verifier=deadbeef">s</a>',
            '<a href="https://x/files/9/preview">s</a>',
        ),
        ("body with &amp;verifier=tok123 escaped", "body with  escaped"),
        ("no token here", "no token here"),
        ("", ""),
        ("https://x/files/1#verifier=abc123", "https://x/files/1"),
        ("https://x/files/1?%76erifier=abc123", "https://x/files/1"),
        ("https://x/files/1?download=1&%76erifier=abc123", "https://x/files/1?download=1"),
    ],
)
def test_strip_verifier(raw, expected):
    from canvas_archive.archiver import strip_verifier

    assert strip_verifier(raw) == expected


def test_scrub_walks_nested_payloads():
    from canvas_archive.archiver import scrub

    payload = {
        "url": "https://x?verifier=aaa",
        "items": [{"body": '<img src="https://x/f/1?verifier=bbb">'}],
        "count": 3,
        "nothing": None,
    }
    result = scrub(payload)
    assert "verifier" not in str(result)
    assert result["count"] == 3 and result["nothing"] is None


@respx.mock
async def test_no_verifier_survives_into_any_written_file(client, tmp_path):
    """The whole archive, not just files.json, must be free of capability tokens."""
    _mount()
    respx.get(f"{API}/courses/1/pages/intro").mock(
        return_value=httpx.Response(
            200,
            json={
                "title": "Intro",
                "body": '<a href="https://files.test/9?verifier=leaky">notes</a>',
            },
        )
    )
    await Archiver(client, tmp_path).run()

    for path in tmp_path.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".html"):
            assert "verifier" not in path.read_text(errors="ignore"), path
    await client.aclose()


# --- submissions -------------------------------------------------------------

FILE_SUBMISSION = {
    "id": 1,
    "assignment_id": 77,
    "attempt": 1,
    "workflow_state": "graded",
    "score": 18.0,
    "grade": "18",
    "assignment": {"id": 77, "name": "Essay: Why firms exist", "points_possible": 20},
    "attachments": [
        {
            "id": 900,
            "display_name": "essay.pdf",
            "size": 8,
            "url": "https://files.test/900?verifier=abc",
        }
    ],
    "submission_comments": [
        {
            "author": {"display_name": "Prof. Meier"},
            "created_at": "2026-03-01T10:00:00Z",
            "comment": "Strong argument, weak conclusion.",
        }
    ],
}

# No `attachments` key at all -- the exact shape the previous tool silently dropped.
TEXT_SUBMISSION = {
    "id": 2,
    "assignment_id": 88,
    "attempt": 1,
    "workflow_state": "submitted",
    "body": "<p>My typed answer</p>",
    "assignment": {"id": 88, "name": "Reflection"},
}

UNSUBMITTED = {
    "id": 3,
    "assignment_id": 99,
    "workflow_state": "unsubmitted",
    "assignment": {"id": 99, "name": "Never handed in"},
}


@respx.mock
async def test_submission_attachments_and_feedback_are_saved(client, tmp_path):
    _mount(submissions=[FILE_SUBMISSION])
    stats = await Archiver(client, tmp_path).run()

    folder = next((tmp_path / "courses").iterdir()) / "submissions" / "Essay- Why firms exist"
    assert (folder / "essay.pdf").read_bytes() == b"my essay"
    assert (folder / "submission.json").exists()
    readme = (folder / "README.md").read_text()
    assert "Prof. Meier" in readme and "weak conclusion" in readme
    assert "# Essay: Why firms exist" in readme
    assert "**Score:** 18.0 / 20" in readme

    assert stats.submissions == 1
    assert stats.submission_files == 1
    await client.aclose()


@respx.mock
async def test_submission_without_attachments_is_not_dropped(client, tmp_path):
    """Regression: text-entry submissions have no `attachments` key at all."""
    _mount(submissions=[TEXT_SUBMISSION])
    stats = await Archiver(client, tmp_path).run()

    folder = next((tmp_path / "courses").iterdir()) / "submissions" / "Reflection"
    readme = (folder / "README.md").read_text()
    assert "My typed answer" in readme
    assert not (folder / "submission.html").exists()
    assert (folder / "submission.json").exists()
    assert stats.submissions == 1
    await client.aclose()


@respx.mock
async def test_unsubmitted_assignments_do_not_create_folders(client, tmp_path):
    _mount(submissions=[UNSUBMITTED])
    stats = await Archiver(client, tmp_path).run()
    assert stats.submissions == 0
    assert not (next((tmp_path / "courses").iterdir()) / "submissions").exists()
    await client.aclose()


@respx.mock
async def test_mixed_submissions_all_recorded(client, tmp_path):
    _mount(submissions=[FILE_SUBMISSION, TEXT_SUBMISSION, UNSUBMITTED])
    stats = await Archiver(client, tmp_path).run()
    assert stats.submissions == 2  # the unsubmitted one is correctly excluded
    assert stats.submission_files == 1
    subs = next((tmp_path / "courses").iterdir()) / "submissions"
    assert (subs / "submissions.json").exists()
    assert {p.name for p in subs.iterdir() if p.is_dir()} == {
        "Essay- Why firms exist",
        "Reflection",
    }
    await client.aclose()


@respx.mock
async def test_one_failing_exporter_does_not_cost_the_course_its_files(client, tmp_path):
    """A course must not lose 40 PDFs because its quizzes endpoint misbehaved."""
    _mount()
    respx.get(url__startswith=f"{API}/courses/1/quizzes").mock(
        side_effect=httpx.ConnectError("boom")
    )
    stats = await Archiver(client, tmp_path, build_html=False).run()

    course_dir = next((tmp_path / "courses").iterdir())
    assert (course_dir / "files" / "Slides.pdf").read_bytes() == PDF  # still archived
    assert stats.files_downloaded == 1
    assert any("quizzes" in e for e in stats.errors)  # and reported


@respx.mock
async def test_html_viewer_is_generated_by_default(client, tmp_path):
    _mount()
    stats = await Archiver(client, tmp_path).run()
    assert (tmp_path / "index.html").exists()
    assert stats.html_pages > 0
    course_dir = next((tmp_path / "courses").iterdir())
    assert (course_dir / "index.html").exists()
    await client.aclose()


@respx.mock
async def test_html_viewer_can_be_switched_off(client, tmp_path):
    _mount()
    await Archiver(client, tmp_path, build_html=False).run()
    assert not (tmp_path / "index.html").exists()
    assert (tmp_path / "README.md").exists()
    await client.aclose()


# --- files referenced from course text ---------------------------------------


@respx.mock
async def test_syllabus_linked_pdf_is_downloaded_and_repointed(client, tmp_path):
    """A syllabus is usually a *link* to a PDF, not inline text.

    Module-first traversal never sees it, so without this the most important
    document in the course is archived as a dead URL.
    """
    _mount(
        syllabus=(
            '<p>See <a href="https://canvas.test/courses/1/files/700'
            '?location=course_syllabus_1&wrap=1">STRAT_Syllabus.pdf</a></p>'
        )
    )
    respx.get(f"{API}/courses/1/files/700").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 700,
                "display_name": "STRAT_Syllabus.pdf",
                "size": 9,
                "url": "https://files.test/700?verifier=zz",
            },
        )
    )
    respx.get(url__startswith="https://files.test/700").mock(
        return_value=httpx.Response(200, content=b"SYLLABUS!")
    )

    stats = await Archiver(client, tmp_path, build_html=False).run()
    course = next((tmp_path / "courses").iterdir())

    assert (course / "files" / "STRAT_Syllabus.pdf").read_bytes() == b"SYLLABUS!"
    assert stats.linked_files == 1

    # The README must point at the local copy, not back at Canvas.
    readme = (course / "README.md").read_text()
    assert "canvas.test/courses/1/files/700" not in readme
    assert "STRAT_Syllabus.pdf" in readme
    assert "files/STRAT_Syllabus.pdf" in readme.replace("%20", " ")

    # And it must appear in the file index, so the archive describes itself.
    listed = json.loads((course / "files" / "files.json").read_text())
    assert any(f.get("id") == 700 for f in listed)
    await client.aclose()


@respx.mock
async def test_linked_and_embedded_files_go_to_different_places(client, tmp_path):
    """Images are decoration and belong in _media; documents belong in files/."""
    _mount(
        page_body=(
            '<p><img src="https://canvas.test/courses/1/files/801/preview">'
            '<a href="https://canvas.test/courses/1/files/802">Handout.pdf</a></p>'
        )
    )
    for fid, name, body in ((801, "diagram.png", b"PNG"), (802, "Handout.pdf", b"PDF")):
        respx.get(f"{API}/courses/1/files/{fid}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": fid,
                    "display_name": name,
                    "size": 3,
                    "url": f"https://files.test/{fid}",
                },
            )
        )
        respx.get(url__startswith=f"https://files.test/{fid}").mock(
            return_value=httpx.Response(200, content=body)
        )

    stats = await Archiver(client, tmp_path, build_html=False).run()
    course = next((tmp_path / "courses").iterdir())

    assert (course / "_media" / "diagram.png").exists()
    assert (course / "files" / "Handout.pdf").exists()
    assert stats.inline_images == 1
    assert stats.linked_files == 1
    await client.aclose()


@respx.mock
async def test_inaccessible_linked_file_is_a_skip_not_a_crash(client, tmp_path):
    _mount(page_body='<a href="https://canvas.test/courses/1/files/999">Secret.pdf</a>')
    respx.get(f"{API}/courses/1/files/999").mock(return_value=httpx.Response(403))

    stats = await Archiver(client, tmp_path, build_html=False).run()
    assert stats.skipped["linked file not accessible"] == 1
    assert stats.errors == []
    await client.aclose()


@respx.mock
async def test_sideways_canvas_links_point_at_local_copies(client, tmp_path):
    """Links to a module or assignment page die with the account; repoint them."""
    _mount(
        assignments=[{"id": 77, "name": "The essay"}],
        page_body=(
            '<p><a href="https://canvas.test/courses/1/modules/55">Module 1</a> '
            '<a href="https://canvas.test/courses/1/assignments/77">The essay</a> '
            '<a href="https://canvas.test/courses/1">Course home</a></p>'
        ),
    )
    await Archiver(client, tmp_path, build_html=False).run()
    leaf = (next((tmp_path / "courses").iterdir()) / "pages" / "Intro.md").read_text()

    assert "canvas.test/courses/1/modules/55" not in leaf
    assert "modules/modules.md" in leaf.replace("%20", " ")
    assert "assignments/assignments.md" in leaf.replace("%20", " ")
    assert "README.md" in leaf
    await client.aclose()


@respx.mock
async def test_links_to_other_courses_are_left_alone(client, tmp_path):
    """We have no local copy of a course you aren't enrolled in; don't fake one."""
    _mount(page_body='<a href="https://canvas.test/courses/430/modules/9">Elsewhere</a>')
    await Archiver(client, tmp_path, build_html=False).run()
    leaf = (next((tmp_path / "courses").iterdir()) / "pages" / "Intro.md").read_text()
    assert "canvas.test/courses/430/modules/9" in leaf
    await client.aclose()


@respx.mock
async def test_link_to_a_section_we_did_not_archive_is_left_alone(client, tmp_path):
    """--only pages means there is no discussions.md to point at."""
    _mount(page_body='<a href="https://canvas.test/courses/1/discussion_topics/3">Thread</a>')
    await Archiver(client, tmp_path, content={"pages", "modules"}, build_html=False).run()
    leaf = (next((tmp_path / "courses").iterdir()) / "pages" / "Intro.md").read_text()
    assert "canvas.test/courses/1/discussion_topics/3" in leaf
    await client.aclose()


# --- byte accounting across every download path ------------------------------


class _ByteRecorder:
    """Minimal progress sink that records only the byte counters."""

    def __init__(self):
        self.total = 0
        self.advanced = 0

    def __getattr__(self, _name):
        return lambda *a, **k: None

    def add_bytes_total(self, count):
        self.total += count

    def advance_bytes(self, count):
        self.advanced += count


@respx.mock
async def test_bytes_are_counted_for_course_files(client, tmp_path):
    _mount()
    rec = _ByteRecorder()
    await Archiver(client, tmp_path, progress=rec, build_html=False).run()
    assert rec.total == len(PDF)
    assert rec.advanced == len(PDF)
    await client.aclose()


@respx.mock
async def test_nameless_course_gets_a_fallback_folder(client, tmp_path):
    _mount()
    respx.get(url__startswith=f"{API}/courses?").mock(
        side_effect=lambda request: httpx.Response(
            200,
            json=[{"id": 1, "course_code": "STRAT"}]
            if "enrollment_state=active" in str(request.url)
            else [],
        )
    )
    await Archiver(client, tmp_path, build_html=False).run()
    names = [p.name for p in (tmp_path / "courses").iterdir()]
    assert names == ["course-1__STRAT__1"]
    await client.aclose()


@respx.mock
async def test_course_filter_with_no_match_is_a_clear_error(client, tmp_path):
    _mount()
    with pytest.raises(SystemExit, match="No matching courses"):
        await Archiver(client, tmp_path, build_html=False).run({99})
    await client.aclose()


@respx.mock
async def test_skip_files_does_not_download_linked_files(client, tmp_path):
    _mount(syllabus='<a href="https://canvas.test/courses/1/files/700">S.pdf</a>')
    respx.get(f"{API}/courses/1/files/700").mock(
        return_value=httpx.Response(
            200,
            json={"id": 700, "display_name": "S.pdf", "size": 9, "url": "https://files.test/700"},
        )
    )
    respx.get(url__startswith="https://files.test/700").mock(
        return_value=httpx.Response(200, content=b"SYLLABUS!")
    )
    stats = await Archiver(
        client, tmp_path, content={"modules", "pages", "syllabus"}, build_html=False
    ).run()
    course = next((tmp_path / "courses").iterdir())
    assert not (course / "files").exists()
    assert stats.linked_files == 0
    await client.aclose()


@respx.mock
async def test_empty_files_index_is_not_reported_as_denied(client, tmp_path):
    _mount()
    respx.get(url__startswith=f"{API}/courses/1/files?").mock(
        return_value=httpx.Response(200, json=[])
    )
    stats = await Archiver(client, tmp_path, build_html=False).run()
    assert "/files index denied" not in stats.skipped
    assert stats.files_downloaded == 1  # still fetched from the module item
    await client.aclose()


@respx.mock
async def test_bytes_are_counted_for_submission_attachments(client, tmp_path):
    """Regression: attachments downloaded without reporting a single byte."""
    _mount(submissions=[FILE_SUBMISSION])
    rec = _ByteRecorder()
    await Archiver(client, tmp_path, progress=rec, build_html=False).run()
    # The course file plus the 8-byte essay attachment.
    assert rec.total == len(PDF) + 8
    assert rec.advanced == len(PDF) + 8
    await client.aclose()


@respx.mock
async def test_bytes_are_counted_for_linked_files(client, tmp_path):
    """Regression: syllabus PDFs downloaded without reporting a single byte."""
    _mount(syllabus='<a href="https://canvas.test/courses/1/files/700">S.pdf</a>')
    respx.get(f"{API}/courses/1/files/700").mock(
        return_value=httpx.Response(
            200,
            json={"id": 700, "display_name": "S.pdf", "size": 9, "url": "https://files.test/700"},
        )
    )
    respx.get(url__startswith="https://files.test/700").mock(
        return_value=httpx.Response(200, content=b"SYLLABUS!")
    )
    rec = _ByteRecorder()
    await Archiver(client, tmp_path, progress=rec, build_html=False).run()
    assert rec.total == len(PDF) + 9
    assert rec.advanced == len(PDF) + 9
    await client.aclose()


@respx.mock
async def test_already_present_files_still_advance_the_bar(client, tmp_path):
    """A resumed run must not sit at zero while it is clearly doing work."""
    _mount()
    await Archiver(client, tmp_path, build_html=False).run()

    rec = _ByteRecorder()
    stats = await Archiver(client, tmp_path, progress=rec, build_html=False).run()
    assert stats.files_skipped == 1  # nothing re-downloaded
    assert rec.advanced == len(PDF)  # but the bar still reaches 100%
    assert rec.total == len(PDF)
    await client.aclose()
