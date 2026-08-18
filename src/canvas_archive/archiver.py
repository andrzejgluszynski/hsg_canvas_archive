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
from html import unescape
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
_VERIFIER = re.compile(r"(?:&amp;|[?&#])verifier=[A-Za-z0-9._~-]+", re.I)


# Canvas text refers to course files by URL, as embedded images and as ordinary
# links. Left alone they turn the "offline" viewer into one that needs the internet
# -- and needs the account that is about to be revoked.
#
# Links matter at least as much as images: syllabus PDFs, reading lists and handouts
# are routinely linked from a page or announcement and never placed in a module, so
# module-first traversal alone never sees them.
_FILE_REF = re.compile(r"(!?)\[([^\]]*)\]\((https?://[^)\s]*?/courses/(\d+)/files/(\d+)[^)\s]*)\)")

# Canvas also links sideways -- to a module, an assignment, another page, the course
# home. Those URLs die with the account, so they are repointed at the local copies we
# already hold. Anything we cannot resolve locally is left alone rather than broken.
_NAV_REF = re.compile(
    r"(?<!\!)\[([^\]]*)\]\((https?://[^)\s]*?/courses/(\d+)"
    r"(/(?:modules|assignments|pages|announcements|discussion_topics|quizzes|grades)"
    r"[^)\s]*)?)\)"
)

# Where each kind of Canvas page lives inside a course folder.
_NAV_TARGETS = {
    "modules": "modules/modules.md",
    "assignments": "assignments/assignments.md",
    "pages": "pages/pages.md",
    "announcements": "announcements/announcements.md",
    "discussion_topics": "discussions/discussions.md",
    "quizzes": "quizzes/quizzes.md",
    "grades": "grades/grades.md",
}


def strip_verifier(text: str | None) -> str | None:
    """Remove verifier capability tokens from a URL or from HTML containing URLs."""
    if not text:
        return text
    cleaned = unescape(text)
    # Percent-encoded "verifier" (e.g. %76erifier=) is still a capability token.
    cleaned = re.sub(r"%76erifier=", "verifier=", cleaned, flags=re.I)
    cleaned = _VERIFIER.sub("", cleaned)
    # A URL whose only parameter was the verifier is left with a dangling separator.
    return re.sub(r"[?&#]$", "", cleaned)


def scrub(value: Any) -> Any:
    """Recursively strip verifier tokens from every string in a payload."""
    if isinstance(value, str):
        return strip_verifier(value)
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def _atomic_write(path: Path, text: str) -> None:
    parent = fs_path(path.parent)
    parent.mkdir(parents=True, exist_ok=True)
    dest = fs_path(path)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)


def write_json(path: Path, payload: Any) -> None:
    _atomic_write(path, json.dumps(scrub(payload), indent=2, ensure_ascii=False))


def write_md(path: Path, text: str) -> None:
    """Readable companion to the JSON. Never the source of truth, always the front door."""
    if not text or not text.strip():
        return
    _atomic_write(path, strip_verifier(text) or "")


