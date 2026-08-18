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
import re
from pathlib import Path
from urllib.parse import quote

from ..paths import fs_path

CSS = """
/* A deliberately small design system. No webfonts and no icon library: the archive
   must render identically from a USB stick in ten years, so everything is either a
   system font or an inline SVG. */
:root {
  --bg:#fbfaf8; --surface:#fff; --fg:#1c1a17; --muted:#6f6a62;
  --rule:#e6e1d8; --accent:#8a5a30; --accent-soft:#f2ebe2; --shadow:0 1px 2px rgba(28,26,23,.05);
  --radius:10px;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI Variable Text","Segoe UI",Roboto,
         "Helvetica Neue",Arial,sans-serif;
  --serif:ui-serif,Georgia,Cambria,"Times New Roman",serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#16150f; --surface:#1e1c18; --fg:#eae5dc; --muted:#9a938a;
    --rule:#2f2c26; --accent:#d9a273; --accent-soft:#2a231c; --shadow:none;
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--fg);
  font-family:var(--sans);font-size:16.5px;line-height:1.7;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;
}
.wrap{max-width:46rem;margin:0 auto;padding:2.75rem 1.5rem 6rem}

/* --- masthead ---------------------------------------------------------- */
nav.crumbs{
  display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;
  font-size:.82rem;color:var(--muted);margin-bottom:2.25rem;
}
nav.crumbs a{color:var(--muted);text-decoration:none;border-bottom:1px solid transparent}
nav.crumbs a:hover{color:var(--accent);border-bottom-color:var(--accent)}
nav.crumbs .sep{opacity:.45}

/* --- type -------------------------------------------------------------- */
h1{
  font-family:var(--serif);font-size:2.05rem;line-height:1.2;font-weight:600;
  letter-spacing:-.015em;margin:0 0 .6rem;
}
h2{
  font-size:1.22rem;font-weight:650;letter-spacing:-.005em;
  margin:2.75rem 0 .9rem;padding-top:1.4rem;border-top:1px solid var(--rule);
}
h3{font-size:1.02rem;font-weight:650;margin:1.9rem 0 .5rem}
h4,h5,h6{font-size:.93rem;font-weight:650;margin:1.4rem 0 .4rem;color:var(--muted)}
p{margin:0 0 1.05rem}
p,li{overflow-wrap:anywhere}
ul,ol{padding-left:1.35rem;margin:0 0 1.05rem}
li{margin:.28rem 0}
li::marker{color:var(--muted)}
a{color:var(--accent);text-decoration:none;
  border-bottom:1px solid color-mix(in srgb,var(--accent) 35%,transparent)}
a:hover{border-bottom-color:var(--accent)}
em{color:var(--muted);font-style:normal}
strong{font-weight:650}
code{font-family:var(--mono);background:var(--accent-soft);padding:.13em .38em;
     border-radius:4px;font-size:.88em}
pre{background:var(--accent-soft);padding:1rem 1.1rem;border-radius:var(--radius);
    overflow-x:auto;line-height:1.55}
pre code{background:none;padding:0}
blockquote{
  margin:1rem 0;padding:.15rem 0 .15rem 1.15rem;
  border-left:2px solid var(--rule);color:var(--fg);
}
blockquote blockquote{border-left-color:var(--accent)}
hr{border:0;border-top:1px solid var(--rule);margin:2.5rem 0}
p.sub{color:var(--muted);font-size:.9rem;margin:-.25rem 0 1.75rem}

/* --- tables ------------------------------------------------------------ */
.tablewrap{overflow-x:auto;margin:1.25rem 0;
           border:1px solid var(--rule);border-radius:var(--radius);background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;padding:.62rem .85rem;border-bottom:1px solid var(--rule)}
th{font-weight:600;color:var(--muted);font-size:.72rem;text-transform:uppercase;
   letter-spacing:.07em;background:var(--accent-soft)}
tr:last-child td{border-bottom:none}

/* --- file browser ------------------------------------------------------ */
table.files td{padding:.58rem .85rem;vertical-align:middle}
table.files td.k{width:2.4rem;padding-right:0;color:var(--muted)}
table.files td.n{text-align:right;color:var(--muted);font-size:.85rem;
                 white-space:nowrap;font-variant-numeric:tabular-nums}
table.files a{border-bottom:none;color:var(--fg);font-weight:500}
table.files tr:hover td{background:var(--accent-soft)}
table.files tr:hover a{color:var(--accent)}

/* --- section cards ----------------------------------------------------- */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(13.5rem,1fr));
       gap:.7rem;margin:1.4rem 0}
.card{
  display:flex;align-items:center;gap:.7rem;padding:.85rem 1rem;
  background:var(--surface);border:1px solid var(--rule);border-radius:var(--radius);
  text-decoration:none;color:var(--fg);box-shadow:var(--shadow);
  transition:border-color .15s ease,transform .15s ease;
}
.card:hover{border-color:var(--accent);transform:translateY(-1px)}
.card .ico{color:var(--accent);flex:none}
.card .t{font-weight:600;font-size:.95rem;line-height:1.3}
.card .s{font-size:.8rem;color:var(--muted);margin-top:.05rem}

/* --- icons ------------------------------------------------------------- */
.ico{width:1.15em;height:1.15em;vertical-align:-.2em;flex:none}
.ico-sm{width:1em;height:1em}

footer{margin-top:4.5rem;padding-top:1.4rem;border-top:1px solid var(--rule);
       font-size:.82rem;color:var(--muted)}
footer a{color:var(--muted)}

@media (max-width:34rem){
  .wrap{padding:1.75rem 1.1rem 4rem}
  h1{font-size:1.7rem}
  .cards{grid-template-columns:1fr}
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


def _inline(text: str) -> str:
    out = html_mod.escape(text, quote=False)
    out = _IMAGE.sub(
        lambda m: f'<img alt="{m.group(1)}" src="{m.group(2)}" style="max-width:100%">', out
    )
    out = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1) or m.group(2)}</a>', out)
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
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
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


def file_listing(directory: Path, *, title: str, depth: int, crumbs: str = "") -> str:
    """A browsable listing for a folder of real files.

    Browsers will not reliably render a directory index for a `file://` URL -- Chrome
    refuses outright -- so a folder of PDFs is otherwise a dead link. This generates
    the index instead of relying on the browser to.
    """
    entries = sorted(
        (e for e in directory.iterdir() if not e.name.startswith(".")),
        key=lambda e: (not e.is_dir(), e.name.lower()),
    )
    rows = []
    total = 0
    for entry in entries:
        if entry.name == "index.html":
            continue
        if entry.is_dir():
            target = f"{quote(entry.name)}/index.html"
            size = ""
            count = sum(1 for _ in entry.rglob("*") if _.is_file())
            note = f"{count} item{'s' if count != 1 else ''}"
        else:
            target = quote(entry.name)
            total += entry.stat().st_size
            size = _size(entry.stat().st_size)
            note = ""
        rows.append(
            f'<tr><td class="k">{icon(_kind(entry))}</td>'
            f'<td><a href="./{target}">{html_mod.escape(entry.name)}</a></td>'
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


def page(title: str, body_html: str, *, crumbs: str = "", depth: int = 0) -> str:
    """Wrap rendered content in the standalone page shell."""
    home = "../" * depth or "./"
    nav = f'<nav class="crumbs">{crumbs}</nav>' if crumbs else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html_mod.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
{ICON_SPRITE}
<div class="wrap">
{nav}
{body_html}
<footer>{icon("archive", "ico-sm")} Archived from Canvas ·
<a href="{home}index.html">All courses</a></footer>
</div>
</body>
</html>
"""


