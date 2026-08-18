"""Cross-platform-safe filename components.

The rules here are deliberately conservative: an archive written on macOS must be
copyable to a Windows machine and back without loss or collision.
"""

from __future__ import annotations

import re
import unicodedata

# Characters illegal on Windows (and ':' / '/' on macOS+Linux respectively).
_ILLEGAL = re.compile(r'[<>:"/\\|?*]')

# Windows reserved device names, matched case-insensitively against the stem.
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

DEFAULT_MAX_BYTES = 120


def _strip_unsafe_categories(text: str) -> str:
    """Drop control/format/surrogate codepoints but keep ordinary letters."""
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co"))


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """Truncate on UTF-8 byte length -- filesystem limits are bytes, not characters."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Cut then drop any partial trailing codepoint.
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def safe_component(
    name: str | None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    fallback: str = "untitled",
) -> str:
    """Return `name` reduced to a single path component safe on all three OSes.

    Idempotent: ``safe_component(safe_component(x)) == safe_component(x)``. Resume
    correctness depends on that property, so it is property-tested.
    """
    if not name:
        return fallback

    # NFC (not NFKC -- NFKC rewrites legitimate characters such as full-width forms).
    text = unicodedata.normalize("NFC", name)
    text = text.replace(" ", " ")
    text = _strip_unsafe_categories(text)

    # Replace rather than delete, so distinct titles stay distinct.
    text = _ILLEGAL.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Windows silently trims trailing dots and spaces, which would desync the manifest.
    text = text.rstrip(" .")

    # Preserve the extension across truncation.
    stem, dot, ext = text.rpartition(".")
    if dot and 0 < len(ext) <= 10 and stem:
        budget = max_bytes - len(ext.encode("utf-8")) - 1
        if budget > 0:
            text = _truncate_bytes(stem, budget).rstrip(" .") + "." + ext
        else:
            text = _truncate_bytes(text, max_bytes).rstrip(" .")
    else:
        text = _truncate_bytes(text, max_bytes).rstrip(" .")

    if not text:
        return fallback

    # Reserved device names are reserved with or without an extension.
    if text.split(".", 1)[0].upper() in _RESERVED:
        text = "_" + text

    return text


def unique_component(component: str, canvas_id: str | int, taken: set[str]) -> str:
    """Disambiguate a collision with the Canvas id -- never a sequence number.

    Sequence numbers depend on iteration order, so a second run can assign a
    different suffix to the same item and silently re-download everything.
    """
    key = component.casefold()
    if key not in taken:
        taken.add(key)
        return component

    stem, dot, ext = component.rpartition(".")
    if dot and stem:
        candidate = f"{stem}-{canvas_id}.{ext}"
    else:
        candidate = f"{component}-{canvas_id}"

    taken.add(candidate.casefold())
    return candidate
