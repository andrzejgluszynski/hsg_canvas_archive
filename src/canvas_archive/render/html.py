"""Offline HTML viewer.

Rather than duplicating every Markdown renderer, this converts the Markdown they
already produce. We control that Markdown, so the subset needing support is small and
known -- which makes a hand-written converter safe here in a way a general-purpose one
would not be.

The output is fully self-contained: inline CSS, no fonts, no scripts, no network. It
must still open in ten years from a USB stick.
"""

from __future__ import annotations

import html as html_mod
import json
import re
from pathlib import Path
from urllib.parse import quote

from ..paths import fs_path
from .html2md import _safe_href
from .markdown import heading_id

CSS = """
/* A small, deliberately modern design system. No webfonts and no icon library: the
   archive must render identically from a USB stick in ten years, so type is a system
   stack and every icon is an inline SVG.

   `system-ui` resolves to SF Pro on macOS, Segoe UI Variable on Windows 11 and Roboto
   on Android -- the native UI face on each, which is what makes it feel current
   without shipping a font. Inter is listed first for anyone who has it. */
:root{
  --bg:#fcfcfd; --surface:#fff; --fg:#0e1116; --muted:#656d7b;
  --rule:#e7e9ee; --accent:#2f5cff; --accent-soft:#f0f3ff;
  --shadow:0 1px 2px rgba(14,17,22,.04), 0 1px 8px rgba(14,17,22,.03);
  --radius:12px;
  --sans:Inter,"SF Pro Text",system-ui,-apple-system,"Segoe UI Variable Text",
         "Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0c0d11; --surface:#14161c; --fg:#e8eaf0; --muted:#949cab;
    --rule:#222630; --accent:#8ea6ff; --accent-soft:#161a24; --shadow:none;
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--fg);
  font-family:var(--sans);font-size:16px;line-height:1.65;font-weight:400;
  font-feature-settings:"cv05" 1,"ss01" 1;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;
}
/* The page is wide so tables and card grids can use the screen, but running text
   is held to a readable measure. A 44rem column on a 27" display wastes most of the
   screen on a course index while wrapping the names that need the room. */
.wrap{max-width:64rem;margin:0 auto;padding:3rem 1.75rem 6rem}
h1,h2,h3,h4,h5,h6,p,ul,ol,blockquote,footer{max-width:46rem}

/* --- masthead ---------------------------------------------------------- */
nav.crumbs{
  display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;
  font-size:.8125rem;font-weight:500;color:var(--muted);margin-bottom:2.5rem;
}
nav.crumbs a{
  display:inline-flex;align-items:center;gap:.3rem;color:var(--muted);
  text-decoration:none;padding:.2rem .5rem;margin-left:-.5rem;border-radius:6px;
  transition:background .12s ease,color .12s ease;
}
nav.crumbs a:hover{color:var(--accent);background:var(--accent-soft)}
nav.crumbs .sep{opacity:.35}

/* --- type -------------------------------------------------------------- */
h1{
  font-size:2rem;line-height:1.15;font-weight:680;
  letter-spacing:-.032em;margin:0 0 .75rem;
}
h2{
  font-size:1.1875rem;font-weight:640;letter-spacing:-.017em;
  margin:3rem 0 1rem;padding-top:1.5rem;border-top:1px solid var(--rule);
}
h3{font-size:1rem;font-weight:640;letter-spacing:-.011em;margin:2rem 0 .55rem}
h4,h5,h6{
  font-size:.75rem;font-weight:620;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);margin:1.75rem 0 .5rem;
}
p{margin:0 0 1rem}
p,li,td,th{overflow-wrap:break-word}
ul,ol{padding-left:1.3rem;margin:0 0 1rem}
li{margin:.3rem 0}
li::marker{color:var(--muted)}
a{
  color:var(--accent);text-decoration:none;font-weight:480;
  border-bottom:1px solid transparent;transition:border-color .12s ease;
}
a:hover{border-bottom-color:var(--accent)}
a:focus-visible,.card:focus-visible,button:focus-visible,input:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px
}
.skip{
  position:absolute;left:-999px;top:0;background:var(--surface);color:var(--fg);
  padding:.4rem .7rem;z-index:2
}
.skip:focus{left:1rem;top:1rem}
.theme{
  position:absolute;top:1rem;right:1.25rem;font-size:.75rem;color:var(--muted);
  font-weight:500;cursor:pointer;user-select:none
}
.theme input{position:absolute;opacity:0;pointer-events:none}
html:has(#theme-light:checked){
  --bg:#fcfcfd;--surface:#fff;--fg:#0e1116;--muted:#656d7b;
  --rule:#e7e9ee;--accent:#2f5cff;--accent-soft:#f0f3ff;
  --shadow:0 1px 2px rgba(14,17,22,.04),0 1px 8px rgba(14,17,22,.03)
}
em{color:var(--muted);font-style:normal;font-size:.9375rem}
strong{font-weight:620;color:var(--fg)}
code{
  font-family:var(--mono);background:var(--accent-soft);padding:.14em .4em;
  border-radius:5px;font-size:.855em;
}
pre{
  background:var(--surface);border:1px solid var(--rule);padding:1rem 1.15rem;
  border-radius:var(--radius);overflow-x:auto;line-height:1.55;
}
pre code{background:none;padding:0}
blockquote{
  margin:1rem 0;padding:.1rem 0 .1rem 1.15rem;
  border-left:2px solid var(--rule);color:var(--fg);
}
blockquote blockquote{border-left-color:var(--accent)}
hr{border:0;border-top:1px solid var(--rule);margin:2.5rem 0}
p.sub{color:var(--muted);font-size:.875rem;margin:-.35rem 0 1.9rem;font-weight:450}

/* --- tables ------------------------------------------------------------ */
.tablewrap{
  overflow-x:auto;margin:1.25rem 0;border:1px solid var(--rule);
  border-radius:var(--radius);background:var(--surface);
}
table{border-collapse:collapse;width:100%;font-size:.9125rem}
table th:first-child,table td:first-child{width:auto}
/* Short trailing values (a grade, a size, a date) should never wrap. */
table td+td:last-child:not(:first-child){white-space:nowrap;width:1%}
th,td{text-align:left;padding:.65rem .9rem;border-bottom:1px solid var(--rule)}
th{
  font-weight:560;color:var(--muted);font-size:.6875rem;text-transform:uppercase;
  letter-spacing:.075em;background:transparent;
}
td{font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}

/* --- file browser ------------------------------------------------------ */
table.files td{padding:.6rem .9rem;vertical-align:middle;border-bottom:1px solid var(--rule)}
table.files td.k{width:2.5rem;padding-right:0;color:var(--muted)}
table.files td.n{
  text-align:right;color:var(--muted);font-size:.8125rem;
  white-space:nowrap;font-variant-numeric:tabular-nums;
}
table.files a{border-bottom:none;color:var(--fg);font-weight:480}
table.files tr{transition:background .1s ease}
table.files tr:hover td{background:var(--accent-soft)}
table.files tr:hover td.k,table.files tr:hover a{color:var(--accent)}
input.q{
  width:100%;max-width:46rem;padding:.55rem .7rem;border:1px solid var(--rule);
  border-radius:8px;background:var(--surface);color:var(--fg);font:inherit
}

/* --- section cards ----------------------------------------------------- */
.cards{
  display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));
  gap:.65rem;margin:1.5rem 0;
}
.card{
  display:flex;align-items:center;gap:.75rem;padding:.9rem 1rem;
  background:var(--surface);border:1px solid var(--rule);border-radius:var(--radius);
  text-decoration:none;color:var(--fg);box-shadow:var(--shadow);
  transition:border-color .14s ease,transform .14s ease,box-shadow .14s ease;
}
.card:hover{
  border-color:var(--accent);transform:translateY(-1px);
  box-shadow:0 2px 4px rgba(47,92,255,.07),0 4px 16px rgba(47,92,255,.06);
}
.card .ico{color:var(--accent);flex:none}
.card .t{font-weight:560;font-size:.9375rem;line-height:1.3;letter-spacing:-.006em}
.card .s{font-size:.78125rem;color:var(--muted);margin-top:.08rem}

/* --- icons ------------------------------------------------------------- */
.ico{width:1.15em;height:1.15em;vertical-align:-.2em;flex:none;stroke-width:1.6}
.ico-sm{width:.95em;height:.95em}

.grade{display:inline-flex;align-items:center;gap:.55rem;white-space:nowrap}
.g-val{font-weight:580;font-variant-numeric:tabular-nums;min-width:2.4ch;text-align:right}
.g-track{
  position:relative;width:5.5rem;height:5px;border-radius:3px;
  background:var(--rule);overflow:hidden;flex:none;
}
.g-fill{position:absolute;inset:0 auto 0 0;background:var(--accent);border-radius:3px}
.g-pct{color:var(--muted);font-size:.8125rem;font-variant-numeric:tabular-nums;
       min-width:4.5ch;text-align:right}
@media (max-width:34rem){.g-track{display:none}}
object.pdf{
  display:block;width:100%;height:min(78vh,60rem);margin:1.25rem 0;
  border:1px solid var(--rule);border-radius:var(--radius);background:var(--surface);
}
footer{
  margin-top:5rem;padding-top:1.5rem;border-top:1px solid var(--rule);
  font-size:.8125rem;color:var(--muted);display:flex;align-items:center;gap:.4rem;
}
footer a{color:var(--muted);font-weight:450}
footer a:hover{color:var(--accent)}

@media (max-width:34rem){
  .wrap{padding:2rem 1.15rem 4rem;max-width:100%}
  table td+td:last-child:not(:first-child){white-space:normal}
  h1{font-size:1.625rem}
  h2{font-size:1.0625rem;margin-top:2.25rem}
  .cards{grid-template-columns:1fr}
  .theme{position:static;display:block;margin:0 0 1rem}
}
@media (prefers-reduced-motion:reduce){
  .card,.card:hover{transition:none;transform:none}
}
@media print{
  :root,html:has(#theme-light:checked){
    --bg:#fff;--surface:#fff;--fg:#111;--muted:#444;--rule:#ccc;--accent:#000;--accent-soft:#f4f4f4;--shadow:none
  }
  nav.crumbs,footer,.theme,.skip{display:none}
  .card,.tablewrap,blockquote,object.pdf{break-inside:avoid;page-break-inside:avoid}
  body{background:#fff;color:#111}
}
"""


