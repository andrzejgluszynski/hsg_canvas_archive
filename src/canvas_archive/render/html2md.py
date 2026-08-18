"""Minimal HTML -> Markdown converter.

Written against the stdlib rather than pulling in html2text or markdownify, because
every dependency ends up inside the shipped binary. Canvas emits a predictable subset
of HTML (the RCE output), so a focused converter covers it without the weight.

Unknown tags degrade to their text content rather than being dropped, so nothing is
ever silently lost.
"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

_BLOCK = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "table",
    "tr",
    "blockquote",
    "pre",
    "hr",
}
_SKIP_CONTENT = {"script", "style", "head", "meta", "link"}


class _Converter(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0
        self._list_stack: list[dict] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._in_pre = False
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._table_rows: list[list[str]] = []
        self._header_done = False

    # -- helpers ---------------------------------------------------------
    def _emit(self, text: str) -> None:
        if self._cell is not None:
            self._cell.append(text)
        elif self._href is not None:
            self._link_text.append(text)
        else:
            self.out.append(text)

    def _newline(self, count: int = 1) -> None:
        if self._cell is not None:
            return
        text = "".join(self.out)
        existing = len(text) - len(text.rstrip("\n"))
        if not text.strip():
            return
        for _ in range(max(0, count - existing)):
            self.out.append("\n")

    # -- tags ------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr = dict(attrs)
        if tag in _SKIP_CONTENT:
            self._skip += 1
            return

        if tag in ("br",):
            self._emit("  \n")
        elif tag == "hr":
            self._newline(2), self.out.append("---\n\n")
        elif re.fullmatch(r"h[1-6]", tag):
            self._newline(2)
            self.out.append("#" * int(tag[1]) + " ")
        elif tag == "p" or tag == "div":
            self._newline(2)
        elif tag == "blockquote":
            self._newline(2), self.out.append("> ")
        elif tag in ("ul", "ol"):
            self._newline(2)
            self._list_stack.append({"ordered": tag == "ol", "n": 0})
        elif tag == "li":
            self._newline(1)
            if self._list_stack:
                lst = self._list_stack[-1]
                indent = "  " * (len(self._list_stack) - 1)
                if lst["ordered"]:
                    lst["n"] += 1
                    self.out.append(f"{indent}{lst['n']}. ")
                else:
                    self.out.append(f"{indent}- ")
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag == "pre":
            self._newline(2), self.out.append("```\n"), setattr(self, "_in_pre", True)
        elif tag == "a":
            self._href = attr.get("href")
            self._link_text = []
        elif tag == "img":
            alt = attr.get("alt") or "image"
            src = attr.get("src") or ""
            self._emit(f"![{alt}]({src})")
        elif tag == "iframe":
            src = attr.get("src") or ""
            title = attr.get("title") or "embedded video"
            if src:
                self._newline(2)
                self._emit(f"[{title}]({src})")
        elif tag == "table":
            self._table_rows = []
            self._header_done = False
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_CONTENT:
            self._skip = max(0, self._skip - 1)
            return

        if re.fullmatch(r"h[1-6]", tag) or tag in ("p", "div", "blockquote"):
            self._newline(2)
        elif tag in ("ul", "ol"):
            if self._list_stack:
                self._list_stack.pop()
            self._newline(2)
        elif tag == "li":
            self._newline(1)
        elif tag in ("strong", "b"):
            self._emit("**")
        elif tag in ("em", "i"):
            self._emit("*")
        elif tag == "code" and not self._in_pre:
            self._emit("`")
        elif tag == "pre":
            self._in_pre = False
            self.out.append("\n```\n\n")
        elif tag == "a":
            text = "".join(self._link_text).strip()
            href, self._href, self._link_text = self._href, None, []
            if href and text:
                self._emit(f"[{text}]({href})")
            elif text:
                self._emit(text)
            elif href:
                self._emit(href)
        elif tag in ("td", "th"):
            if self._row is not None and self._cell is not None:
                cell = " ".join("".join(self._cell).split())
                self._row.append(cell.replace("|", "\\|"))
            self._cell = None
        elif tag == "tr":
            if self._row:
                self._table_rows.append(self._row)
            self._row = None
        elif tag == "table":
            self._flush_table()

    def _flush_table(self) -> None:
        if not self._table_rows:
            return
        width = max(len(r) for r in self._table_rows)
        self._newline(2)
        for index, row in enumerate(self._table_rows):
            padded = row + [""] * (width - len(row))
            self.out.append("| " + " | ".join(padded) + " |\n")
            if index == 0:
                self.out.append("|" + "|".join([" --- "] * width) + "|\n")
        self.out.append("\n")
        self._table_rows = []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_pre:
            self.out.append(data)
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            # Preserve a single separating space between inline elements.
            if text and self.out and not "".join(self.out[-1:]).endswith((" ", "\n")):
                self._emit(" ")
            return
        self._emit(text)


def html_to_markdown(html: str | None) -> str:
    """Convert Canvas HTML to Markdown. Returns '' for empty input."""
    if not html:
        return ""
    parser = _Converter()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Never let a malformed body break an archive: fall back to stripped text.
        return re.sub(r"<[^>]+>", "", unescape(html)).strip()

    text = "".join(parser.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
