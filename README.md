# Canvas Archive

Saves your Canvas courses — files, pages, grades, assignments, announcements — to your
own computer, before your university takes your account away.

**Do this before you graduate, not after.** Many schools turn on *"restrict students
from viewing course after end date"*. Once that happens, the content is gone and there
is no way to get it back.

It only ever **reads**. It never posts, edits, or deletes anything in Canvas.

---

## How to use it

You do not need to install Python or anything else. Follow the steps for your computer.

### On a Mac

**Do not download the program in a browser.** macOS then blocks it with no error message.
Use the steps below instead.

1. **Open Terminal.** Press **Command (⌘) and Space**, type `Terminal`, press Return. A
   window with a blinking cursor is the right one.
2. **Copy this whole line**, click in that window, paste with **⌘V**, and press Return:

   ```sh
   curl -fsSL https://github.com/andrzejgluszynski/hsg_canvas_archive/releases/latest/download/install.sh | sh
   ```

3. When it finishes, **type** `canvas-archive` and press Return. If it says
   `command not found`, type `~/.local/bin/canvas-archive` instead.
4. **Answer the questions** (which university, then a Canvas token). It can open a web
   page and tell you what to click. When you paste the token, the characters stay
   hidden — that is normal.
5. **Leave the window open** until it says **Done**. Closing the laptop or the window
   stops it. If it stops halfway, run the same command again; it continues from where
   it left off.
6. Your files land in a folder called **CanvasArchive** in your home folder. Open
   `index.html` in a browser — no internet needed.

### On Windows

1. **Download** `canvas-archive-windows-x64.exe` from the
   [latest release](https://github.com/andrzejgluszynski/hsg_canvas_archive/releases/latest).
2. **Double-click** it.
3. If Windows says *Windows protected your PC*, click **More info → Run anyway**. The
   program is not signed by a registered publisher; that warning is expected.
4. **Answer the questions.** Leave the window open until it says **Done**. If it stops
   halfway, run it again; it continues from where it left off.
5. Open the **CanvasArchive** folder in your user folder, then open `index.html`.

---

## What you get

An **offline website** (`index.html`), **Markdown** you can open in any notes app, and
the **raw JSON** it all came from. Embedded images are downloaded too, so it still
works years from now on a USB stick.

```
CanvasArchive/
├── index.html                      ← start here
├── README.md
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

## If it says things were skipped

That is normal, not a failure.

Schools often hide the Files tab, Pages list, or Quizzes list from students. The tool
says so under *"Some things weren't available to your account"* and carries on. It
still reaches most of that content through **course modules**.

## If it's slow

Canvas limits how fast anyone can pull data. The tool waits and says so rather than
crashing. A large archive can take a while — leave it running. Stopping it is safe;
just run it again.

## Quizzes

Quiz titles, scores and attempts are saved. The **questions and answers usually are
not** — instructors often lock them after a quiz closes, and Canvas then refuses them
to students. The quiz page says so when that happens.

## Getting an access token

The tool walks you through this. For reference:

1. Open `https://<your-canvas>/profile/settings`
2. Scroll to **Approved Integrations**
3. Click **+ New Access Token**
4. Purpose: `Canvas Archive`. Leave **Expires** blank.
5. Click **Generate Token** and copy it — Canvas shows it only once.

Stray spaces, quotes, or a `Bearer` prefix are cleaned up automatically.

## If the Mac program does nothing

You probably downloaded it in a browser. Undo the block:

```sh
xattr -d com.apple.quarantine ~/Downloads/canvas-archive
chmod +x ~/Downloads/canvas-archive
```

On macOS 15 and later, right-click → Open no longer works. Use the command above, or
System Settings → Privacy & Security → **Open Anyway**.

## Choosing what to archive

By default it takes everything it can reach. To be selective:

```
canvas-archive --only files,grades            # just the documents and your marks
canvas-archive --only submissions              # only your own work and feedback
canvas-archive --skip announcements           # everything except announcements
canvas-archive --course 843 --course 835      # specific courses only
canvas-archive --url canvas.myschool.edu      # a different university
```

Run `canvas-archive --help` for the full list. Pass `--no-html` if you only want the
Markdown and JSON.

## Linux

Same install command as on a Mac:

```sh
curl -fsSL https://github.com/andrzejgluszynski/hsg_canvas_archive/releases/latest/download/install.sh | sh
canvas-archive
```

## Notes for the technically inclined

- Python 3.12+, `httpx`, no Node and no browser needed
- Read-only: the client refuses anything that isn't a `GET`
- Downloads stream to `.part` files and resume by byte range after a failure
- Failures get a calm single-threaded retry sweep at the end of the run
- Rate limiting is driven by Canvas's own `x-rate-limit-remaining` header, not guesswork
- Your token is stored nowhere by default, is never written to the archive, and is
  redacted from all log output. File download URLs have their `verifier=` capability
  token stripped before being saved.

Every release publishes `SHA256SUMS` if you would rather verify than trust. If Windows
antivirus quarantines the `.exe`, use the `-folder.zip` build instead.

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