# A tiny inline sprite. Stroke-based so a single set works on both themes, and small
# enough that repeating it on every page costs less than a single webfont request
# would -- which we could not make anyway.
# Stroke-based icon paths, kept as data so no line runs away and a new icon is one
# entry rather than a wall of markup. Drawn on a 24x24 grid, inheriting currentColor
# so a single set works in both themes.
_ICON_PATHS: dict[str, str] = {
    "doc": (
        '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10'
        'a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>'
    ),
    "folder": (
        '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
    ),
    "video": '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 11 5-3v8l-5-3z"/>',
    "audio": (
        '<path d="M9 18V6l10-2v12"/>'
        '<circle cx="6.5" cy="18" r="2.5"/><circle cx="16.5" cy="16" r="2.5"/>'
    ),
    "image": (
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<circle cx="8.5" cy="9.5" r="1.6"/><path d="m21 16-5-5L5 20"/>'
    ),
    "page": '<path d="M5 4h14v16H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "link": (
        '<path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1"/>'
        '<path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1"/>'
    ),
    "grade": '<path d="M4 20V10M10 20V4M16 20v-8M22 20H2"/>',
    "task": '<path d="M9 11l2 2 4-4"/><rect x="3" y="4" width="18" height="16" rx="2"/>',
    "chat": '<path d="M21 12a8 8 0 0 1-8 8H4l2-3a8 8 0 1 1 15-5z"/>',
    "mega": (
        '<path d="M3 11v2a1 1 0 0 0 1 1h3l7 4V6L7 10H4a1 1 0 0 0-1 1z"/>'
        '<path d="M18 9a4 4 0 0 1 0 6"/>'
    ),
    "book": '<path d="M4 5a2 2 0 0 1 2-2h13v18H6a2 2 0 0 1-2-2z"/><path d="M8 3v18"/>',
    "quiz": (
        '<circle cx="12" cy="12" r="9"/>'
        '<path d="M9.5 9.5a2.5 2.5 0 1 1 3 2.45V14"/><path d="M12 17.5v.01"/>'
    ),
    "home": '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/>',
    "archive": (
        '<rect x="3" y="4" width="18" height="4" rx="1"/>'
        '<path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><path d="M10 12h4"/>'
    ),
}

