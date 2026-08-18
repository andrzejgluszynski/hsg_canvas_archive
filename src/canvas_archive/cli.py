"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .archiver import Archiver
from .auth import (
    ENV_VAR,
    default_institution,
    normalize_base_url,
    redact,
    remember_token,
    resolve_token,
    token_shard,
)
from .http.client import CanvasClient
from .paths import check_path_budget, configure_console
from .tui.progress import human, make_progress
from .tui.wizard import run_wizard

ALL_CONTENT = [
    "modules",
    "files",
    "pages",
    "grades",
    "submissions",
    "assignments",
    "announcements",
    "discussions",
    "quizzes",
    "syllabus",
]


class RedactingFilter(logging.Filter):
    """Tokens must never reach a log file, including via httpx's URL logging."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


def setup_logging(verbose: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    # Quiet by default: the progress display is the interface, and duplicate log lines
    # interleaving with it on a second stream reads as noise to a non-technical user.
    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    inst = default_institution()
    parser = argparse.ArgumentParser(
        prog="canvas-archive",
        description="Archive everything from your Canvas LMS account before you lose access.",
    )
    parser.add_argument(
        "--url",
        default=inst.base_url if inst else None,
        help=f"Canvas URL (default: {inst.base_url if inst else 'required'})",
    )
    parser.add_argument(
        "--token",
        help=(
            f"API token. Prefer ${ENV_VAR} or the setup wizard; "
            "passing it here leaves it in shell history"
        ),
    )
    parser.add_argument(
        "--creds-file",
        type=Path,
        default=Path("creds.txt"),
        help="file containing the API token (default: creds.txt)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path.home() / "CanvasArchive",
        help="output directory (default: ~/CanvasArchive)",
    )
    parser.add_argument("--only", help=f"comma-separated subset of: {','.join(ALL_CONTENT)}")
    parser.add_argument("--skip", help="comma-separated content types to exclude")
    parser.add_argument(
        "--course", action="append", type=int, help="limit to course id (repeatable)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="simultaneous HTTP requests (default: 6)",
    )
    parser.add_argument(
        "--course-workers",
        type=int,
        default=0,
        help="courses archived at the same time (default: 0 = all at once)",
    )
    parser.add_argument(
        "--download-concurrency",
        type=int,
        default=8,
        help="simultaneous file downloads (default: 8)",
    )
    parser.add_argument(
        "--retries", type=int, default=5, help="attempts per file before giving up (default: 5)"
    )
    parser.add_argument("--plain", action="store_true", help="disable the progress bar")
    parser.add_argument("--no-html", action="store_true", help="skip the offline HTML viewer")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="don't open the archive in your browser when it finishes",
    )
    parser.add_argument(
        "--no-wizard", action="store_true", help="never prompt; fail if the token is missing"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def harden_creds_file(path: Path) -> None:
    """Warn and tighten a token file that is group- or world-readable."""
    if not path.exists() or not path.is_file():
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o077:
        print(
            f"  Warning: {path} is readable by others. Restricting permissions to 0600.",
            file=sys.stderr,
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass


def resolve_content(only: str | None, skip: str | None) -> set[str]:
    if only and skip:
        raise SystemExit("--only and --skip are mutually exclusive")
    if only:
        chosen = {c.strip() for c in only.split(",") if c.strip()}
    else:
        chosen = set(ALL_CONTENT)
    if skip:
        chosen -= {c.strip() for c in skip.split(",") if c.strip()}
    unknown = chosen - set(ALL_CONTENT)
    if unknown:
        raise SystemExit(
            f"unknown content type(s): {', '.join(sorted(unknown))}\n"
            f"valid: {', '.join(ALL_CONTENT)}"
        )
    if not chosen:
        raise SystemExit("nothing selected")
    return chosen


async def run(args: argparse.Namespace) -> int:
    wizard_used = False
    base_url = normalize_base_url(args.url) if args.url else None
    token = resolve_token(args.token, args.creds_file)
    output = args.output

    # No token and a real terminal -> walk them through it rather than printing usage.
    if not token and not args.no_wizard and sys.stdin.isatty():
        base_url, token, output = run_wizard()
        wizard_used = True
    elif not base_url:
        raise SystemExit("--url is required")
    elif not token:
        raise SystemExit(
            f"No API token found.\n\n"
            f"  1. Open {base_url}/profile/settings\n"
            f"  2. 'Approved Integrations' -> '+ New Access Token'\n"
            f"  3. Purpose: 'Canvas Archive', leave Expires blank\n"
            f"  4. Then either:  export {ENV_VAR}=<token>   or   --token <token>\n"
        )

    remember_token(token)
    harden_creds_file(args.creds_file)

    content = resolve_content(args.only, args.skip)
    shard = token_shard(token)

    warning = check_path_budget(output.expanduser())
    if warning:
        print(f"\n  Note: {warning}")

    print(f"\n  Canvas:  {base_url}" + (f"   (token shard {shard})" if shard else ""))
    print(f"  Saving:  {output.expanduser().resolve()}")
    print(f"  Content: {', '.join(sorted(content))}\n")
    # Errors are raised on stderr; without this the two streams interleave wrongly
    # when both are piped to the same place.
    sys.stdout.flush()

    progress = make_progress(force_plain=args.plain)

    async with CanvasClient(
        base_url,
        token,
        concurrency=args.concurrency,
        download_concurrency=args.download_concurrency,
        retries=args.retries,
    ) as client:
        # Surface waits in the UI so a throttled run never looks like a hang.
        client.throttle.on_wait = progress.note

        try:
            user = await client.get("users/self")
        except Exception as exc:
            raise SystemExit(friendly_auth_error(exc, base_url)) from None
        print(f"  Signed in as {user.get('name')}\n")

        with progress:
            archiver = Archiver(
                client,
                output.expanduser(),
                content=content,
                progress=progress,
                build_html=not args.no_html,
                course_workers=args.course_workers,
            )
            stats = await archiver.run(set(args.course) if args.course else None, user=user)

    print_summary(stats, output.expanduser(), wizard_used, open_browser=not args.no_open)
    return 0


def friendly_auth_error(exc: Exception, base_url: str) -> str:
    """One actionable sentence, never a traceback."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status == 401:
        return (
            "Canvas rejected that token.\n\n"
            "  The three usual causes:\n"
            "    - a stray character was copied along with the token\n"
            f"    - the Canvas address is wrong (we tried {base_url})\n"
            "    - the token was deleted or has expired\n\n"
            "  Generate a fresh one at " + base_url + "/profile/settings"
        )
    if status == 404:
        return f"{base_url} answered, but it doesn't look like a Canvas site."
    return (
        f"Couldn't reach {base_url}.\n\n  {redact(str(exc))}\n\n  Check your internet connection."
    )


