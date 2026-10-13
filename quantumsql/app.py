"""Application entry point."""
import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, APP_VERSION, theme
from .config import data_dir, resource_path, settings


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        with open(f"{data_dir()}/error.log", "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    box = QMessageBox(QMessageBox.Critical, f"{APP_NAME} — unexpected error", str(exc) or exc_type.__name__)
    box.setDetailedText(text)
    box.exec()


def main():
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
