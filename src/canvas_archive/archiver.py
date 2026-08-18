"""Module-first Canvas archiver.

Traversal is deliberately inverted from the obvious design. On locked-down instances
`/courses/:id/files` returns 403 and `/courses/:id/pages` returns 404 "disabled", yet
the individual files and pages behind *module items* resolve fine. So modules are the
primary route and the collection indexes are an opportunistic bonus.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .http.client import CanvasClient
from .paths import as_url_path, fs_path
from .render import html as site
from .render import markdown as md
from .sanitize import safe_component, unique_component

log = logging.getLogger(__name__)

# Canvas file URLs carry a `verifier` capability token: anyone holding the URL can
# download the file without logging in. They appear not just in file metadata but
# inline in announcement bodies, page bodies and assignment descriptions, so the scrub
# has to walk everything we persist -- someone will eventually share this folder.
_VERIFIER = re.compile(r"(?:&amp;|[?&])verifier=[A-Za-z0-9._~-]+", re.I)


# Images embedded in Canvas HTML bodies point back at course files by URL. Left alone
# they turn the "offline" viewer into one that needs the internet -- and needs the
# account that is about to be revoked.
_INLINE_IMAGE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]*?/courses/(\d+)/files/(\d+)[^)\s]*)\)")


def strip_verifier(text: str | None) -> str | None:
    """Remove verifier capability tokens from a URL or from HTML containing URLs."""
    if not text:
        return text
    cleaned = _VERIFIER.sub("", text)
    # A URL whose only parameter was the verifier is left with a dangling separator.
    return re.sub(r"[?&]$", "", cleaned)


def scrub(value: Any) -> Any:
    """Recursively strip verifier tokens from every string in a payload."""
    if isinstance(value, str):
        return strip_verifier(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def write_json(path: Path, payload: Any) -> None:
    fs_path(path.parent).mkdir(parents=True, exist_ok=True)
    fs_path(path).write_text(
        json.dumps(scrub(payload), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def write_html(path: Path, body: str) -> None:
    fs_path(path.parent).mkdir(parents=True, exist_ok=True)
    fs_path(path).write_text(strip_verifier(body) or "", encoding="utf-8")


def write_md(path: Path, text: str) -> None:
    """Readable companion to the JSON. Never the source of truth, always the front door."""
    if not text or not text.strip():
        return
    fs_path(path.parent).mkdir(parents=True, exist_ok=True)
    fs_path(path).write_text(strip_verifier(text) or "", encoding="utf-8")


@dataclass
class Stats:
    files_downloaded: int = 0
    files_skipped: int = 0
    bytes_downloaded: int = 0
    pages: int = 0
    courses: int = 0
    html_pages: int = 0
    submissions: int = 0
    submission_files: int = 0
    submission_bytes: int = 0
    quizzes: int = 0
    discussion_posts: int = 0
    inline_images: int = 0
    json_records: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    recovered: int = 0


def course_dirname(course: dict) -> str:
    """`<name-slug>__<code>__<id>`.

    The Canvas id is always present, which makes collisions structurally impossible --
    unlike truncation-based schemes. Term is deliberately not a parent directory: many
    instances report "Default Term" for every course, collapsing the whole tree.
    """
    name = safe_component(course.get("name") or "course", max_bytes=80)
    code = safe_component(course.get("course_code") or "", max_bytes=40, fallback="")
    parts = [name] + ([code] if code else []) + [str(course["id"])]
    return "__".join(parts)


class Archiver:
    def __init__(
        self,
        client: CanvasClient,
        out: Path,
        *,
        content: set[str] | None = None,
        progress=None,
        build_html: bool = True,
    ) -> None:
        self.client = client
        self.out = out
        self.progress = progress
        self.build_html = build_html
        self.stats = Stats()
        self._failed: list[tuple[int, Path, dict, str]] = []
        self._index: list[dict] = []
        self._graded: dict[int, list[dict]] = {}
        self.content = content or {
            "files",
            "pages",
            "modules",
            "grades",
            "submissions",
            "assignments",
            "announcements",
            "discussions",
            "syllabus",
            "quizzes",
        }

    def want(self, key: str) -> bool:
        return key in self.content

    async def run(self, course_filter: set[int] | None = None) -> Stats:
        self.out.mkdir(parents=True, exist_ok=True)

        user = await self.client.get("users/self")
        write_json(self.out / "user" / "profile.json", user)
        log.info("signed in as %s (id %s)", user.get("name"), user.get("id"))

        enrollments = [
            e
            async for e in self.client.paginate(
                "users/self/enrollments", **{"state[]": ["active", "completed"]}
            )
        ]
        write_json(self.out / "user" / "enrollments.json", enrollments)
        grades_by_course = {
            e["course_id"]: e.get("grades") for e in enrollments if e.get("course_id")
        }

        courses: list[dict] = []
        seen: set[int] = set()
        for state in ("active", "completed"):
            async for course in self.client.paginate(
                "courses", enrollment_state=state, **{"include[]": ["term", "syllabus_body"]}
            ):
                cid = course.get("id")
                if cid and cid not in seen and course.get("name"):
                    seen.add(cid)
                    courses.append(course)

        if course_filter:
            courses = [c for c in courses if c["id"] in course_filter]

        log.info("archiving %d courses", len(courses))
        if self.progress:
            self.progress.start_courses(len(courses))

        for index, course in enumerate(courses, 1):
            log.info("[%d/%d] %s", index, len(courses), course.get("name"))
            if self.progress:
                self.progress.start_course(course.get("name") or "course", index, len(courses))
            try:
                await self.archive_course(course, grades_by_course.get(course["id"]))
                self.stats.courses += 1
            except Exception as exc:  # keep going -- one bad course must not end the run
                log.error("course %s failed: %s", course.get("id"), exc)
                self.stats.errors.append(f"course {course.get('id')}: {exc}")
            finally:
                if self.progress:
                    self.progress.finish_course()

        await self.retry_failed()

        write_md(self.out / "README.md", md.archive_index(user, self.client.base_url, self._index))

        if self.build_html:
            # Built last, so it renders every Markdown file the run produced.
            try:
                self.stats.html_pages = site.build_site(self.out)
            except Exception as exc:
                log.warning("HTML viewer generation failed: %s", exc)

        write_json(
            self.out / "archive.json",
            {
                "canvas_host": self.client.base_url,
                "user_id": user.get("id"),
                "user_name": user.get("name"),
                "courses": len(courses),
                "content": sorted(self.content),
            },
        )
        return self.stats

    async def archive_course(self, course: dict, grades: dict | None) -> None:
        cdir = self.out / "courses" / course_dirname(course)
        cdir.mkdir(parents=True, exist_ok=True)
        cid = course["id"]
        counts: dict[str, int] = {}

        write_json(cdir / "course.json", course)

        if self.want("syllabus") and course.get("syllabus_body"):
            write_html(cdir / "syllabus.html", course["syllabus_body"])

        if self.want("grades") and grades:
            write_json(cdir / "grades" / "grades.json", grades)
            self.stats.json_records["grades"] += 1

        modules = await self.archive_modules(cid, cdir, counts)
        if modules:
            write_md(
                cdir / "modules" / "modules.md", md.modules_md(course.get("name") or "", modules)
            )

        for key, path, params in (
            ("assignments", "assignments", {}),
            ("announcements", "discussion_topics", {"only_announcements": "true"}),
        ):
            if not self.want(key):
                continue
            items = [i async for i in self.client.paginate(f"courses/{cid}/{path}", **params)]
            if not items:
                continue
            write_json(cdir / key / f"{key}.json", items)
            self.stats.json_records[key] += len(items)
            counts[key] = len(items)

            name = course.get("name") or ""
            renderer = {
                "assignments": md.assignments_md,
                "announcements": md.announcements_md,
            }[key]
            write_md(cdir / key / f"{key}.md", renderer(name, items))

        # Each exporter is isolated: a course must not lose its files because its
        # quizzes endpoint misbehaved.
        async def step(key: str, coro):
            if not self.want(key):
                return
            try:
                result = await coro
            except Exception as exc:
                log.warning("%s failed for course %s: %s", key, cid, exc)
                self.stats.errors.append(f"course {cid} {key}: {exc}")
                return
            if isinstance(result, int):
                counts[key] = result

        await step("discussions", self.archive_discussions(cid, cdir, course))
        await step("quizzes", self.archive_quizzes(cid, cdir, course, modules))
        await step("submissions", self.archive_submissions(cid, cdir))
        await step("files", self.archive_files(cid, cdir, modules))

        if self.want("grades") and grades:
            write_md(
                cdir / "grades" / "grades.md",
                md.grades_md(course.get("name") or "", grades, self._graded.get(cid, [])),
            )

        write_md(cdir / "README.md", md.course_overview(course, grades, counts))

        # Last, so it can rewrite every Markdown file this course produced.
        await self.archive_inline_images(cid, cdir)
        self._index.append(
            {
                "name": course.get("name") or str(cid),
                "folder": course_dirname(course),
                "grade": md._grade_line(grades),
            }
        )

    async def archive_inline_images(self, cid: int, cdir: Path) -> None:
        """Download images embedded in course text and repoint them locally.

        Runs after the content exporters so it sees every Markdown file the course
        produced, and rewrites them in place.
        """
        md_files = list(cdir.rglob("*.md"))
        if not md_files:
            return

        wanted: dict[str, tuple[int, int]] = {}
        for path in md_files:
            for _alt, url, course_id, file_id in _INLINE_IMAGE.findall(
                path.read_text(encoding="utf-8", errors="ignore")
            ):
                wanted[url] = (int(course_id), int(file_id))
        if not wanted:
            return

        media = cdir / "_media"
        local: dict[str, Path] = {}
        taken: set[str] = set()

        for url, (course_id, file_id) in wanted.items():
            meta = await self.client.get_optional(f"courses/{course_id}/files/{file_id}")
            if not meta or not meta.get("url"):
                self.stats.skipped["embedded image not accessible"] += 1
                continue
            name = unique_component(
                safe_component(meta.get("display_name") or f"image-{file_id}"),
                file_id,
                taken,
            )
            try:
                await self.client.download(
                    meta["url"], media / name, expected_size=meta.get("size")
                )
            except Exception as exc:
                log.debug("embedded image %s failed: %s", file_id, exc)
                self.stats.skipped["embedded image not accessible"] += 1
                continue
            local[url] = media / name
            self.stats.inline_images += 1

        if not local:
            return

        for path in md_files:
            text = original = fs_path(path).read_text(encoding="utf-8")

            def repoint(match: re.Match, *, base: Path = path.parent) -> str:
                target = local.get(match.group(2))
                if not target:
                    return match.group(0)
                rel = as_url_path(os.path.relpath(target, base))
                return f"![{match.group(1)}]({quote(rel)})"

            text = _INLINE_IMAGE.sub(repoint, text)
            if text != original:
                fs_path(path).write_text(text, encoding="utf-8")

    async def archive_discussions(self, cid: int, cdir: Path, course: dict) -> int:
        """Discussion topics with their full reply threads.

        `/discussion_topics/:id/view` returns participants and the whole nested thread
        in one call. It can 403 when the student never posted, so each topic falls back
        to walking entries and their replies.
        """
        topics = [t async for t in self.client.paginate(f"courses/{cid}/discussion_topics")]
        if not topics:
            return 0

        enriched = []
        for topic in topics:
            record = dict(topic)
            view = await self.client.get_optional(
                f"courses/{cid}/discussion_topics/{topic['id']}/view"
            )
            if view and isinstance(view, dict):
                record["_view"] = view
                record["_entries"] = view.get("view") or []
                record["_participants"] = view.get("participants") or []
            else:
                entries = [
                    e
                    async for e in self.client.paginate(
                        f"courses/{cid}/discussion_topics/{topic['id']}/entries"
                    )
                ]
                for entry in entries:
                    replies = await self.client.get_optional(
                        f"courses/{cid}/discussion_topics/{topic['id']}"
                        f"/entries/{entry['id']}/replies"
                    )
                    entry["replies"] = replies or []
                record["_entries"] = entries
            self.stats.discussion_posts += len(record.get("_entries") or [])
            enriched.append(record)

        write_json(cdir / "discussions" / "discussions.json", enriched)
        self.stats.json_records["discussions"] += len(enriched)
        write_md(
            cdir / "discussions" / "discussions.md",
            md.discussions_md(course.get("name") or "", enriched),
        )
        return len(enriched)

    async def archive_quizzes(self, cid: int, cdir: Path, course: dict, modules: list[dict]) -> int:
        """Quiz metadata and your own scores.

        The quiz *index* is often disabled, so ids also come from module items -- the
        same module-first fallback the rest of the tool relies on.

        Question and answer text is frequently unavailable to students: instructors
        restrict it via one-question-at-a-time or hide_results, and the API then 401s.
        That is an instructor setting, not a failure, so it is reported as a skip.
        """
        quizzes = {
            q["id"]: q async for q in self.client.paginate(f"courses/{cid}/quizzes") if q.get("id")
        }

        for module in modules:
            for item in module.get("items") or []:
                if item.get("type") == "Quiz" and item.get("content_id") not in quizzes:
                    meta = await self.client.get_optional(
                        f"courses/{cid}/quizzes/{item['content_id']}"
                    )
                    if meta and meta.get("id"):
                        quizzes[meta["id"]] = meta

        if not quizzes:
            return 0

        records = []
        for quiz_id, quiz in quizzes.items():
            record = dict(quiz)
            payload = await self.client.get_optional(
                f"courses/{cid}/quizzes/{quiz_id}/submission",
                **{"include[]": ["submission", "quiz"]},
            )
            submissions = (payload or {}).get("quiz_submissions") or []
            if submissions:
                record["_submission"] = submissions[0]
                questions = await self.client.get_optional(
                    f"quiz_submissions/{submissions[0]['id']}/questions"
                )
                if questions and questions.get("quiz_submission_questions"):
                    record["_questions"] = questions["quiz_submission_questions"]
                else:
                    self.stats.skipped["quiz questions restricted by instructor"] += 1
            records.append(record)
            self.stats.quizzes += 1

        write_json(cdir / "quizzes" / "quizzes.json", records)
        write_md(cdir / "quizzes" / "quizzes.md", md.quizzes_md(course.get("name") or "", records))
        return len(records)

    async def archive_submissions(self, cid: int, cdir: Path) -> None:
        """Your own submitted work, instructor comments and rubric scores.

        One call carries assignment, comments, rubric assessment and every prior
        attempt, so there is no N+1 walk over assignments.

        Note the shape of the loop: a submission is recorded whether or not it has
        attachments. Text-entry and URL submissions have no `attachments` key at all,
        and a structure that only records them inside an attachments branch loses them
        silently -- which is exactly the bug in the tool this replaces.
        """
        params = {
            "student_ids[]": "self",
            "include[]": [
                "assignment",
                "submission_comments",
                "rubric_assessment",
                "submission_history",
            ],
        }
        submissions = [
            s async for s in self.client.paginate(f"courses/{cid}/students/submissions", **params)
        ]

        if not submissions:
            # Some instances reject the rubric include; retry without it before
            # concluding the student genuinely has nothing.
            params["include[]"] = ["assignment", "submission_comments"]
            submissions = [
                s
                async for s in self.client.paginate(f"courses/{cid}/students/submissions", **params)
            ]
            if not submissions:
                self.stats.skipped["submissions not available"] += 1
                return

        real = [s for s in submissions if s.get("workflow_state") != "unsubmitted"]
        if not real:
            return

        write_json(cdir / "submissions" / "submissions.json", submissions)
        self.stats.submissions += len(real)
        self._graded[cid] = real

        taken_dirs: set[str] = set()
        for submission in real:
            await self._save_submission(cid, cdir, submission, taken_dirs)

    async def _save_submission(
        self, cid: int, cdir: Path, submission: dict, taken_dirs: set[str]
    ) -> None:
        assignment = submission.get("assignment") or {}
        title = assignment.get("name") or f"assignment-{submission.get('assignment_id')}"
        folder = unique_component(
            safe_component(title, max_bytes=90),
            submission.get("assignment_id", "x"),
            taken_dirs,
        )
        base = cdir / "submissions" / folder
        base.mkdir(parents=True, exist_ok=True)

        write_json(base / "submission.json", submission)
        write_md(base / "README.md", md.submission_md(submission))

        # A text-entry submission is the actual work; it exists only as HTML.
        if submission.get("body"):
            write_html(base / "submission.html", submission["body"])

        # Instructor feedback, saved plainly so it is readable without digging
        # through JSON. This is the part people most want back years later.
        await self._save_submission_attachments(base, submission)

        # Earlier attempts, kept separately so the latest stays at the top level.
        history = submission.get("submission_history") or []
        for older in history:
            if older.get("attempt") in (None, submission.get("attempt")):
                continue
            if not older.get("attachments") and not older.get("body"):
                continue
            sub = base / f"attempt-{older['attempt']}"
            sub.mkdir(parents=True, exist_ok=True)
            if older.get("body"):
                write_html(sub / "submission.html", older["body"])
            await self._save_submission_attachments(sub, older)

    async def _save_submission_attachments(self, base: Path, submission: dict) -> None:
        taken: set[str] = set()
        for attachment in submission.get("attachments") or []:
            url = attachment.get("url")
            if not url:
                continue
            name = unique_component(
                safe_component(
                    attachment.get("display_name") or attachment.get("filename") or "attachment"
                ),
                attachment.get("id", "x"),
                taken,
            )
            try:
                result = await self.client.download(
                    url, base / name, expected_size=attachment.get("size")
                )
            except Exception as exc:
                log.warning("submission attachment failed (%s): %s", name, exc)
                self.stats.errors.append(f"submission attachment {name}: {exc}")
                continue
            if not result.skipped:
                self.stats.submission_bytes += result.bytes_written
            self.stats.submission_files += 1

    async def archive_modules(self, cid: int, cdir: Path, counts: dict) -> list[dict]:
        modules = [
            m
            async for m in self.client.paginate(f"courses/{cid}/modules", **{"include[]": "items"})
        ]
        if not modules:
            self.stats.skipped["modules unavailable"] += 1
            return []

        if self.want("modules"):
            write_json(cdir / "modules" / "modules.json", modules)
            self.stats.json_records["modules"] += len(modules)
            counts["modules"] = len(modules)

        if self.want("pages"):
            counts["pages"] = await self.archive_pages(cid, cdir, modules)
        return modules

    async def archive_pages(self, cid: int, cdir: Path, modules: list[dict]) -> int:
        """Fetch pages named by module items.

        The /pages *index* is often disabled (404) while individual pages resolve, so
        module items are the only reliable enumeration.
        """
        slugs: list[str] = []
        for module in modules:
            for item in module.get("items") or []:
                if item.get("type") == "Page" and item.get("page_url"):
                    slugs.append(item["page_url"])

        if not slugs:
            return 0

        taken: set[str] = set()
        records = []
        for slug in dict.fromkeys(slugs):
            page = await self.client.get_optional(f"courses/{cid}/pages/{slug}")
            if not page:
                self.stats.skipped["page not accessible"] += 1
                continue
            records.append(page)
            body = page.get("body")
            if body:
                name = unique_component(
                    safe_component(page.get("title") or slug) + ".html", slug, taken
                )
                write_html(cdir / "pages" / name, body)
            self.stats.pages += 1

        if records:
            write_json(cdir / "pages" / "pages.json", records)
            write_md(cdir / "pages" / "pages.md", md.pages_md("", records))
        return len(records)

    async def archive_files(self, cid: int, cdir: Path, modules: list[dict]) -> int:
        """Collect file ids from module items, plus the /files index where permitted."""
        file_ids: list[int] = []
        for module in modules:
            for item in module.get("items") or []:
                if item.get("type") == "File" and item.get("content_id"):
                    file_ids.append(item["content_id"])

        index_files = [f async for f in self.client.paginate(f"courses/{cid}/files")]
        if not index_files:
            self.stats.skipped["/files index denied"] += 1

        by_id: dict[int, dict] = {f["id"]: f for f in index_files if f.get("id")}
        missing = [fid for fid in dict.fromkeys(file_ids) if fid not in by_id]

        async def fetch(fid: int) -> None:
            meta = await self.client.get_optional(f"courses/{cid}/files/{fid}")
            if meta and meta.get("id"):
                by_id[meta["id"]] = meta
            else:
                self.stats.skipped["file metadata denied"] += 1

        await asyncio.gather(*(fetch(fid) for fid in missing))

        if not by_id:
            return 0

        taken: set[str] = set()
        records = []
        for meta in by_id.values():
            records.append(meta)
            await self.download_file(cid, cdir, meta, taken)

        write_json(cdir / "files" / "files.json", records)
        return len(records)

    async def download_file(self, cid: int, cdir: Path, meta: dict, taken: set[str]) -> None:
        url = meta.get("url")
        if not url:
            self.stats.skipped["file has no url"] += 1
            return

        name = unique_component(
            safe_component(meta.get("display_name") or meta.get("filename") or "file"),
            meta.get("id", "x"),
            taken,
        )
        await self._fetch_one(cid, cdir, meta, name, first_pass=True)

    async def _fetch_one(
        self, cid: int, cdir: Path, meta: dict, name: str, *, first_pass: bool
    ) -> bool:
        dest = cdir / "files" / name
        size = meta.get("size")

        if self.progress:
            self.progress.start_file(name, size)

        async def refresh() -> str | None:
            fresh = await self.client.get_optional(f"courses/{cid}/files/{meta['id']}")
            return fresh.get("url") if fresh else None

        try:
            result = await self.client.download(
                meta["url"],
                dest,
                expected_size=size,
                refresh=refresh,
                on_bytes=self.progress.advance_bytes if self.progress else None,
            )
        except Exception as exc:
            if first_pass:
                # Hold it for the calm second pass rather than failing the run.
                self._failed.append((cid, cdir, meta, name))
                log.debug("deferring %s to retry sweep: %s", name, exc)
            else:
                log.warning("download failed for %s: %s", name, exc)
                self.stats.errors.append(f"{name}: {exc}")
            if self.progress:
                self.progress.finish_file(failed=True)
            return False

        if result.skipped:
            self.stats.files_skipped += 1
            if self.progress and size:
                self.progress.advance_bytes(size)
        else:
            self.stats.files_downloaded += 1
            self.stats.bytes_downloaded += result.bytes_written

        if self.progress:
            self.progress.finish_file()
        return True

    async def retry_failed(self) -> None:
        """Calm second pass over everything that failed.

        Concurrency is forced to 1: most first-pass failures are throttling, and the
        fix for throttling is to stop being in a hurry. Partial files are preserved,
        so a retry resumes rather than restarts.
        """
        if not self._failed:
            return

        pending, self._failed = self._failed, []
        log.info("retrying %d file(s) that failed", len(pending))
        if self.progress:
            self.progress.start_retry(len(pending))

        original = self.client.throttle.max_concurrency
        self.client.throttle.max_concurrency = 1
        try:
            for cid, cdir, meta, name in pending:
                if await self._fetch_one(cid, cdir, meta, name, first_pass=False):
                    self.stats.recovered += 1
        finally:
            self.client.throttle.max_concurrency = original
