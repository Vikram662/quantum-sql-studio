"""Application entry point."""
import faulthandler
import os
import sys
import threading
import time
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, APP_VERSION, theme
from .config import data_dir, resource_path, settings


_box_open = False


def _excepthook(exc_type, exc, tb):
    global _box_open
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(f"{data_dir()}/error.log", "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    # A repeating error (e.g. from a timer) must not stack modal boxes inside each other's event loops —
    # that recursion eventually overflows the stack and kills the process. Show one; the rest go to the log.
    if _box_open:
        return
    _box_open = True
    try:
        box = QMessageBox(QMessageBox.Critical, f"{APP_NAME} — unexpected error", str(exc) or exc_type.__name__)
        box.setDetailedText(text)
        box.exec()
    finally:
        _box_open = False


def _thread_excepthook(args):
    text = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    try:
        with open(f"{data_dir()}/error.log", "a", encoding="utf-8") as f:
            f.write(f"[thread {args.thread.name if args.thread else '?'}]\n{text}\n")
    except OSError:
        pass


def _enable_crash_log():
    """Hard crashes (segfault, abort) skip Python's excepthook; faulthandler still dumps every thread's stack."""
    try:
        f = open(f"{data_dir()}/crash.log", "a", encoding="utf-8")  # kept open for the process lifetime
        f.write(f"\n=== {APP_NAME} {APP_VERSION} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        f.flush()
        faulthandler.enable(f, all_threads=True)
    except OSError:
        pass


def main():
    _enable_crash_log()
    threading.excepthook = _thread_excepthook
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QuantumSQL.Studio")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(QIcon(resource_path("app_icon.ico")))
    theme.apply(app, settings.get("theme", "dark"))
    sys.excepthook = _excepthook

    from .main_window import MainWindow
    win = MainWindow()
    win.show()
    if not win.open_session_manager():
        return 0
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            tab = win.new_query()
            if tab:
                tab.open_file(arg)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
