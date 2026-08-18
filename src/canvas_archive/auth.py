"""Token resolution and Canvas URL normalisation."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

ENV_VAR = "CANVAS_ARCHIVE_TOKEN"
_TOKEN_SHARD = re.compile(r"^(\d+)~")
# Redact anything token-shaped from logs and error text.
TOKEN_PATTERN = re.compile(r"\b\d+~[A-Za-z0-9]{20,}\b")
# What a Canvas Cloud token looks like. Self-hosted instances omit the shard prefix,
# so a mismatch is a warning, never a rejection.
TOKEN_SHAPE = re.compile(r"^\d+~[A-Za-z0-9]{20,}$")

# Characters that survive a copy/paste from a browser or an email and silently
# corrupt the token: zero-width spaces, BOM, non-breaking spaces, soft hyphen.
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00a0\u202f\u00ad\u2060"), None)
# Smart quotes that rich-text editors substitute for plain ones.
_QUOTES = "\"'\u2018\u2019\u201c\u201d\u00ab\u00bb\u2039\u203a`"

# The exact token currently in use, so self-hosted tokens (no shard prefix) are
# still redacted from logs. Set via remember_token() after resolution.
_exact_token: str | None = None


def clean_token(raw: str | None) -> str:
    """Undo the damage a paste does to an API token.

    Non-technical users paste from Canvas, from an email, or from a chat message, and
    pick up wrappers and invisible characters on the way. Every transformation here
    corresponds to a real, observed way a paste goes wrong. Idempotent.
    """
    if not raw:
        return ""

    text = str(raw).translate(_INVISIBLE)

    # Peel wrappers first, while whitespace still separates a prefix from the token.
    # '"Bearer <token>"' needs two passes, hence the loop.
    for _ in range(4):
        before = text
        text = text.strip().strip(_QUOTES).strip()
        if text[:7].lower() == "bearer ":
            text = text[7:]
        elif text[:6].lower() == "token=":
            text = text[6:]
        # A trailing period from the end of a sentence in an email.
        text = text.strip().rstrip(".,;:")
        if text == before:
            break

    # Only now collapse internal whitespace: a token that wrapped across lines in the
    # source must be rejoined, not truncated at the break.
    return "".join(text.split())


def looks_like_token(token: str) -> bool:
    """Advisory shape check -- self-hosted Canvas issues tokens without a shard."""
    return bool(TOKEN_SHAPE.match(token))


@dataclass(frozen=True, slots=True)
class Institution:
    id: str
    name: str
    base_url: str
    token_url: str
    default: bool = False


def load_institutions() -> list[Institution]:
    path = Path(__file__).with_name("institutions.toml")
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Institution(**entry) for entry in data.get("institution", [])]


def default_institution() -> Institution | None:
    return next((i for i in load_institutions() if i.default), None)


def normalize_base_url(raw: str) -> str:
    """Reduce anything a user might paste to scheme + host.

    People paste `canvas.school.edu/courses/812`, a full /profile/settings URL, or a
    bare hostname. All three must resolve to the same base.
    """
    text = raw.strip()
    text = re.sub(r"^https?://", "", text, flags=re.I)
    host = text.split("/", 1)[0].strip().rstrip(".").lower()
    if not host:
        raise ValueError("empty Canvas URL")
    return f"https://{host}"


def token_shard(token: str) -> str | None:
    match = _TOKEN_SHARD.match(token.strip())
    return match.group(1) if match else None


def remember_token(token: str | None) -> None:
    """Remember the live token so redact() can strip it even without a shard prefix."""
    global _exact_token
    _exact_token = token or None


def redact(text: str) -> str:
    if _exact_token and _exact_token in text:
        text = text.replace(_exact_token, "<redacted-token>")
    return TOKEN_PATTERN.sub("<redacted-token>", text)


def resolve_token(explicit: str | None = None, creds_file: Path | None = None) -> str | None:
    """Explicit -> env var -> local creds file. All sources get the same cleaning."""
    for candidate in (
        explicit,
        os.environ.get(ENV_VAR),
        creds_file.read_text(encoding="utf-8") if creds_file and creds_file.exists() else None,
    ):
        cleaned = clean_token(candidate)
        if cleaned:
            return cleaned
    return None


def prompt_for_token(base_url: str) -> str:
    """Masked entry, so the token never lands in scrollback or shell history."""
    import getpass

    print("\n  Get an API token:\n")
    print(f"    1. Open  {base_url}/profile/settings")
    print("    2. Scroll to 'Approved Integrations' -> '+ New Access Token'")
    print("    3. Purpose: 'Canvas Archive'   Expires: leave blank")
    print("    4. Click 'Generate Token' and copy it (shown only once)\n")

    while True:
        raw = getpass.getpass("  Paste token (input hidden): ")
        token = clean_token(raw)
        if not token:
            print("  Nothing pasted -- try again, or press Ctrl-C to quit.")
            continue
        if not looks_like_token(token):
            print("  Hmm, that doesn't look like a Canvas token (expected e.g. 1234~AbC...).")
            if input("  Use it anyway? [y/N] ").strip().lower() not in ("y", "yes"):
                continue
        return token