def open_in_browser(index: Path) -> None:
    """Show the finished archive.

    Only for an interactive run: popping a browser out of a cron job or a CI step is an
    unwelcome surprise. The path is printed either way, so nothing is lost when this
    does nothing -- which is also what happens on a headless Linux box.
    """
    if not sys.stdout.isatty():
        return
    if os.environ.get("CI") or os.environ.get("CANVAS_ARCHIVE_NO_OPEN"):
        return
    try:
        import webbrowser

        webbrowser.open(index.as_uri())
    except Exception as exc:  # a missing browser must never fail the run
        logging.debug("could not open a browser: %s", exc)


def print_summary(stats, output, wizard_used: bool, *, open_browser: bool = True) -> None:
    line = "=" * 60
    print("\n" + line)
    print("  Done.\n")
    print(f"  Courses          {stats.courses}")
    print(f"  Course files     {stats.files_downloaded}   ({human(stats.bytes_downloaded)})")
    if stats.files_skipped:
        print(f"  Already had      {stats.files_skipped}")
    if stats.recovered:
        print(f"  Recovered on retry {stats.recovered}")
    if stats.submissions:
        print(
            f"  Your submissions {stats.submissions}"
            f"   ({stats.submission_files} files, {human(stats.submission_bytes)})"
        )
    if stats.linked_files:
        print(
            f"  Linked documents {stats.linked_files}"
            f"   ({human(stats.linked_bytes)} — syllabus PDFs, handouts)"
        )
    if stats.quizzes:
        print(f"  Quizzes          {stats.quizzes}")
    if stats.discussion_posts:
        print(f"  Discussion posts {stats.discussion_posts}")
    if stats.pages:
        print(f"  Pages            {stats.pages}")
    for key, count in sorted(stats.json_records.items()):
        print(f"  {key:<16} {count}")

    if stats.skipped:
        print("\n  Some things weren't available to your account.")
        print("  This is normal -- schools hide parts of Canvas from students:")
        for reason, count in sorted(stats.skipped.items()):
            print(f"    {count:>4}x  {reason}")

    if stats.errors:
        print(f"\n  {len(stats.errors)} item(s) couldn't be fetched, even after retrying:")
        for err in stats.errors[:5]:
            print(f"    - {redact(err)}")
        if len(stats.errors) > 5:
            print(f"    ... and {len(stats.errors) - 5} more")
        print("\n  Just run the tool again -- it picks up where it left off.")

    print(f"\n  Your archive is here:\n    {output.resolve()}")

    index = (output / "index.html").resolve()
    if stats.html_pages and index.exists():
        print(f"  Open this in your browser to read it:\n    {index}")
        if open_browser:
            open_in_browser(index)
    print(line)
    if wizard_used and sys.stdin.isatty():
        try:
            input("\n  Press Enter to close. ")
        except (EOFError, KeyboardInterrupt):
            pass


def main() -> None:
    configure_console()
    args = build_parser().parse_args()
    setup_logging(args.verbose)
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        print("\n  Stopped. Run it again any time -- it resumes where it left off.")
        raise SystemExit(0) from None


if __name__ == "__main__":
    main()
