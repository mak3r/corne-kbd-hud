import signal
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .hid_transport import HidTransport
from .hud_window import HudWindow


def make_tray_icon(connected: bool) -> QIcon:
    # Distinguish by SHAPE, not just color -- macOS tray icons are often
    # rendered as monochrome "template" images, which would silently
    # flatten a color-only distinction.
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))  # explicit transparent, not a bare 0 int
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(125, 211, 192) if connected else QColor(140, 144, 155)
    painter.setPen(color)
    if connected:
        painter.setBrush(color)
        painter.drawEllipse(8, 8, 16, 16)  # filled dot = connected
    else:
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(9, 9, 14, 14)  # hollow ring = not connected
    painter.end()
    return QIcon(pixmap)


class HalcyonHudApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        # PySide6's event loop otherwise swallows SIGINT (Ctrl-C) silently --
        # this timer just gives Python's own signal handler a chance to run.
        # The handler itself only calls app.quit() (safe from a signal
        # context); actual cleanup happens on aboutToQuit below, in normal
        # Qt event-loop context -- calling a blocking QThread.wait() directly
        # from a signal handler was causing an intermittent abort on exit.
        signal.signal(signal.SIGINT, lambda *_: self.app.quit())
        self._signal_timer = QTimer()
        self._signal_timer.timeout.connect(lambda: None)
        self._signal_timer.start(200)

        self.hud = HudWindow()

        self.transport = HidTransport()
        self.transport.layerChanged.connect(self.hud.set_layer)
        self.transport.connectionChanged.connect(self._on_connection_changed)
        self.app.aboutToQuit.connect(self.transport.stop)

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(make_tray_icon(connected=False))
        self.tray.setToolTip("Halcyon Corne HUD")

        # Every QAction/QMenu needs a Python-side reference kept alive for
        # the app's lifetime (self.foo, not a local var) -- PySide's
        # ownership handoff to Qt's C++ side isn't reliable enough on its
        # own; a local-only reference can silently vanish from the menu.
        self.menu = QMenu()
        self.toggle_action = QAction("Keep HUD Visible")
        self.toggle_action.setCheckable(True)
        self.toggle_action.triggered.connect(self._toggle_hud)
        self.menu.addAction(self.toggle_action)
        self.menu.addSeparator()
        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(self.app.quit)
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.show()

        self.transport.start()

    def _on_connection_changed(self, connected: bool):
        self.hud.set_connected(connected)
        self.tray.setIcon(make_tray_icon(connected))
        self.tray.setToolTip("Halcyon Corne HUD -- connected" if connected else "Halcyon Corne HUD -- not connected")

    def _toggle_hud(self, checked: bool):
        self.hud.set_pinned(checked)

    def run(self):
        return self.app.exec()


def main():
    app = HalcyonHudApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