def md_file_to_html(md_path: Path, *, title: str, crumbs: str, depth: int) -> str:
    text = fs_path(md_path).read_text(encoding="utf-8")
    # Point cross-links at the generated HTML rather than the Markdown source.
    text = re.sub(r"\]\((\.[^)]*?)README\.md\)", r"](\1index.html)", text)
    text = re.sub(r"\]\((\.[^)]*?)([\w-]+)\.md\)", r"](\1\2.html)", text)
    return page(title, markdown_to_html(text), crumbs=crumbs, depth=depth)


def build_site(root: Path) -> int:
    """Generate an .html beside every .md in the archive. Returns the page count."""
    count = 0
    for md_path in sorted(root.rglob("*.md")):
        rel = md_path.relative_to(root)
        depth = len(rel.parts) - 1
        is_index = md_path.name == "README.md"
        target = md_path.with_name("index.html" if is_index else md_path.stem + ".html")

        crumbs = ""
        if depth:
            crumbs = f'<a href="{"../" * depth}index.html">{icon("home", "ico-sm")} All courses</a>'
            # Everything below courses/<course>/ also links back to its course index.
            # That index lives (depth - 2) levels up; at depth 2 the page *is* it.
            if rel.parts[0] == "courses" and depth > 2:
                up = "../" * (depth - 2)
                label = html_mod.escape(course_label(rel.parts[1]))
                crumbs += f'<span class="sep">&rsaquo;</span><a href="{up}index.html">{label}</a>'

        if depth == 0:
            title = "Canvas Archive"
        elif is_index:
            title = course_label(rel.parts[-2]) if depth == 2 else rel.parts[-2]
        else:
            title = md_path.stem.replace("_", " ").title()
        fs_path(target).write_text(
            md_file_to_html(md_path, title=title, crumbs=crumbs, depth=depth),
            encoding="utf-8",
        )
        count += 1

    # Folders of real files get a generated index, because a browser will not draw
    # one for a file:// URL.
    for course_dir in (root / "courses").glob("*"):
        if not course_dir.is_dir():
            continue
        for folder in course_dir.rglob("*"):
            if not folder.is_dir():
                continue
            if (folder / "index.html").exists():
                continue  # already has a page generated from its README
            # A folder holding only sub-folders (submissions/) still needs an index,
            # otherwise the only way in is to already know the sub-folder names.
            has_content = any(
                (f.is_file() and f.suffix != ".md") or (f.is_dir() and any(f.iterdir()))
                for f in folder.iterdir()
            )
            if not has_content:
                continue
            rel = folder.relative_to(root)
            depth = len(rel.parts)
            crumbs = (
                f'<a href="{"../" * depth}index.html">'
                f"{icon('home', 'ico-sm')} All courses</a>"
                f'<span class="sep">&rsaquo;</span>'
                f'<a href="{"../" * (depth - 2)}index.html">'
                f"{html_mod.escape(course_label(course_dir.name))}</a>"
            )
            fs_path(folder / "index.html").write_text(
                file_listing(
                    folder,
                    title=folder.name.replace("_", " ").title(),
                    depth=depth,
                    crumbs=crumbs,
                ),
                encoding="utf-8",
            )
            count += 1

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
                items = [f for f in section.rglob("*") if f.is_file() and f.suffix != ".html"]
                note = f"{len(items)} file{'s' if len(items) != 1 else ''}" if items else ""
                cards.append(
                    f'<a class="card" href="./{quote(section.name)}/index.html">'
                    f"{icon(_SECTION_ICON.get(section.name, 'doc'))}"
                    f'<div><div class="t">{html_mod.escape(_pretty(section.name))}</div>'
                    + (f'<div class="s">{note}</div>' if note else "")
                    + "</div></a>"
                )
                continue
            pages = sorted(section.glob("*.html"))
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
                text.replace("<footer>", block + "\n<footer>"), encoding="utf-8"
            )
    return count
