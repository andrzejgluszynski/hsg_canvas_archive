# PyInstaller spec -- one file, all platforms.
#
# Build:
#   uv run pyinstaller packaging/canvas-archive.spec --noconfirm            # onefile
#   uv run pyinstaller packaging/canvas-archive.spec --noconfirm -- --onedir
#
# PyInstaller cannot cross-compile, so each target is built on its own runner.

import sys
from pathlib import Path

# SPECPATH is injected by PyInstaller; the repo root is its parent.
ROOT = Path(SPECPATH).parent
ONEDIR = "--onedir" in sys.argv

block_cipher = None

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # institutions.toml is read at runtime via Path(__file__).with_name(), so it has
    # to travel with the package rather than being inlined.
    datas=[(str(ROOT / "src" / "canvas_archive" / "institutions.toml"), "canvas_archive")],
    hiddenimports=[
        # rich is imported lazily inside RichProgress.__init__ so the plain path stays
        # fast; name the submodules explicitly so static analysis cannot miss them.
        "rich.console",
        "rich.progress",
        # httpx selects its backend at runtime.
        "httpcore",
        "h11",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Nothing here is imported, and each one is meaningful weight in a binary that
        # people download over hotel wifi.
        "tkinter", "unittest", "pydoc", "doctest", "test", "distutils",
        "setuptools", "pip", "pkg_resources",
        "numpy", "pandas", "matplotlib", "PIL", "IPython",
        "h2", "socksio", "brotli", "brotlicffi",
        "sqlite3",          # not used yet; the manifest lands in a later phase
        "xml", "email.mime", "curses",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEDIR:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="canvas-archive",
        debug=False,
        bootloader_ignore_signals=False,
        # strip breaks the macOS code signature; only safe on Linux.
        strip=sys.platform.startswith("linux"),
        upx=False,
        console=True,
        # Ad-hoc signing is mandatory on Apple Silicon: unsigned arm64 binaries are
        # killed outright rather than merely warned about.
        codesign_identity=None,
        target_arch=None,
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=sys.platform.startswith("linux"),
        upx=False,
        name="canvas-archive",
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name="canvas-archive",
        debug=False,
        bootloader_ignore_signals=False,
        strip=sys.platform.startswith("linux"),
        upx=False,
        runtime_tmpdir=None,
        console=True,
        disable_windowed_traceback=False,
        codesign_identity=None,
        target_arch=None,
    )
