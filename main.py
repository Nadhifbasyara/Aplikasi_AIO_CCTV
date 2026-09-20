import sys

from PyQt6.QtWidgets import QApplication

from aio_cctv.gui.main_window import MainWindow
from aio_cctv.gui.utils import APP, ORG


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("AIO-CCTV Analytics")
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName("AIO-CCTV Analytics")
    app.setDesktopFileName(APP)
    win = MainWindow()
    win.resize(1400, 850)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