_ICON_ATTRS = (
    'fill="none" stroke="currentColor" stroke-width="1.7" '
    'stroke-linecap="round" stroke-linejoin="round"'
)

ICON_SPRITE = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
    + "".join(f'<g id="i-{k}" {_ICON_ATTRS}>{v}</g>' for k, v in _ICON_PATHS.items())
    + "</defs></svg>"
)


def icon(name: str, extra: str = "") -> str:
    """Reference a sprite symbol. Decorative, so hidden from assistive tech."""
    cls = f"ico {extra}".strip()
    return f'<svg class="{cls}" aria-hidden="true"><use href="#i-{name}"/></svg>'


_INLINE = (
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"<strong>\1</strong>"),
    (re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)"), r"<em>\1</em>"),
    (re.compile(r"`([^`]+?)`"), r"<code>\1</code>"),
)
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


PAGE_SUFFIXES = {".html", ".htm", ""}
_CSP = (
    "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
    "media-src 'self'; frame-src 'none'; object-src 'self'; base-uri 'none'"
)
_CSP_SEARCH = _CSP + "; script-src 'unsafe-inline'"


def _safe_url(url: str) -> str | None:
    """Allow relative, http(s) and mailto. Reject javascript:/data:/vbscript:."""
    return _safe_href(url)


