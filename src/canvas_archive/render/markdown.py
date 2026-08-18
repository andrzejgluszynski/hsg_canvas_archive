"""Turn raw Canvas payloads into Markdown a human actually wants to read.

The JSON stays on disk as the source of truth -- it is the complete record, and it is
what a future tool would parse. These files sit alongside it for the far commoner case
of a person opening the folder years later and wanting to read their coursework.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .html2md import _safe_href, html_to_markdown

MAX_BODY = 200_000  # a runaway body should not produce an unopenable file

_PASS_FAIL = frozenset(
    {"pass", "fail", "credit", "no credit", "complete", "incomplete", "passed", "failed"}
)
_SWISS_MARK = re.compile(r"^\d+(?:\.\d+)?$")


def fmt_date(value: str | None, *, with_time: bool = True) -> str:
    """Canvas timestamps are ISO-8601 Zulu; render them plainly."""
    if not value:
        return ""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return str(value)
    return stamp.strftime("%d %b %Y, %H:%M") if with_time else stamp.strftime("%d %b %Y")


def _body(html: str | None) -> str:
    text = html_to_markdown(html)
    if len(text) > MAX_BODY:
        text = text[:MAX_BODY] + "\n\n*(truncated -- see the JSON for the full text)*"
    return text


def _render(lines: list[str]) -> str:
    """Join lines and normalise whitespace.

    Renderers append sections independently, so blank-line runs are inevitable at the
    seams. Collapsing centrally beats making every renderer track what preceded it.
    """
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _section(title: str, level: int = 2) -> str:
    return f"{'#' * level} {title}"


def _link(label: str, target: str) -> str:
    """A Markdown link to a local file.

    Filenames routinely contain spaces, brackets and other characters that break bare
    Markdown link targets, so percent-encode the path. Renderers must never emit a
    link a viewer cannot follow.
    """
    return f"[{label}](./{quote(target)})"


def heading_id(text: str) -> str:
    """Stable fragment id for a heading, so module items can deep-link into a TOC."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    slug = re.sub(r"[-\s]+", "-", slug.strip()).lower()
    return slug or "section"


def safe_href(url: str | None) -> str | None:
    """Drop javascript:/data:/vbscript: URLs. Relative, http(s) and mailto stay."""
    return _safe_href(url)


def _is_swiss_mark(value: str) -> bool:
    if not _SWISS_MARK.match(value.strip()):
        return False
    number = float(value)
    return 1.0 <= number <= 6.0


def _grade_line(grades: dict | None) -> str:
    """A short grade for the index, hub, and grades page — one string, shared.

    Swiss 1–6 courses keep `5.25 / 78.45%`. Pass/fail courses must not look like a
    perfect percentage: Canvas typically sends `current_score: 100` and no mark.
    """
    if not grades:
        return ""
    letter = grades.get("current_grade") or grades.get("final_grade") or ""
    letter = str(letter).strip()
    score = grades.get("current_score")
    if score is None:
        score = grades.get("final_score")

    if letter and letter.lower() in _PASS_FAIL:
        return letter
    if letter and _is_swiss_mark(letter):
        parts = [letter]
        if score is not None:
            parts.append(f"{score}%")
        return " / ".join(parts)
    if letter:
        parts = [letter]
        if score is not None:
            parts.append(f"{score}%")
        return " / ".join(parts)
    if score is None:
        return ""
    try:
        numeric = float(score)
    except (TypeError, ValueError):
        return f"{score}%"
    if numeric == 100:
        return "Pass"
    if numeric == 0:
        return "Fail"
    return f"{score}%"


# --- per content type --------------------------------------------------------


def course_overview(course: dict, grades: dict | None) -> str:
    """Course hub: identity, grade, syllabus. Section cards are injected in HTML."""
    name = course.get("name") or "Course"
    lines = [f"# {name}", ""]

    meta = []
    if course.get("course_code"):
        meta.append(f"**Code:** {course['course_code']}")
    term = (course.get("term") or {}).get("name")
    if term and term.lower() != "default term":
        meta.append(f"**Term:** {term}")
    grade = _grade_line(grades)
    if grade:
        meta.append(f"**Your grade:** {grade}")
    if meta:
        lines += [" · ".join(meta), ""]

    if course.get("syllabus_body"):
        lines += ["## Syllabus", "", _body(course["syllabus_body"]), ""]

    return _render(lines)


