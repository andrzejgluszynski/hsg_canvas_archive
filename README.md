# Canvas Archive

Saves your Canvas courses — files, pages, grades, assignments, announcements — to your
own computer, before your university takes your account away.

**Run this before you graduate, not after.** Many schools switch on *"restrict students
from viewing course after end date"*, and once that flips, the content is gone from the
API too. There is no way to get it back afterwards.

It only ever **reads**. It never posts, edits, or deletes anything in Canvas.

---

## Install

Nothing to install first — no Python, no Node, no browser. One file.

**macOS and Linux**

```sh
curl -fsSL https://github.com/andrzejgluszynski/hsg_canvas_archive/releases/latest/download/install.sh | sh
```

Use this rather than downloading in a browser. Files fetched with `curl` don't get
macOS's quarantine flag, so Gatekeeper never blocks the program. A browser download
*does* get flagged, and macOS then kills it **with no error message at all** — it simply
does nothing, which is impossible to diagnose if you don't know to expect it.

If you did download it in a browser, undo the flag with:

```sh
xattr -d com.apple.quarantine ~/Downloads/canvas-archive
chmod +x ~/Downloads/canvas-archive
```

(On macOS 15 and later the old right-click → Open trick no longer works. Use the command
above, or System Settings → Privacy & Security → **Open Anyway**.)

**Windows**

Download `canvas-archive-windows-x64.exe` from the
[releases page](https://github.com/andrzejgluszynski/hsg_canvas_archive/releases/latest) and double-click it.

SmartScreen will say *"Windows protected your PC"* because the program isn't signed by a
registered publisher — code-signing certificates cost a few hundred dollars a year.
Click **More info → Run anyway**. If your antivirus quarantines it, use the
`-folder.zip` build instead: same program, just unpacked rather than self-extracting.

Every release publishes `SHA256SUMS` if you would rather verify than trust.

## Quick start

```
canvas-archive
```

That's it. It asks which university you're at, walks you through getting an access
token, and saves everything to `~/CanvasArchive`.

If something goes wrong partway through — your laptop sleeps, the wifi drops, Canvas
gets grumpy — just run it again. It picks up where it left off and re-downloads nothing.

## Getting an access token

The tool walks you through this, but for reference:

1. Open `https://<your-canvas>/profile/settings`
2. Scroll to **Approved Integrations**
3. Click **+ New Access Token**
4. Purpose: `Canvas Archive`. Leave **Expires** blank.
5. Click **Generate Token** and copy it — Canvas shows it only once.

Paste it when asked. Stray spaces, quotes, a `Bearer` prefix or a trailing full stop are
all cleaned up automatically, so don't worry about pasting it perfectly.

## What you get

Everything is saved three ways: an **offline website** you open in a browser,
**readable Markdown** for any Markdown viewer, and the **raw JSON** it all came from so
nothing is lost and a future tool can still parse it.

Open `index.html` and read your degree like a small website — no internet, no account,
no Canvas. Embedded images are downloaded and repointed locally, so it still works years
from now on a USB stick.

```
CanvasArchive/
├── index.html                      ← start here
├── README.md                       same thing, as Markdown
├── archive.json
├── user/
└── courses/
    └── 09 Corporate Finance II__PT_COFIN2_26__843/
        ├── index.html / README.md  overview, grade, syllabus
        ├── grades/                 your marks, per assignment
        ├── submissions/
        │   └── Assignment 3/
        │       ├── index.html      the task, your score, instructor feedback
        │       ├── Essay.pdf       what you actually handed in
        │       └── submission.json
        ├── quizzes/                quiz scores and attempts
        ├── discussions/            topics with their full reply threads
        ├── modules/                the course structure
        ├── announcements/  assignments/  pages/
        ├── files/                  the PDFs, slides and recordings
        └── _media/                 images embedded in the course text
```

Every page exists as `.html`, `.md` and `.json`. The HTML is for reading, the Markdown
for GitHub/VS Code/Obsidian, the JSON is the archival record — the complete, untouched
API response.

Pass `--no-html` if you only want the Markdown and JSON.

## Choosing what to archive

By default it takes everything it can reach. To be selective:

```
canvas-archive --only files,grades            # just the documents and your marks
canvas-archive --only submissions              # only your own work and feedback
canvas-archive --skip announcements           # everything except announcements
canvas-archive --course 843 --course 835      # specific courses only
canvas-archive --url canvas.myschool.edu      # a different university
```

Run `canvas-archive --help` for the full list.

## Quizzes

Quiz titles, your scores and your attempts are archived. The **questions and answers
usually are not** — instructors commonly restrict them once a quiz closes, and Canvas
then refuses them to students through the API too. Where that happens the tool says so
on the quiz page rather than leaving a confusing gap.

## "It says a lot of things were skipped"

That's normal, and not a failure.

Most universities hide parts of Canvas from students — the Files tab, the Pages index,
the Quizzes list. When that happens the tool reports it plainly under *"Some things
weren't available to your account"* and carries on. It reaches your content a different
way: by walking the **course modules**, which stay readable even when the top-level
listings are locked. That's usually the difference between archiving one file and
archiving all of them.

## If it's slow

Canvas limits how fast anyone can pull data. When it starts limiting us, the tool waits
and says so rather than hammering the server or crashing. A large archive can take a
while — leave it running. Interrupting is safe.

## Notes for the technically inclined

- Python 3.12+, `httpx`, no Node and no browser needed
- Read-only: the client refuses anything that isn't a `GET`
- Downloads stream to `.part` files and resume by byte range after a failure
- Failures get a calm single-threaded retry sweep at the end of the run
- Rate limiting is driven by Canvas's own `x-rate-limit-remaining` header, not guesswork
- Your token is stored nowhere by default, is never written to the archive, and is
  redacted from all log output. File download URLs have their `verifier=` capability
  token stripped before being saved.

```sh
uv sync
uv run pytest
uv run python -m canvas_archive --help
```

### Building the binaries

`uv sync --group build` then:

```sh
uv run pyinstaller packaging/canvas-archive.spec --noconfirm                  # one file
uv run pyinstaller packaging/canvas-archive.spec --noconfirm -- --onedir      # folder
```

PyInstaller cannot cross-compile, so each platform builds on its own runner.
`.github/workflows/release.yml` does all four on a `v*` tag: macOS arm64 (`macos-14`),
macOS x86_64 (`macos-15-intel`), Linux x86_64 (`ubuntu-22.04`) and Windows x64. Two
pins are deliberate and worth keeping:

- **`macos-15-intel`**, because `macos-13` was retired in December 2025. It is also the
  last free x86_64 macOS image and disappears in August 2027.
- **`ubuntu-22.04`, not `ubuntu-latest`** — PyInstaller links against the build
  machine's glibc, so building on 24.04 produces a binary that refuses to start on
  22.04 or Debian 12.

Set `andrzejgluszynski/hsg_canvas_archive` in `install.sh` (or export `CANVAS_ARCHIVE_REPO`) once the repository
has a home.

## Prior art

Inspired by [davekats/canvas-student-data-export](https://github.com/davekats/canvas-student-data-export)
(MIT, © 2025 David Katsandres), which mapped out which Canvas endpoints are worth
calling. This is an independent implementation.

## Licence

MIT
