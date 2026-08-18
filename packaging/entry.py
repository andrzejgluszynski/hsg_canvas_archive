"""PyInstaller entry point.

The package's own __main__.py uses a relative import, which has no parent package
when PyInstaller runs it as the top-level script. This uses an absolute import instead.
"""

import multiprocessing
import sys


def _pause_if_double_clicked() -> None:
    """Keep the window open when launched from Explorer.

    A double-clicked console app on Windows closes the instant it returns, so a
    non-technical user sees a flash and nothing else.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        count = ctypes.windll.kernel32.GetConsoleProcessList(
            (ctypes.c_uint * 2)(), 2
        )
        if count <= 1:  # we are the only process attached to this console
            try:
                input("\n  Press Enter to close this window. ")
            except (EOFError, KeyboardInterrupt):
                pass
    except Exception:
        pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from canvas_archive.cli import main

    try:
        main()
    finally:
        _pause_if_double_clicked()