def grades_md(
    course_name: str,
    grades: dict | None,
    submissions: list[dict],
    *,
    submission_links: dict | None = None,
) -> str:
    submission_links = submission_links or {}
    lines = [f"# Grades — {course_name}", ""]
    overall = _grade_line(grades)
    if overall:
        lines += [f"**Overall: {overall}**", ""]
        if grades and grades.get("final_score") is not None and overall not in {"Pass", "Fail"}:
            letter = str(grades.get("current_grade") or "").strip().lower()
            if letter not in _PASS_FAIL:
                lines.append(f"Final score: {grades['final_score']}  ")
        if grades and grades.get("final_grade"):
            letter = str(grades.get("final_grade")).strip()
            if letter.lower() not in _PASS_FAIL and not _is_swiss_mark(letter):
                lines.append(f"Final grade: {grades['final_grade']}")
        lines.append("")

    graded = [s for s in submissions if s.get("score") is not None]
    if not graded:
        lines += ["Nothing has been graded yet.", ""]
        return _render(lines)

    lines += ["| Assignment | Score | Out of | Graded |", "|---|---|---|---|"]
    for sub in graded:
        assignment = sub.get("assignment") or {}
        name = (assignment.get("name") or "?").replace("|", "\\|")
        aid = assignment.get("id") or sub.get("assignment_id")
        folder = submission_links.get(aid)
        if folder:
            name = f"[{name}](../{quote(folder)}/README.md)"
        possible = assignment.get("points_possible")
        lines.append(
            f"| {name} | {sub.get('score')} | "
            f"{possible if possible is not None else '-'} | "
            f"{fmt_date(sub.get('graded_at'), with_time=False)} |"
        )
    lines.append("")
    return _render(lines)


def submission_md(submission: dict) -> str:
    assignment = submission.get("assignment") or {}
    lines = [f"# {assignment.get('name') or 'Submission'}", ""]

    facts = []
    if submission.get("score") is not None:
        possible = assignment.get("points_possible")
        facts.append(
            f"**Score:** {submission['score']}" + (f" / {possible}" if possible is not None else "")
        )
    if submission.get("grade"):
        facts.append(f"**Grade:** {submission['grade']}")
    if submission.get("submitted_at"):
        facts.append(f"**Submitted:** {fmt_date(submission['submitted_at'])}")
    if submission.get("late"):
        facts.append("**Late**")
    if facts:
        lines += [" · ".join(facts), ""]

    if assignment.get("description"):
        lines += ["", _section("The assignment"), "", _body(assignment["description"]), ""]

    attachments = submission.get("attachments") or []
    if attachments:
        lines += ["", _section("What I submitted"), ""]
        for att in attachments:
            name = att.get("display_name") or att.get("filename") or "file"
            lines.append(f"- {_link(name, name)}")
        lines.append("")

    if submission.get("body"):
        lines += ["", _section("My answer"), "", _body(submission["body"]), ""]

    comments = submission.get("submission_comments") or []
    if comments:
        lines += ["", _section("Feedback"), ""]
        for comment in comments:
            author = (comment.get("author") or {}).get("display_name") or "Instructor"
            lines += [
                f"**{author}** — {fmt_date(comment.get('created_at'))}",
                "",
                _body(comment.get("comment")) or (comment.get("comment") or ""),
                "",
            ]

    rubric = submission.get("rubric_assessment") or {}
    criteria = {c.get("id"): c for c in (assignment.get("rubric") or [])}
    if rubric:
        lines += ["", _section("Rubric"), "", "| Criterion | Points | Comment |", "|---|---|---|"]
        for key, entry in rubric.items():
            if not isinstance(entry, dict):
                continue
            label = (criteria.get(key, {}).get("description") or key).replace("|", "\\|")
            comment = str(entry.get("comments") or "").replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {label} | {entry.get('points', '-')} | {comment} |")
        lines.append("")

    return _render(lines)


