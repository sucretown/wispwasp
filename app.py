"""
WispWasp desktop app.

    python app.py                    normal start
    python app.py --demo             fake backend, no GPU or audio needed
    python app.py --demo --listen    ...and start listening immediately
    python app.py --panel settings   open on a particular panel
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

from avcore.config import Settings
from avcore.engine import Engine
from avcore.server import OverlayServer
from avgui.window import run


def log_dir():
    """Somewhere writable whether running from source or from an install."""
    base = Path.home() / "WispWasp"
    base.mkdir(parents=True, exist_ok=True)
    return base


def install_crash_log():
    """
    Write unhandled exceptions to a file.

    A packaged windowed app has no console, so without this a crash just
    makes the window vanish with nothing to go on - which is useless when
    someone else is testing a build for you.
    """
    path = log_dir() / "crash.log"

    def hook(exc_type, exc, tb):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {stamp} =====\n")
            traceback.print_exception(exc_type, exc, tb, file=fh)
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook
    return path


def main():
    crash_log = install_crash_log()

    # Frozen builds unpack next to the executable, not next to this file.
    if getattr(sys, "frozen", False):
        here = Path(sys._MEIPASS)
    else:
        here = Path(__file__).resolve().parent

    if "--selftest" in sys.argv:
        # Checks the things that can silently go missing once packaged.
        # Results go to a file and are shown, since a windowed build has
        # nowhere to print.
        from selftest import run as run_selftest
        log = log_dir() / "selftest.log"
        ok, text = run_selftest(log)
        print(text)
        if not getattr(sys, "frozen", False):
            return 0 if ok else 1
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication([])
        box = QMessageBox()
        box.setWindowTitle("WispWasp self-test")
        box.setText("Everything passed." if ok
                    else "Something is wrong - see the details.")
        box.setDetailedText(text)
        box.setInformativeText(f"Saved to {log}")
        box.exec()
        return 0 if ok else 1

    settings = Settings.load()
    engine = Engine(settings)
    print(f"crash log: {crash_log}")

    # The overlay page and its images are served from inside the app, so
    # OBS can use a Browser Source without listener.py running.
    server = OverlayServer(settings, app_dir=here)
    if server.start():
        print(f"overlay: {server.url(bare=True)}")
    else:
        print(f"overlay unavailable: {server.error}")
    engine.server = server

    if "--demo" in sys.argv:
        from demo_stubs import install_stubs
        install_stubs(engine, autostart="--listen" in sys.argv)
        print("demo mode: no GPU, no microphone, no ComfyUI")
    elif "--listen" in sys.argv or settings.get("ui.autostart_listening"):
        # Begin listening without a click, for anyone who just wants it
        # running the moment the app opens.
        engine.start_listening()

    panel = None
    if "--panel" in sys.argv:
        i = sys.argv.index("--panel")
        if i + 1 < len(sys.argv):
            panel = sys.argv[i + 1]

    try:
        return run(engine, panel=panel)
    finally:
        server.stop()


if __name__ == "__main__":
    sys.exit(main())