def _to_viewer(url: str) -> str:
    """Point HTML navigation at the in-page PDF viewer; leave Markdown sources alone."""
    prefix, hash_part = (url.split("#", 1) + [""])[:2]
    path, query = (prefix.split("?", 1) + [""])[:2]
    if path.lower().endswith(".pdf"):
        path = path[: -len(".pdf")] + ".view.html"
        url = path + (("?" + query) if query else "")
        if hash_part:
            url += "#" + hash_part
    return url


def _link_attrs(target: str) -> str:
    """Open documents in a new tab; keep page-to-page navigation in place.

    Clicking a PDF should not replace the page you were reading -- you lose your
    place in the archive and have to navigate back in. Pages link normally.
    """
    if target.startswith(("http://", "https://", "mailto:")):
        return ' target="_blank" rel="noopener noreferrer"'
    stem = target.split("?")[0].split("#")[0].rstrip("/")
    suffix = Path(stem).suffix.lower()
    if suffix in PAGE_SUFFIXES or stem.endswith(".view.html"):
        return ""
    return ' target="_blank" rel="noopener"'


def _inline(text: str) -> str:
    out = html_mod.escape(text, quote=False)

    def image(match: re.Match) -> str:
        alt = html_mod.escape(match.group(1), quote=True)
        src = _safe_url(match.group(2))
        if src is None:
            return alt or "image"
        src = html_mod.escape(_to_viewer(src), quote=True)
        return f'<img alt="{alt}" src="{src}" style="max-width:100%">'

    def link(match: re.Match) -> str:
        label = match.group(1) or match.group(2)
        href = _safe_url(match.group(2))
        if href is None:
            return label
        href = _to_viewer(href)
        return f'<a href="{html_mod.escape(href, quote=True)}"{_link_attrs(href)}>{label}</a>'

    out = _IMAGE.sub(image, out)
    out = _LINK.sub(link, out)
    for pattern, repl in _INLINE:
        out = pattern.sub(repl, out)
    return out