def assignments_md(
    course_name: str,
    assignments: list[dict],
    *,
    submission_links: dict | None = None,
) -> str:
    """A TOC of assignments. The brief lives on the submission leaf when one exists."""
    submission_links = submission_links or {}
    lines = [f"# Assignments — {course_name}", ""]
    if not assignments:
        lines += ["*No assignments were archived.*", ""]
        return _render(lines)

    for item in assignments:
        title = item.get("name") or "Untitled"
        folder = submission_links.get(item.get("id"))
        heading = f"[{title}](../{quote(folder)}/README.md)" if folder else title
        lines.append(f"## {heading}")
        facts = []
        if item.get("due_at"):
            facts.append(f"Due {fmt_date(item['due_at'])}")
        if item.get("points_possible") is not None:
            facts.append(f"{item['points_possible']} points")
        if facts:
            lines += ["", " · ".join(facts)]
        # Unsubmitted work has no leaf; keep the brief here so it is not lost.
        if not folder:
            body = _body(item.get("description"))
            if body:
                lines += ["", body]
        lines.append("")
    return _render(lines)


def announcements_md(course_name: str, items: list[dict]) -> str:
    lines = [f"# Announcements — {course_name}", ""]
    ordered = sorted(items, key=lambda a: a.get("posted_at") or "", reverse=True)
    for item in ordered:
        lines.append(f"## {item.get('title') or 'Untitled'}")
        stamp = fmt_date(item.get("posted_at"))
        author = (item.get("author") or {}).get("display_name") or item.get("user_name")
        byline = " · ".join(p for p in (stamp, author) if p)
        if byline:
            lines += ["", f"*{byline}*"]
        body = _body(item.get("message"))
        if body:
            lines += ["", body]
        lines.append("")
    return _render(lines)


def _entry_lines(entries: list[dict], depth: int = 0) -> list[str]:
    """Render a reply thread, indenting each level as a nested blockquote."""
    lines: list[str] = []
    prefix = "> " * depth
    for entry in entries:
        if entry.get("deleted"):
            continue
        author = (
            entry.get("user_name") or (entry.get("user") or {}).get("display_name") or "Someone"
        )
        stamp = fmt_date(entry.get("created_at"))
        lines.append(f"{prefix}**{author}** — {stamp}")
        lines.append(f"{prefix}")
        body = _body(entry.get("message"))
        for line in (body or "*(no text)*").splitlines():
            lines.append(f"{prefix}{line}")
        lines.append("")
        replies = entry.get("replies") or entry.get("recent_replies") or []
        if replies:
            lines += _entry_lines(replies, depth + 1)
    return lines


def discussions_md(course_name: str, items: list[dict]) -> str:
    lines = [f"# Discussions — {course_name}", ""]
    for topic in sorted(items, key=lambda t: t.get("posted_at") or "", reverse=True):
        lines.append(f"## {topic.get('title') or 'Untitled'}")
        stamp = fmt_date(topic.get("posted_at"))
        author = (topic.get("author") or {}).get("display_name") or topic.get("user_name")
        byline = " · ".join(p for p in (stamp, author) if p)
        if byline:
            lines += ["", f"*{byline}*"]
        body = _body(topic.get("message"))
        if body:
            lines += ["", body]

        entries = topic.get("_entries") or []
        if entries:
            lines += ["", f"### Replies ({len(entries)})", ""]
            lines += _entry_lines(entries)
        else:
            lines += ["", "*No replies.*", ""]
    return _render(lines)


def quizzes_md(course_name: str, quizzes: list[dict]) -> str:
    lines = [f"# Quizzes — {course_name}", ""]
    for quiz in quizzes:
        lines.append(f"## {quiz.get('title') or 'Quiz'}")
        submission = quiz.get("_submission") or {}

        facts = []
        score = submission.get("kept_score", submission.get("score"))
        if score is not None:
            possible = quiz.get("points_possible")
            facts.append(
                f"**Your score:** {score}" + (f" / {possible}" if possible is not None else "")
            )
        if quiz.get("question_count") is not None:
            facts.append(f"{quiz['question_count']} questions")
        if submission.get("attempt"):
            facts.append(f"attempt {submission['attempt']}")
        if submission.get("finished_at"):
            facts.append(f"taken {fmt_date(submission['finished_at'], with_time=False)}")
        if facts:
            lines += ["", " · ".join(facts)]

        description = _body(quiz.get("description"))
        if description:
            lines += ["", description]

        questions = quiz.get("_questions") or []
        if questions:
            lines += ["", "### Questions", ""]
            for index, question in enumerate(questions, 1):
                lines.append(f"**{index}. {_body(question.get('question_text')) or ''}**")
                answer = question.get("answer")
                if answer not in (None, ""):
                    lines += ["", f"Your answer: {answer}"]
                lines.append("")
        elif submission:
            lines += [
                "",
                "*The questions and answers aren't available — your instructor "
                "restricted access to them after the quiz closed. Your score above "
                "is the full record Canvas will release.*",
            ]
        lines.append("")
    return _render(lines)


