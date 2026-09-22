import signal
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .hid_transport import HidTransport
from .hud_window import HudWindow, _log


def _hide_from_dock():
    """Make this a menu-bar-only "accessory" app on macOS: no Dock icon,
    and critically, the app itself can never become the frontmost
    application -- which is what was actually stealing keyboard focus.
    Window-level Qt flags (WindowDoesNotAcceptFocus, WA_ShowWithoutActivating)
    only control focus within an already-frontmost app; they can't stop the
    whole application from becoming frontmost in the first place, which is
    a Dock-visible "regular" app's default behavior whenever it shows any
    window. This must run AFTER QApplication() so it reuses the same
    NSApplication instance Qt already set up, not a second one.

    For a Briefcase-packaged build, the equivalent fix is LSUIElement=True
    in Info.plist; this covers running unpackaged via `python -m halcyon_hud`.
    """
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp, NSApplicationActivationPolicyAccessory

        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        _log("set NSApplicationActivationPolicyAccessory (no Dock icon)")
    except ImportError:
        _log("pyobjc not installed -- app will show in the Dock and can steal focus; "
             "pip3 install --break-system-packages pyobjc-framework-Cocoa to fix")


def make_tray_icon(connected: bool) -> QIcon:
    # Distinguish by SHAPE, not just color -- macOS tray icons are often
    # rendered as monochrome "template" images, which would silently
    # flatten a color-only distinction. The disconnected color also needs
    # to be a real, saturated color rather than near-grey (R~=G~=B) --
    # confirmed on hardware that a near-grayscale icon renders faint/
    # translucent (macOS's template-image auto-detection triggers on
    # colors that already look monochrome) and is easy to miss entirely
    # on a large/ultrawide display. The shape also now fills most of the
    # canvas instead of a small centered dot, for the same reason.
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor(0, 0, 0, 0))  # explicit transparent, not a bare 0 int
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(125, 211, 192) if connected else QColor(224, 122, 95)  # teal / terracotta
    painter.setPen(color)
    if connected:
        painter.setBrush(color)
        painter.drawEllipse(4, 4, 24, 24)  # filled dot = connected
    else:
        pen = painter.pen()
        pen.setWidth(3)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(6, 6, 20, 20)  # hollow ring = not connected
    painter.end()
    return QIcon(pixmap)


class HalcyonHudApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        _hide_from_dock()

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

        self._pinned = False
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
        # Not setCheckable(True): checkable QActions in a QSystemTrayIcon's
        # context menu have a known rendering quirk on macOS. Toggling the
        # label text instead sidesteps it entirely.
        self.toggle_action = QAction("Pin HUD Visible")
        self.toggle_action.triggered.connect(self._toggle_hud)
        self.menu.addAction(self.toggle_action)
        self.menu.addSeparator()
        self.quit_action = QAction("Quit")
        self.quit_action.triggered.connect(self.app.quit)
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: _log(f"tray activated, reason={reason}"))
        self.tray.show()
        _log(f"menu built with {len(self.menu.actions())} actions: {[a.text() for a in self.menu.actions()]}")
        _log(f"QSystemTrayIcon.isSystemTrayAvailable() = {QSystemTrayIcon.isSystemTrayAvailable()}")
        _log(f"tray.isVisible() = {self.tray.isVisible()}, tray.geometry() = {self.tray.geometry()}")
        QTimer.singleShot(1000, lambda: _log(
            f"tray.isVisible() [+1s] = {self.tray.isVisible()}, geometry = {self.tray.geometry()}"
        ))

        # Poll Qt's own idea of which window is active/focused, AND the real
        # AppKit ground truth (frontmost app + whether we're it) -- Qt's
        # isActiveWindow() turned out not to mean what we assumed, so this
        # time we also check what macOS itself thinks, independently.
        self._focus_poll_timer = QTimer()
        self._focus_poll_timer.timeout.connect(self._log_focus_state)
        self._focus_poll_timer.start(150)
        self._last_active = None
        self._last_we_are_active = None
        self._last_frontmost_name = None

        self.transport.start()

    def _log_focus_state(self):
        active = self.app.activeWindow()
        if active is not self._last_active:
            self._last_active = active
            _log(f"QApplication.activeWindow() changed to: {active!r}")

        try:
            from AppKit import NSRunningApplication, NSWorkspace

            we_are_active = NSRunningApplication.currentApplication().isActive()
            frontmost = NSWorkspace.sharedWorkspace().frontmostApplication()
            frontmost_name = frontmost.localizedName() if frontmost else None
            if we_are_active != self._last_we_are_active or frontmost_name != self._last_frontmost_name:
                self._last_we_are_active = we_are_active
                self._last_frontmost_name = frontmost_name
                _log(f"AppKit ground truth: weAreActive={we_are_active}, frontmostApp={frontmost_name!r}")
        except ImportError:
            pass

    def _on_connection_changed(self, connected: bool):
        self.hud.set_connected(connected)
        self.tray.setIcon(make_tray_icon(connected))
        self.tray.setToolTip("Halcyon Corne HUD -- connected" if connected else "Halcyon Corne HUD -- not connected")

    def _toggle_hud(self):
        pinned = not self._pinned
        _log(f"_toggle_hud() called, pinned now {pinned}")
        self._pinned = pinned
        self.toggle_action.setText("Unpin HUD" if pinned else "Pin HUD Visible")
        self.hud.set_pinned(pinned)

    def run(self):
        return self.app.exec()


def main():
    app = HalcyonHudApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
