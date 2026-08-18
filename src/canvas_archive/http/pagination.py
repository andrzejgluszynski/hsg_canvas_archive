"""RFC 5988 Link header parsing.

Canvas paginates with Link headers rather than page counts, so following `rel="next"`
is the only correct way to walk a collection. Guessing page counts from an item total
(as some tools do) silently drops the tail.
"""

from __future__ import annotations

import re

_LINK_RE = re.compile(r"<(?P<url>[^>]*)>\s*;\s*(?P<params>.*)")
_REL_RE = re.compile(r'rel\s*=\s*"?(?P<rel>[^",;]+)"?')


def parse_link_header(value: str | None) -> dict[str, str]:
    """Map rel -> url. Tolerates quoted params, extra params, and malformed segments."""
    links: dict[str, str] = {}
    if not value:
        return links

    for segment in value.split(","):
        segment = segment.strip()
        if not segment:
            continue
        match = _LINK_RE.match(segment)
        if not match:
            continue
        rel_match = _REL_RE.search(match.group("params"))
        if not rel_match:
            continue
        links[rel_match.group("rel").strip()] = match.group("url").strip()

    return links


def next_url(link_header: str | None) -> str | None:
    return parse_link_header(link_header).get("next")
