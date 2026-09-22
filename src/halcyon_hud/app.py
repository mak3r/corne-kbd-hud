import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .hid_transport import HidTransport
from .hud_window import HudWindow


def make_tray_icon(connected: bool) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(0x00000000)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(125, 211, 192) if connected else QColor(90, 94, 107)
    painter.setBrush(color)
    painter.setPen(color.darker(120))
    painter.drawRoundedRect(3, 8, 26, 16, 4, 4)
    painter.end()
    return QIcon(pixmap)


class HalcyonHudApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # PySide6's event loop otherwise swallows SIGINT (Ctrl-C) silently --
        # this timer just gives Python's own signal handler a chance to run.
        signal.signal(signal.SIGINT, lambda *_: self._quit())
        self._signal_timer = QTimer()
        self._signal_timer.timeout.connect(lambda: None)
        self._signal_timer.start(200)

        self.hud = HudWindow()

        self.transport = HidTransport()
        self.transport.layerChanged.connect(self.hud.set_layer)
        self.transport.connectionChanged.connect(self._on_connection_changed)

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(make_tray_icon(connected=False))
        self.tray.setToolTip("Halcyon Corne HUD")

        menu = QMenu()
        self.toggle_action = QAction("Show HUD")
        self.toggle_action.setCheckable(True)
        self.toggle_action.triggered.connect(self._toggle_hud)
        menu.addAction(self.toggle_action)
        menu.addSeparator()
        quit_action = QAction("Quit")
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

        self.transport.start()

    def _on_connection_changed(self, connected: bool):
        self.hud.set_connected(connected)
        self.tray.setIcon(make_tray_icon(connected))
        self.tray.setToolTip("Halcyon Corne HUD -- connected" if connected else "Halcyon Corne HUD -- not connected")

    def _toggle_hud(self, checked: bool):
        if checked:
            self.hud.show()
        else:
            self.hud.hide()

    def _quit(self):
        self.transport.stop()
        self.app.quit()

    def run(self):
        return self.app.exec()


def main():
    app = HalcyonHudApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