def markdown_to_html(text: str) -> str:
    """Convert the Markdown this tool emits into HTML."""
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    list_stack: list[str] = []

    def close_lists(to: int = 0) -> None:
        while len(list_stack) > to:
            out.append(f"</{list_stack.pop()}>")

    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            close_lists()
            index += 1
            continue

        if stripped.startswith("```"):
            close_lists()
            block = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(html_mod.escape(lines[index]))
                index += 1
            index += 1
            out.append("<pre><code>" + "\n".join(block) + "</code></pre>")
            continue

        if re.fullmatch(r"-{3,}", stripped):
            close_lists()
            out.append("<hr>")
            index += 1
            continue

        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            close_lists()
            level = len(heading.group(1))
            title = heading.group(2)
            ident = html_mod.escape(heading_id(title), quote=True)
            out.append(f'<h{level} id="{ident}">{_inline(title)}</h{level}>')
            index += 1
            continue

        # Tables: a header row followed by a |---| separator.
        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\|[\s:|-]+\|$", lines[index + 1].strip())
        ):
            close_lists()
            header = [c.strip() for c in stripped.strip("|").split("|")]
            index += 2
            rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append([c.strip() for c in lines[index].strip().strip("|").split("|")])
                index += 1
            head = "".join(f"<th>{_inline(c)}</th>" for c in header)
            body = "".join(
                "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows
            )
            out.append(
                f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead>'
                f"<tbody>{body}</tbody></table></div>"
            )
            continue

        quote_match = re.match(r"^((?:>\s?)+)(.*)", stripped)
        if quote_match:
            close_lists()
            depth = quote_match.group(1).count(">")
            block: list[str] = []
            while index < len(lines):
                m = re.match(r"^((?:>\s?)+)(.*)", lines[index].strip())
                if not m or m.group(1).count(">") != depth:
                    break
                block.append(m.group(2))
                index += 1
            inner = markdown_to_html("\n".join(block))
            out.append("<blockquote>" * depth + inner + "</blockquote>" * depth)
            continue

        bullet = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)", raw)
        if bullet:
            indent = len(bullet.group(1)) // 2
            tag = "ul" if bullet.group(2) in ("-", "*") else "ol"
            while len(list_stack) > indent + 1:
                out.append(f"</{list_stack.pop()}>")
            if len(list_stack) < indent + 1:
                out.append(f"<{tag}>")
                list_stack.append(tag)
            out.append(f"<li>{_inline(bullet.group(3))}</li>")
            index += 1
            continue

        close_lists()
        para = [stripped]
        index += 1
        while (
            index < len(lines)
            and lines[index].strip()
            and not re.match(r"^(#{1,6}\s|\||>|```|\s*([-*]|\d+\.)\s)", lines[index].strip())
        ):
            para.append(lines[index].strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")

    close_lists()
    return "\n".join(out)


def _pretty(name: str) -> str:
    """Folder name to something a person would write."""
    return name.lstrip("_").replace("_", " ").replace("-", " ").strip().title()


def course_label(folder: str) -> str:
    """`Corporate Finance II__PT_COFIN2_26__843` -> `Corporate Finance II`.

    The folder name carries the code and Canvas id so collisions are impossible on
    disk, but neither belongs in a breadcrumb a person reads.
    """
    return folder.split("__")[0].strip() or folder


_EXT_ICON = {
    ".pdf": "doc",
    ".doc": "doc",
    ".docx": "doc",
    ".txt": "doc",
    ".rtf": "doc",
    ".ppt": "page",
    ".pptx": "page",
    ".key": "page",
    ".xls": "grade",
    ".xlsx": "grade",
    ".csv": "grade",
    ".numbers": "grade",
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".avi": "video",
    ".mkv": "video",
    ".mp3": "audio",
    ".m4a": "audio",
    ".wav": "audio",
    ".aac": "audio",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".svg": "image",
    ".webp": "image",
    ".heic": "image",
    ".jfif": "image",
    ".html": "page",
    ".htm": "page",
    ".md": "page",
    ".json": "doc",
    ".zip": "folder",
}

# Which icon fronts each section card.
_SECTION_ICON = {
    "files": "folder",
    "submissions": "task",
    "grades": "grade",
    "assignments": "task",
    "announcements": "mega",
    "discussions": "chat",
    "modules": "book",
    "pages": "page",
    "quizzes": "quiz",
    "_media": "image",
}


# The raw API payloads and the Markdown sources are the archival record, not things
# anyone wants to browse. They stay on disk; they just do not clutter the file lists.
_PLUMBING_SUFFIXES = {".json", ".md"}


def _is_plumbing(entry: Path) -> bool:
    """Hide archival sources and generated companions from folder listings."""
    if not entry.is_file():
        return False
    name = entry.name.lower()
    if name == "index.html" or name.endswith(".view.html"):
        return True
    return entry.suffix.lower() in _PLUMBING_SUFFIXES


def _size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def _kind(path: Path) -> str:
    if path.is_dir():
        return "folder"
    return _EXT_ICON.get(path.suffix.lower(), "doc")


def pdf_viewer(pdf: Path, *, depth: int, crumbs: str) -> str:
    """A page that shows a PDF inline, with the archive's navigation still around it.

    Clicking a document should not eject you from the archive. `<object>` uses the
    browser's own PDF viewer and falls back to its child content when there isn't one,
    so this degrades to a plain link rather than an empty frame.
    """
    name = html_mod.escape(pdf.name)
    target = quote(pdf.name)
    body = (
        f"<h1>{name}</h1>"
        f'<p class="sub">{_size(pdf.stat().st_size)}'
        f' · <a href="./{target}" target="_blank" rel="noopener">open directly</a></p>'
        f'<object class="pdf" data="./{target}" type="application/pdf">'
        f"<p>Your browser can't show this PDF inline. "
        f'<a href="./{target}" target="_blank" rel="noopener">Open {name}</a> instead.</p>'
        f"</object>"
    )
    return page(pdf.name, body, crumbs=crumbs, depth=depth)


def file_listing(directory: Path, *, title: str, depth: int, crumbs: str = "") -> str:
    """A browsable listing for a folder of real files.

    Browsers will not reliably render a directory index for a `file://` URL -- Chrome
    refuses outright -- so a folder of PDFs is otherwise a dead link. This generates
    the index instead of relying on the browser to.
    """
    entries = sorted(
        (e for e in directory.iterdir() if not e.name.startswith(".") and not _is_plumbing(e)),
        key=lambda e: (not e.is_dir(), e.name.lower()),
    )
    rows = []
    total = 0
    for entry in entries:
        if entry.is_dir():
            target = f"{quote(entry.name)}/index.html"
            size = ""
            count = sum(1 for f in entry.rglob("*") if f.is_file() and not _is_plumbing(f))
            note = f"{count} item{'s' if count != 1 else ''}"
        else:
            # PDFs open in the in-page viewer; everything else opens directly.
            target = (
                f"{quote(entry.stem)}.view.html"
                if entry.suffix.lower() == ".pdf"
                else quote(entry.name)
            )
            total += entry.stat().st_size
            size = _size(entry.stat().st_size)
            note = ""
        rows.append(
            f'<tr><td class="k">{icon(_kind(entry))}</td>'
            f'<td><a href="./{target}"{_link_attrs(target)}>'
            f"{html_mod.escape(entry.name)}</a></td>"
            f'<td class="n">{size or note}</td></tr>'
        )

    if not rows:
        body = "<p><em>This folder is empty.</em></p>"
    else:
        body = (
            f"<h1>{html_mod.escape(title)}</h1>"
            f"<p class='sub'>{len(rows)} item{'s' if len(rows) != 1 else ''}"
            + (f" · {_size(total)}" if total else "")
            + "</p>"
            "<div class='tablewrap'><table class='files'><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    return page(title, body, crumbs=crumbs, depth=depth)


# Grades arrive as "5.25 / 78.45%" (a Swiss 1-6 mark plus a percentage), or as a bare
# percentage, or as an em dash when the course was never graded. The Markdown keeps the
# plain text; only the rendered index draws the scale.
_GRADE_BOTH = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)%\s*$")
_GRADE_PCT = re.compile(r"^\s*(\d+(?:\.\d+)?)%\s*$")

SWISS_MIN, SWISS_MAX = 1.0, 6.0


def grade_cell(text: str) -> str:
    """Render a grade as a value, a bar showing where it sits, and its percentage.

    A number alone gives no sense of how good it is unless you already know the scale,
    which nobody will in ten years.
    """
    both = _GRADE_BOTH.match(text)
    pct_only = _GRADE_PCT.match(text)

    if both:
        mark, pct = float(both.group(1)), float(both.group(2))
        fill = (mark - SWISS_MIN) / (SWISS_MAX - SWISS_MIN)
        value, trailing = both.group(1), f"{both.group(2)}%"
        hint = f"{value} on a {SWISS_MIN:.0f}-{SWISS_MAX:.0f} scale ({trailing})"
    elif pct_only:
        pct = float(pct_only.group(1))
        fill = pct / 100
        value, trailing = f"{pct_only.group(1)}%", ""
        hint = value
    else:
        return html_mod.escape(text)

    width = max(0.0, min(1.0, fill)) * 100
    return (
        f'<span class="grade" title="{html_mod.escape(hint)}">'
        f'<span class="g-val">{html_mod.escape(value)}</span>'
        f'<span class="g-track"><span class="g-fill" style="width:{width:.1f}%"></span></span>'
        + (f'<span class="g-pct">{html_mod.escape(trailing)}</span>' if trailing else "")
        + "</span>"
    )


def enhance_grades(html: str) -> str:
    """Swap the second column of the index table for rendered grade cells."""
    row = re.compile(r"(<tr><td>.*?</td><td>)(.*?)(</td></tr>)", re.S)
    return row.sub(lambda m: m.group(1) + grade_cell(m.group(2)) + m.group(3), html)


def page(
    title: str,
    body_html: str,
    *,
    crumbs: str = "",
    depth: int = 0,
    csp: str | None = None,
    extra_head: str = "",
) -> str:
    """Wrap rendered content in the standalone page shell."""
    home = "../" * depth or "./"
    nav = f'<nav class="crumbs" aria-label="Breadcrumb">{crumbs}</nav>' if crumbs else ""
    policy = csp or _CSP
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{policy}">
<title>{html_mod.escape(title)}</title>
<style>{CSS}</style>
{extra_head}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<label class="theme"><input type="checkbox" id="theme-light"> Light</label>
{ICON_SPRITE}
<div class="wrap">
{nav}
<main id="main">
{body_html}
</main>
<footer>{icon("archive", "ico-sm")} Archived from Canvas ·
<a href="{home}index.html">All courses</a> ·
<a href="{home}search.html">Search</a></footer>
</div>
</body>
</html>
"""


def md_file_to_html(md_path: Path, *, title: str, crumbs: str, depth: int) -> str:
    text = fs_path(md_path).read_text(encoding="utf-8")
    # Point cross-links at the generated HTML rather than the Markdown source.
    text = re.sub(r"\]\(((\.[^)]*?/)?)README\.md\)", r"](\1index.html)", text)
    text = re.sub(r"\]\(((\.[^)]*?/)?)([^)/]+)\.md\)", r"](\1\3.html)", text)
    return page(title, markdown_to_html(text), crumbs=crumbs, depth=depth)


def _md_crumbs(rel: Path, depth: int) -> str:
    if not depth:
        return ""
    crumbs = f'<a href="{"../" * depth}index.html">{icon("home", "ico-sm")} All courses</a>'
    if rel.parts[0] != "courses" or depth < 3:
        return crumbs
    up = "../" * (depth - 2)
    label = html_mod.escape(course_label(rel.parts[1]))
    crumbs += f'<span class="sep">&rsaquo;</span><a href="{up}index.html">{label}</a>'
    section = html_mod.escape(_pretty(rel.parts[2]))
    if depth == 3:
        crumbs += f'<span class="sep">&rsaquo;</span><span>{section}</span>'
    else:
        sec_up = "../" * (depth - 3)
        crumbs += (
            f'<span class="sep">&rsaquo;</span>'
            f'<a href="{sec_up}index.html">{section}</a>'
            f'<span class="sep">&rsaquo;</span>'
            f"<span>{html_mod.escape(_pretty(rel.parts[-2]))}</span>"
        )
    return crumbs


def _write_pdf_viewers(root: Path, course_dir: Path) -> int:
    count = 0
    for pdf in course_dir.rglob("*.pdf"):
        if not pdf.is_file() or _is_plumbing(pdf):
            continue
        folder = pdf.parent
        rel = folder.relative_to(root)
        depth = len(rel.parts)
        crumbs = (
            f'<a href="{"../" * depth}index.html">{icon("home", "ico-sm")} All courses</a>'
            f'<span class="sep">&rsaquo;</span>'
            f'<a href="{"../" * (depth - 2)}index.html">'
            f"{html_mod.escape(course_label(course_dir.name))}</a>"
            f'<span class="sep">&rsaquo;</span>'
            f'<a href="./index.html">{html_mod.escape(_pretty(folder.name))}</a>'
        )
        fs_path(folder / f"{pdf.stem}.view.html").write_text(
            pdf_viewer(pdf, depth=depth, crumbs=crumbs), encoding="utf-8"
        )
        count += 1
    return count


def build_search(root: Path) -> None:
    """A self-contained search page. The only generated file that contains a script."""
    entries: list[dict[str, str]] = []
    for md_path in sorted(root.rglob("*.md")):
        rel = md_path.relative_to(root)
        is_index = md_path.name == "README.md"
        href = str(rel.with_name("index.html" if is_index else md_path.stem + ".html")).replace(
            "\\", "/"
        )
        text = fs_path(md_path).read_text(encoding="utf-8", errors="ignore")
        title_match = re.search(r"^#\s+(.+)$", text, re.M)
        title = title_match.group(1).strip() if title_match else md_path.stem
        snippet = re.sub(r"[#*_`\[\]]+", " ", text)
        snippet = re.sub(r"\s+", " ", snippet).strip()[:240]
        entries.append({"title": title, "href": href, "text": snippet})

    payload = json.dumps(entries, ensure_ascii=False).replace("</", "<\\/")
    body = (
        "<h1>Search</h1>\n"
        '<p class="sub">Everything in this archive, searched on this page. '
        "Nothing is sent anywhere.</p>\n"
        '<p><input id="q" type="search" placeholder="Find a course, file or page" '
        'aria-label="Search" class="q"></p>\n'
        '<ul id="hits" class="hits"></ul>'
    )
    script = f"""<script>
const INDEX = {payload};
const hits = document.getElementById("hits");
document.getElementById("q").addEventListener("input", function () {{
  const q = this.value.trim().toLowerCase();
  hits.innerHTML = "";
  if (q.length < 2) return;
  let n = 0;
  for (const item of INDEX) {{
    const hay = (item.title + " " + item.text).toLowerCase();
    if (hay.indexOf(q) === -1) continue;
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = item.href;
    a.textContent = item.title;
    li.appendChild(a);
    hits.appendChild(li);
    if (++n >= 50) break;
  }}
  if (!n) hits.innerHTML = "<li><em>No matches.</em></li>";
}});
</script>"""
    fs_path(root / "search.html").write_text(
        page(
            "Search",
            body,
            crumbs=f'<a href="./index.html">{icon("home", "ico-sm")} All courses</a>',
            depth=0,
            csp=_CSP_SEARCH,
            extra_head=script,
        ),
        encoding="utf-8",
    )


def build_site(root: Path) -> int:
    """Generate an .html beside every .md in the archive. Returns the page count."""
    count = 0
    for md_path in sorted(root.rglob("*.md")):
        rel = md_path.relative_to(root)
        depth = len(rel.parts) - 1
        is_index = md_path.name == "README.md"
        target = md_path.with_name("index.html" if is_index else md_path.stem + ".html")
        crumbs = _md_crumbs(rel, depth)

        if depth == 0:
            title = "Canvas Archive"
        elif is_index:
            title = course_label(rel.parts[-2]) if depth == 2 else rel.parts[-2]
        else:
            title = md_path.stem.replace("_", " ").title()
        rendered = md_file_to_html(md_path, title=title, crumbs=crumbs, depth=depth)
        if depth == 0 and is_index:
            rendered = enhance_grades(rendered)
        fs_path(target).write_text(rendered, encoding="utf-8")
        count += 1

    # A folder whose contents came from Markdown already has a rendered page. Giving
    # it a file listing as well would put the raw JSON in front of the reader instead
    # of the page they actually want -- clicking "Grades" must land on grades.html.
    rendered_dirs = {md.parent for md in root.rglob("*.md")}

    # Drop stale pages from earlier builds: an index.html is only ever legitimate
    # where a README.md produced it. Without this, a listing written by a previous
    # version keeps shadowing the rendered page it was replaced by.
    for folder in rendered_dirs:
        stale = folder / "index.html"
        if stale.exists() and not (folder / "README.md").exists():
            stale.unlink()

    for course_dir in (root / "courses").glob("*"):
        if not course_dir.is_dir():
            continue
        for folder in course_dir.rglob("*"):
            if not folder.is_dir() or folder in rendered_dirs:
                continue
            # A folder holding only sub-folders (submissions/) still needs an index,
            # otherwise the only way in is to already know the sub-folder names.
            has_content = any(
                (f.is_file() and not _is_plumbing(f)) or (f.is_dir() and any(f.iterdir()))
                for f in folder.iterdir()
            )
            if not has_content:
                continue
            rel = folder.relative_to(root)
            depth = len(rel.parts)
            crumbs = _md_crumbs(rel / "index.html", depth)
            fs_path(folder / "index.html").write_text(
                file_listing(
                    folder,
                    title=_pretty(folder.name),
                    depth=depth,
                    crumbs=crumbs,
                ),
                encoding="utf-8",
            )
            count += 1

        count += _write_pdf_viewers(root, course_dir)

    # A course index should also link to its section pages.
    for course_dir in (root / "courses").glob("*"):
        index = course_dir / "index.html"
        if not index.exists():
            continue
        cards = []
        for section in sorted(course_dir.iterdir()):
            if not section.is_dir():
                continue
            page_file = section / "index.html"
            if page_file.exists():
                items = [f for f in section.rglob("*") if f.is_file() and not _is_plumbing(f)]
                note = f"{len(items)} file{'s' if len(items) != 1 else ''}" if items else ""
                cards.append(
                    f'<a class="card" href="./{quote(section.name)}/index.html">'
                    f"{icon(_SECTION_ICON.get(section.name, 'doc'))}"
                    f'<div><div class="t">{html_mod.escape(_pretty(section.name))}</div>'
                    + (f'<div class="s">{note}</div>' if note else "")
                    + "</div></a>"
                )
                continue
            # Prefer the section's own page (pages/pages.html) over whichever
            # individual page happens to sort first.
            named = section / f"{section.name}.html"
            pages = (
                [named]
                if named.exists()
                else sorted(p for p in section.glob("*.html") if not p.name.endswith(".view.html"))
            )
            if pages:
                cards.append(
                    f'<a class="card" href="./{quote(section.name)}/{quote(pages[0].name)}">'
                    f"{icon(_SECTION_ICON.get(section.name, 'doc'))}"
                    f'<div><div class="t">{html_mod.escape(_pretty(section.name))}</div>'
                    f"</div></a>"
                )
        if cards:
            text = fs_path(index).read_text(encoding="utf-8")
            block = '<h2>Sections</h2>\n<div class="cards">' + "".join(cards) + "</div>"
            fs_path(index).write_text(
                text.replace("</main>", block + "\n</main>"), encoding="utf-8"
            )

    build_search(root)
    count += 1
    return count
