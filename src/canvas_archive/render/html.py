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
:root {
  --bg: #fdfdfc; --fg: #24211d; --muted: #6b655c; --rule: #e3ded6;
  --accent: #7a4b2a; --card: #ffffff; --code: #f4f1ec;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1a1815; --fg: #e8e3da; --muted: #9a9287; --rule: #332f2a;
    --accent: #d9a273; --card: #211e1a; --code: #262320;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
}
.wrap { max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.25rem 5rem; }
nav.crumbs { font-size: .85rem; color: var(--muted); margin-bottom: 2rem; }
nav.crumbs a { color: var(--muted); }
h1 { font-size: 1.9rem; line-height: 1.25; margin: 0 0 1rem; letter-spacing: -.02em; }
h2 { font-size: 1.3rem; margin: 2.5rem 0 .75rem; padding-top: 1.25rem;
     border-top: 1px solid var(--rule); }
h3 { font-size: 1.05rem; margin: 1.75rem 0 .5rem; }
h4, h5, h6 { font-size: .95rem; margin: 1.25rem 0 .5rem; }
p, li { overflow-wrap: anywhere; }
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
em { color: var(--muted); }
code { background: var(--code); padding: .12em .35em; border-radius: 3px; font-size: .9em; }
pre { background: var(--code); padding: 1rem; border-radius: 6px; overflow-x: auto; }
blockquote {
  margin: .75rem 0; padding: .1rem 0 .1rem 1rem;
  border-left: 3px solid var(--rule); color: var(--fg);
}
blockquote blockquote { border-left-color: var(--accent); }
.tablewrap { overflow-x: auto; margin: 1rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .93rem; }
th, td { text-align: left; padding: .5rem .7rem; border-bottom: 1px solid var(--rule); }
th { font-weight: 600; color: var(--muted); font-size: .8rem; text-transform: uppercase;
     letter-spacing: .04em; }
tr:last-child td { border-bottom: none; }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2rem 0; }
.cards { display: grid; gap: .6rem; margin: 1.5rem 0; }
.card {
  display: block; padding: .9rem 1.1rem; background: var(--card);
  border: 1px solid var(--rule); border-radius: 8px; text-decoration: none; color: var(--fg);
}
.card:hover { border-color: var(--accent); }
.card .t { font-weight: 600; }
.card .s { font-size: .85rem; color: var(--muted); margin-top: .15rem; }
footer { margin-top: 4rem; padding-top: 1.25rem; border-top: 1px solid var(--rule);
         font-size: .8rem; color: var(--muted); }
"""

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
        if stripped.startswith("|") and index + 1 < len(lines) and re.match(
            r"^\|[\s:|-]+\|$", lines[index + 1].strip()
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
        while index < len(lines) and lines[index].strip() and not re.match(
            r"^(#{1,6}\s|\||>|```|\s*([-*]|\d+\.)\s)", lines[index].strip()
        ):
            para.append(lines[index].strip())
            index += 1
        out.append(f"<p>{_inline(' '.join(para))}</p>")

    close_lists()
    return "\n".join(out)


def course_label(folder: str) -> str:
    """`Corporate Finance II__PT_COFIN2_26__843` -> `Corporate Finance II`.

    The folder name carries the code and Canvas id so collisions are impossible on
    disk, but neither belongs in a breadcrumb a person reads.
    """
    return folder.split("__")[0].strip() or folder


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
<div class="wrap">
{nav}
{body_html}
<footer>Archived from Canvas · <a href="{home}index.html">All courses</a></footer>
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
            crumbs = f'<a href="{"../" * depth}index.html">All courses</a>'
            # Everything below courses/<course>/ also links back to its course index.
            # That index lives (depth - 2) levels up; at depth 2 the page *is* it.
            if rel.parts[0] == "courses" and depth > 2:
                up = "../" * (depth - 2)
                label = html_mod.escape(course_label(rel.parts[1]))
                crumbs += f' &rsaquo; <a href="{up}index.html">{label}</a>'

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

    # A course index should also link to its section pages.
    for course_dir in (root / "courses").glob("*"):
        index = course_dir / "index.html"
        if not index.exists():
            continue
        cards = []
        for section in sorted(course_dir.iterdir()):
            if not section.is_dir():
                continue
            pages = sorted(section.glob("*.html"))
            if pages:
                cards.append(
                    f'<a class="card" href="./{quote(section.name)}/{quote(pages[0].name)}">'
                    f'<div class="t">{html_mod.escape(section.name.title())}</div></a>'
                )
            elif section.name in ("files", "submissions"):
                items = [p for p in section.iterdir() if p.is_file() and p.suffix != ".json"]
                if items:
                    cards.append(
                        f'<a class="card" href="./{quote(section.name)}/">'
                        f'<div class="t">{html_mod.escape(section.name.title())}</div>'
                        f'<div class="s">{len(items)} files</div></a>'
                    )
        if cards:
            text = fs_path(index).read_text(encoding="utf-8")
            block = '<h2>Sections</h2>\n<div class="cards">' + "".join(cards) + "</div>"
            fs_path(index).write_text(
                text.replace("<footer>", block + "\n<footer>"), encoding="utf-8"
            )
    return count