def modules_md(
    course_name: str,
    modules: list[dict],
    *,
    file_links: dict | None = None,
    page_links: dict | None = None,
    section_links: dict | None = None,
) -> str:
    """The course structure -- the closest thing to a table of contents.

    Each item links to whatever was actually archived for it. Listing a file by name
    without linking to the copy sitting one folder away is the sort of thing that makes
    an archive feel broken.
    """
    file_links = file_links or {}
    page_links = page_links or {}
    section_links = section_links or {}
    lines = [f"# Course structure — {course_name}", ""]
    labels = {
        "File": "file",
        "Page": "page",
        "ExternalUrl": "link",
        "Assignment": "task",
        "Quiz": "quiz",
        "Discussion": "discussion",
    }

    for module in modules:
        lines += [f"## {module.get('name') or 'Module'}", ""]
        for item in module.get("items") or []:
            kind = item.get("type") or ""
            title = (item.get("title") or "").strip() or "Untitled"

            if kind == "SubHeader":
                lines.append(f"**{title}**")
                continue

            label = labels.get(kind, kind.lower() or "item")
            target = None
            if kind == "File":
                target = file_links.get(item.get("content_id"))
            elif kind == "Page":
                target = page_links.get(item.get("page_url"))
            elif kind == "ExternalUrl":
                target = safe_href(item.get("external_url"))
            elif kind == "Assignment":
                target = section_links.get("assignments")
            elif kind == "Quiz":
                target = section_links.get("quizzes")
            elif kind == "Discussion":
                target = section_links.get("discussions")

            if target and kind in {"Assignment", "Quiz", "Discussion"}:
                target = f"{target}#{heading_id(title)}"

            if target:
                lines.append(f"- *{label}* · [{title}]({quote(str(target), safe='/:?=&#')})")
            else:
                lines.append(f"- *{label}* · {title}")
        lines.append("")
    return _render(lines)


def page_md(page: dict) -> str:
    """One Canvas page as its own readable leaf."""
    lines = [f"# {page.get('title') or 'Untitled'}", ""]
    if page.get("updated_at"):
        lines += [f"*Updated {fmt_date(page['updated_at'], with_time=False)}*", ""]
    body = _body(page.get("body"))
    if body:
        lines += [body, ""]
    return _render(lines)


def pages_md(course_name: str, pages: list[dict], *, links: dict | None = None) -> str:
    """A TOC of pages. Bodies live on the per-page leaves."""
    links = links or {}
    lines = [f"# Pages — {course_name}", ""]
    if not pages:
        lines += ["*No pages were archived.*", ""]
        return _render(lines)
    lines += ["| Page | Updated |", "|---|---|"]
    for page in pages:
        title = (page.get("title") or "Untitled").replace("|", "\\|")
        href = links.get(page.get("url") or page.get("page_url"))
        date = fmt_date(page.get("updated_at"), with_time=False) or "—"
        cell = f"[{title}](./{quote(href)})" if href else title
        lines.append(f"| {cell} | {date} |")
    lines.append("")
    return _render(lines)


def archive_index(user: dict, host: str, courses: list[dict[str, Any]]) -> str:
    """The front door: every course, its grade, and a way in.

    The course name is itself the link. An extra column holding the word "open" only
    costs width that the names need.
    """
    lines = [
        "# Canvas Archive",
        "",
        f"**{user.get('name') or 'Student'}** · {host}",
        "",
        f"{len(courses)} courses archived.",
        "",
        "| Course | Grade |",
        "|---|---|",
    ]
    for entry in sorted(courses, key=lambda c: c["name"].lower()):
        grade = entry.get("grade") or "—"
        folder = quote(entry["folder"])
        name = entry["name"].strip().replace("|", "\\|")
        lines.append(f"| [{name}](./courses/{folder}/README.md) | {grade} |")
    lines += [
        "",
        "---",
        "",
        "Each course folder holds a `README.md` overview, the readable Markdown "
        "versions of its content, the original files, and the raw JSON the data "
        "came from.",
        "",
    ]
    return _render(lines)