def _read_json_list(path: Path) -> list:
    if not fs_path(path).exists():
        return []
    try:
        data = json.loads(fs_path(path).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def merge_files_json(path: Path, records: list[dict]) -> None:
    """Union by Canvas id so a later pass cannot drop files another pass saved."""
    by_id: dict[int, dict] = {}
    for rec in _read_json_list(path) + records:
        if isinstance(rec, dict) and rec.get("id") is not None:
            by_id[rec["id"]] = rec
    if by_id:
        write_json(path, list(by_id.values()))


def _disk_name_candidates(display_name: str | None, file_id: int) -> list[str]:
    base = safe_component(display_name or f"file-{file_id}")
    stem, dot, ext = base.rpartition(".")
    suffixed = f"{stem}-{file_id}.{ext}" if dot and stem else f"{base}-{file_id}"
    # Id-suffixed name first: it can only be this Canvas file.
    return [suffixed, base]


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
    linked_files: int = 0
    linked_bytes: int = 0
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
        course_workers: int = 0,
    ) -> None:
        self.client = client
        self.out = out
        self.progress = progress
        self.build_html = build_html
        # 0 means every course at once. The real ceilings are the HTTP client's two
        # pools -- the API rate-limit governor and the download pool -- which every
        # request passes through regardless of how many courses are in flight. Bounding
        # courses as well only helps if you want to cap memory or tidy the display.
        self.course_workers = max(0, course_workers)
        self.stats = Stats()
        self._failed: list[tuple[int, Path, dict, str]] = []
        self._index: list[dict] = []
        self._graded: dict[int, list[dict]] = {}
        # Where each module item's content actually landed, so the structure page can
        # link to it. Populated as files and pages are archived.
        self._file_links: dict[int, dict] = {}
        self._page_links: dict[int, dict] = {}
        self._submission_links: dict[int, dict] = {}
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

    async def run(self, course_filter: set[int] | None = None, user: dict | None = None) -> Stats:
        self.out.mkdir(parents=True, exist_ok=True)

        user = user or await self.client.get("users/self")
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
                if cid and cid not in seen:
                    if not course.get("name"):
                        course = {**course, "name": f"course-{cid}"}
                        log.warning("course %s has no name, using %s", cid, course["name"])
                    seen.add(cid)
                    courses.append(course)

        if course_filter:
            available = ", ".join(str(c["id"]) for c in courses) or "none"
            courses = [c for c in courses if c["id"] in course_filter]
            if not courses:
                raise SystemExit(f"No matching courses for --course. Available ids: {available}")

        log.info("archiving %d courses", len(courses))
        if self.progress:
            self.progress.start_courses(len(courses))

        gate = asyncio.Semaphore(self.course_workers or len(courses) or 1)

        async def one(course: dict) -> None:
            async with gate:
                handle = (
                    self.progress.add_course(course.get("name") or "course")
                    if self.progress
                    else None
                )
                try:
                    await self.archive_course(course, grades_by_course.get(course["id"]), handle)
                    self.stats.courses += 1
                except Exception as exc:  # one bad course must not end the run
                    log.error("course %s failed: %s", course.get("id"), exc)
                    self.stats.errors.append(f"course {course.get('id')}: {exc}")
                finally:
                    if self.progress:
                        self.progress.finish_course(handle)

        await asyncio.gather(*(one(c) for c in courses))

        # Deterministic output regardless of the order courses happened to finish in.
        self._index.sort(key=lambda e: e["name"].lower())

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

    async def _download(
        self,
        url: str,
        dest: Path,
        *,
        size: int | None = None,
        refresh=None,
    ):
        """Download a file and keep the byte counter honest.

        Every download in the archive goes through here. The byte total is learned as
        sizes are discovered rather than pre-scanned, and an already-present file still
        advances the bar -- otherwise a resumed run sits at zero while clearly working.
        """
        if self.progress and size:
            self.progress.add_bytes_total(size)

        result = await self.client.download(
            url,
            dest,
            expected_size=size,
            refresh=refresh,
            on_bytes=self.progress.advance_bytes if self.progress else None,
        )

        if result.skipped and self.progress and size:
            self.progress.advance_bytes(size)
        return result

    def _note(self, handle: object, step: str) -> None:
        if self.progress and handle is not None:
            self.progress.set_step(handle, step)

    async def archive_course(
        self, course: dict, grades: dict | None, handle: object = None
    ) -> None:
        cdir = self.out / "courses" / course_dirname(course)
        cdir.mkdir(parents=True, exist_ok=True)
        cid = course["id"]
        counts: dict[str, int] = {}

        write_json(cdir / "course.json", course)

        if self.want("grades") and grades:
            write_json(cdir / "grades" / "grades.json", grades)
            self.stats.json_records["grades"] += 1

        self._note(handle, "modules")
        try:
            modules = await self.archive_modules(
                cid, cdir, counts, course_name=course.get("name") or ""
            )
        except Exception as exc:
            # Modules are the backbone of the traversal, but losing them must not cost
            # the course its grades, submissions and announcements too.
            log.warning("modules failed for course %s: %s", cid, exc)
            self.stats.errors.append(f"course {cid} modules: {exc}")
            modules = []

        assignment_items: list[dict] = []

        async def simple(key: str, path: str, params: dict) -> int:
            items = [i async for i in self.client.paginate(f"courses/{cid}/{path}", **params)]
            if not items:
                return 0
            write_json(cdir / key / f"{key}.json", items)
            self.stats.json_records[key] += len(items)
            if key == "assignments":
                assignment_items.extend(items)
                return len(items)
            write_md(cdir / key / f"{key}.md", md.announcements_md(course.get("name") or "", items))
            return len(items)

        # Each exporter is isolated: a course must not lose its files because its
        # quizzes endpoint misbehaved.
        async def step(key: str, factory) -> None:
            if not self.want(key):
                return
            self._note(handle, key)
            try:
                result = await factory()
            except Exception as exc:
                log.warning("%s failed for course %s: %s", key, cid, exc)
                self.stats.errors.append(f"course {cid} {key}: {exc}")
                return
            if isinstance(result, int):
                counts[key] = result

        # These exporters are independent of one another, so they run together. The
        # rate-limit governor still serialises the actual requests.
        await asyncio.gather(
            step("assignments", lambda: simple("assignments", "assignments", {})),
            step(
                "announcements",
                lambda: simple(
                    "announcements", "discussion_topics", {"only_announcements": "true"}
                ),
            ),
            step("discussions", lambda: self.archive_discussions(cid, cdir, course)),
            step("quizzes", lambda: self.archive_quizzes(cid, cdir, course, modules)),
            step("submissions", lambda: self.archive_submissions(cid, cdir)),
            step("files", lambda: self.archive_files(cid, cdir, modules, handle)),
        )

        links = self._submission_links.get(cid, {})
        if assignment_items:
            write_md(
                cdir / "assignments" / "assignments.md",
                md.assignments_md(
                    course.get("name") or "", assignment_items, submission_links=links
                ),
            )

        if modules and self.want("modules"):
            # Written now, not earlier: it links to files and pages that only exist
            # once their exporters have run.
            sections = {}
            if (cdir / "assignments" / "assignments.md").exists():
                sections["assignments"] = "../assignments/assignments.md"
            if (cdir / "quizzes" / "quizzes.md").exists():
                sections["quizzes"] = "../quizzes/quizzes.md"
            if (cdir / "discussions" / "discussions.md").exists():
                sections["discussions"] = "../discussions/discussions.md"
            write_md(
                cdir / "modules" / "modules.md",
                md.modules_md(
                    course.get("name") or "",
                    modules,
                    file_links={k: f"../{v}" for k, v in self._file_links.get(cid, {}).items()},
                    page_links={k: f"../{v}" for k, v in self._page_links.get(cid, {}).items()},
                    section_links=sections,
                ),
            )

        if self.want("grades") and grades:
            write_md(
                cdir / "grades" / "grades.md",
                md.grades_md(
                    course.get("name") or "",
                    grades,
                    self._graded.get(cid, []),
                    submission_links=links,
                ),
            )

        write_md(cdir / "README.md", md.course_overview(course, grades))

        # Last, so it can rewrite every Markdown file this course produced.
        self._note(handle, "linked files")
        await self.archive_referenced_files(cid, cdir)
        self._index.append(
            {
                "name": course.get("name") or str(cid),
                "folder": course_dirname(course),
                "grade": md._grade_line(grades),
            }
        )

    def _known_file_paths(self, cid: int, cdir: Path) -> dict[int, Path]:
        """Canvas file id → path already on disk, so a later pass cannot duplicate it.

        Linked-file extraction used to treat a module file of the same name as a
        collision and save `Slides-79400.pdf` beside `Slides.pdf`.
        """
        known: dict[int, Path] = {}
        for fid, rel in self._file_links.get(cid, {}).items():
            path = cdir / rel
            if fs_path(path).exists():
                known[fid] = path

        index = _read_json_list(cdir / "files" / "files.json")
        display_counts: dict[str, int] = {}
        for rec in index:
            if not isinstance(rec, dict):
                continue
            base = safe_component(
                rec.get("display_name") or rec.get("filename") or f"file-{rec.get('id')}"
            )
            display_counts[base.casefold()] = display_counts.get(base.casefold(), 0) + 1

        folder = cdir / "files"
        for rec in index:
            if not isinstance(rec, dict) or rec.get("id") is None:
                continue
            fid = rec["id"]
            if fid in known:
                continue
            base = safe_component(rec.get("display_name") or rec.get("filename") or f"file-{fid}")
            suffixed, plain = _disk_name_candidates(
                rec.get("display_name") or rec.get("filename"), fid
            )
            if fs_path(folder / suffixed).exists():
                known[fid] = folder / suffixed
            elif display_counts.get(base.casefold(), 0) <= 1 and fs_path(folder / plain).exists():
                known[fid] = folder / plain
        return known

    async def archive_referenced_files(self, cid: int, cdir: Path) -> None:
        """Download every course file referenced from course text, and repoint it.

        Runs after the content exporters so it sees every Markdown file the course
        produced, and rewrites them in place. Embedded images go to `_media/` because
        they are decoration; linked documents go to `files/` alongside the module
        files, because that is what they are.
        """
        md_files = list(cdir.rglob("*.md"))
        if not md_files:
            return

        # url -> (course_id, file_id, is_image)  -- may legitimately be empty; the
        # navigation rewrite below still has work to do.
        wanted: dict[str, tuple[int, int, bool]] = {}
        if self.want("files"):
            for path in md_files:
                text = fs_path(path).read_text(encoding="utf-8", errors="ignore")
                for bang, _label, url, course_id, file_id in _FILE_REF.findall(text):
                    # If the same file is ever embedded, treat it as an image: it has to
                    # render inline, and a document copy would be redundant.
                    is_image = bool(bang) or wanted.get(url, (0, 0, False))[2]
                    wanted[url] = (int(course_id), int(file_id), is_image)

        local: dict[str, Path] = {}
        taken_media: set[str] = set()
        taken_files = {
            p.name.casefold()
            for p in (cdir / "files").glob("*")
            if p.is_file() and p.suffix.lower() not in {".json", ".md", ".html"}
        }
        have_ids = self._known_file_paths(cid, cdir)
        added: list[dict] = []

        for url, (course_id, file_id, is_image) in wanted.items():
            dest_dir = cdir / ("_media" if is_image else "files")
            if file_id in have_ids and not is_image:
                local[url] = have_ids[file_id]
                continue

            meta = await self.client.get_optional(f"courses/{course_id}/files/{file_id}")
            if not meta or not meta.get("url"):
                self.stats.skipped["linked file not accessible"] += 1
                continue

            base_name = safe_component(meta.get("display_name") or f"file-{file_id}")
            if is_image:
                dest = dest_dir / unique_component(base_name, file_id, taken_media)
            else:
                dest = dest_dir / unique_component(base_name, file_id, taken_files)

            try:
                result = await self._download(meta["url"], dest, size=meta.get("size"))
            except Exception as exc:
                log.debug("referenced file %s failed: %s", file_id, exc)
                self.stats.skipped["linked file not accessible"] += 1
                continue

            local[url] = dest
            if is_image:
                self.stats.inline_images += 1
            else:
                self.stats.linked_files += 1
                added.append(meta)
                have_ids[file_id] = dest
                self._file_links.setdefault(cid, {}).setdefault(file_id, f"files/{dest.name}")
                if not result.skipped:
                    self.stats.linked_bytes += result.bytes_written

        if added:
            merge_files_json(cdir / "files" / "files.json", added)

        for path in md_files:
            text = original = fs_path(path).read_text(encoding="utf-8")

            def repoint(match: re.Match, *, base: Path = path.parent) -> str:
                target = local.get(match.group(3))
                if not target:
                    return match.group(0)
                rel = as_url_path(os.path.relpath(target, base))
                return f"{match.group(1)}[{match.group(2)}]({quote(rel)})"

            text = _FILE_REF.sub(repoint, text)
            text = self._repoint_navigation(text, cdir, path, cid)
            if text != original:
                fs_path(path).write_text(text, encoding="utf-8")

    def _repoint_navigation(self, text: str, cdir: Path, path: Path, cid: int) -> str:
        """Point sideways Canvas links at the local copies of those pages."""

        def swap(match: re.Match) -> str:
            label, _url, course_id, tail = match.groups()
            if int(course_id) != cid:
                return match.group(0)  # another course: we may not even have it
            if not tail:
                target = cdir / "README.md"
            else:
                kind = tail.lstrip("/").split("/")[0].split("?")[0]
                rel = _NAV_TARGETS.get(kind)
                if not rel:
                    return match.group(0)
                target = cdir / rel
            if not fs_path(target).exists():
                return match.group(0)  # nothing local to point at; leave the URL alone
            local = as_url_path(os.path.relpath(target, path.parent))
            return f"[{label}]({quote(local)})"

        return _NAV_REF.sub(swap, text)

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
        aid = submission.get("assignment_id") or assignment.get("id")
        self._submission_links.setdefault(cid, {})[aid] = f"submissions/{folder}"

        await self._save_submission_attachments(base, submission)

        history = submission.get("submission_history") or []
        for older in history:
            if older.get("attempt") in (None, submission.get("attempt")):
                continue
            if not older.get("attachments") and not older.get("body"):
                continue
            sub = base / f"attempt-{older['attempt']}"
            sub.mkdir(parents=True, exist_ok=True)
            write_md(sub / "README.md", md.submission_md(older))
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
                result = await self._download(url, base / name, size=attachment.get("size"))
            except Exception as exc:
                log.warning("submission attachment failed (%s): %s", name, exc)
                self.stats.errors.append(f"submission attachment {name}: {exc}")
                continue
            if not result.skipped:
                self.stats.submission_bytes += result.bytes_written
            self.stats.submission_files += 1

    async def archive_modules(
        self, cid: int, cdir: Path, counts: dict, *, course_name: str = ""
    ) -> list[dict]:
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
            counts["pages"] = await self.archive_pages(cid, cdir, modules, course_name=course_name)
        return modules

    async def archive_pages(
        self, cid: int, cdir: Path, modules: list[dict], *, course_name: str = ""
    ) -> int:
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
        toc_links: dict[str, str] = {}
        for slug in dict.fromkeys(slugs):
            page = await self.client.get_optional(f"courses/{cid}/pages/{slug}")
            if not page:
                self.stats.skipped["page not accessible"] += 1
                continue
            records.append(page)
            name = unique_component(safe_component(page.get("title") or slug) + ".md", slug, taken)
            write_md(cdir / "pages" / name, md.page_md(page))
            self._page_links.setdefault(cid, {})[slug] = f"pages/{name}"
            toc_links[page.get("url") or slug] = name
            toc_links[slug] = name
            self.stats.pages += 1

        if records:
            write_json(cdir / "pages" / "pages.json", records)
            write_md(
                cdir / "pages" / "pages.md",
                md.pages_md(course_name, records, links=toc_links),
            )
        return len(records)

    async def archive_files(
        self, cid: int, cdir: Path, modules: list[dict], handle: object = None
    ) -> int:
        """Collect file ids from module items, plus the /files index where permitted."""
        file_ids: list[int] = []
        for module in modules:
            for item in module.get("items") or []:
                if item.get("type") == "File" and item.get("content_id"):
                    file_ids.append(item["content_id"])

        index_files = await self.client.collect(f"courses/{cid}/files")
        if index_files is None:
            self.stats.skipped["/files index denied"] += 1
            index_files = []

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

        if self.progress and handle is not None:
            self.progress.add_files(handle, len(by_id))

        taken: set[str] = set()
        records = []
        for meta in by_id.values():
            records.append(meta)
            await self.download_file(cid, cdir, meta, taken)
            if self.progress and handle is not None:
                self.progress.file_done(handle)

        merge_files_json(cdir / "files" / "files.json", records)
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
        self._file_links.setdefault(cid, {})[meta.get("id")] = f"files/{name}"
        await self._fetch_one(cid, cdir, meta, name, first_pass=True)

    async def _fetch_one(
        self, cid: int, cdir: Path, meta: dict, name: str, *, first_pass: bool
    ) -> bool:
        dest = cdir / "files" / name
        size = meta.get("size")

        async def refresh() -> str | None:
            fresh = await self.client.get_optional(f"courses/{cid}/files/{meta['id']}")
            return fresh.get("url") if fresh else None

        try:
            result = await self._download(meta["url"], dest, size=size, refresh=refresh)
        except Exception as exc:
            if first_pass:
                # Hold it for the calm second pass rather than failing the run.
                self._failed.append((cid, cdir, meta, name))
                log.debug("deferring %s to retry sweep: %s", name, exc)
            else:
                log.warning("download failed for %s: %s", name, exc)
                self.stats.errors.append(f"{name}: {exc}")
            return False

        if result.skipped:
            self.stats.files_skipped += 1
        else:
            self.stats.files_downloaded += 1
            self.stats.bytes_downloaded += result.bytes_written
        return True

    async def retry_failed(self) -> None:
        """Calm second pass over everything that failed.

        Partial files are preserved, so a retry resumes rather than restarts.
        """
        if not self._failed:
            return

        pending, self._failed = self._failed, []
        log.info("retrying %d file(s) that failed", len(pending))
        if self.progress:
            self.progress.start_retry(len(pending))

        for cid, cdir, meta, name in pending:
            if await self._fetch_one(cid, cdir, meta, name, first_pass=False):
                self.stats.recovered += 1
