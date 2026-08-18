"""First-run wizard.

This is the default path when the tool is launched with no arguments, because the
people it is built for will double-click it rather than read `--help`.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from ..auth import (
    clean_token,
    default_institution,
    load_institutions,
    looks_like_token,
    normalize_base_url,
)

BANNER = """
  Canvas Archive
  Saves your Canvas courses, files and grades to your own computer.
  It only ever reads -- it never posts, changes or deletes anything.
"""


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default or ""
    return answer or (default or "")


def _confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} [{hint}]").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def choose_institution() -> str:
    institutions = load_institutions()
    fallback = default_institution()

    if institutions:
        print("  Which Canvas do you use?\n")
        for index, inst in enumerate(institutions, 1):
            print(f"    {index}. {inst.name}")
            print(f"       {inst.base_url}")
        print(f"    {len(institutions) + 1}. Somewhere else\n")

        choice = _ask("  Choose", "1")
        if choice.isdigit() and 1 <= int(choice) <= len(institutions):
            return institutions[int(choice) - 1].base_url

    while True:
        raw = _ask("  Your Canvas web address (e.g. canvas.myschool.edu)")
        if not raw:
            if fallback:
                return fallback.base_url
            continue
        try:
            return normalize_base_url(raw)
        except ValueError:
            print("  That doesn't look like a web address -- try again.")


def get_token(base_url: str) -> str:
    import getpass

    settings_url = f"{base_url}/profile/settings"
    print("\n  Now we need an access token so the tool can read your courses.\n")
    print(f"    1. Open  {settings_url}")
    print("    2. Scroll down to 'Approved Integrations'")
    print("    3. Click '+ New Access Token'")
    print("    4. Purpose: 'Canvas Archive'   Expires: leave blank")
    print("    5. Click 'Generate Token', then copy it\n")
    print("  (The token is only shown once, so copy it before closing the box.)\n")

    if _confirm("  Open that page in your browser now?"):
        try:
            webbrowser.open(settings_url)
        except Exception:
            pass
        print(f"  If nothing opened, paste this into your browser:\n    {settings_url}\n")

    while True:
        try:
            raw = getpass.getpass("  Paste the token here (it stays hidden): ")
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1) from None

        token = clean_token(raw)
        if not token:
            print("  Nothing was pasted. Try again, or press Ctrl-C to quit.")
            continue
        if not looks_like_token(token) and not _confirm(
            "  That doesn't look like a usual Canvas token. Use it anyway?", default=False
        ):
            continue
        return token


def choose_output() -> Path:
    default = Path.home() / "CanvasArchive"
    raw = _ask("\n  Where should the archive go?", str(default))
    return Path(raw).expanduser()


def run_wizard() -> tuple[str, str, Path]:
    """Return (base_url, token, output_dir)."""
    if not sys.stdin.isatty():
        raise SystemExit(
            "This needs an interactive terminal.\n"
            "Run it directly, or use flags: canvas-archive --url ... --token ... -o ..."
        )

    print(BANNER)
    base_url = choose_institution()
    token = get_token(base_url)
    output = choose_output()
    return base_url, token, output
