import ctypes
import signal
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .hid_transport import HidManager
from .hud_window import HudWindow, _log


def _request_hid_access():
    """Explicitly trigger macOS's Input Monitoring permission prompt via
    IOHIDRequestAccess, on the main thread, before the app is demoted to
    an accessory (no-Dock-icon, never-foreground) app.

    hidapi fires an equivalent request implicitly the first time it opens
    a device -- but that happens from a BoardReader background QThread,
    after _hide_from_dock() has already run. Confirmed on hardware: that
    implicit request never produces a system prompt OR even an entry in
    System Settings -> Privacy & Security -> Input Monitoring for this
    app at all (not "denied", just never registered) -- apparently
    because an accessory app that never becomes foreground can't host
    the consent sheet. Calling this ourselves, synchronously, while the
    app can still become foreground, is the fix; it returns immediately
    if permission was already granted/denied in an earlier run.
    """
    if sys.platform != "darwin":
        return True
    try:
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDRequestAccess.restype = ctypes.c_bool
        iokit.IOHIDRequestAccess.argtypes = [ctypes.c_int32]
        kIOHIDRequestTypeListenEvent = 1
        granted = iokit.IOHIDRequestAccess(kIOHIDRequestTypeListenEvent)
        _log(f"IOHIDRequestAccess(ListenEvent) -> granted={granted}")
        return granted
    except Exception as e:
        _log(f"_request_hid_access failed: {e!r}")
        return True


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
    in Info.plist; this covers running unpackaged via `python -m corne_kbd_hud`.
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


class CorneHudApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        _request_hid_access()
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
        # Every open console interface, by board id (hid_transport.py's
        # describe_board() plus "layer", "build", and "keymap": None until
        # the board answers, then its keymap, or False if it couldn't).
        # The HUD follows whichever board an event last came from.
        self._boards = {}
        self._active = None
        self.hud = HudWindow()

        self.transport = HidManager()
        self.transport.layerChanged.connect(self._on_layer)
        self.transport.keyEvent.connect(self._on_key)
        self.transport.buildInfo.connect(self._on_build_info)
        self.transport.keymapLoaded.connect(self._on_keymap_loaded)
        self.transport.boardsChanged.connect(self._on_boards_changed)
        self.app.aboutToQuit.connect(self.transport.stop)

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(make_tray_icon(connected=False))
        self.tray.setToolTip("Corne HUD")

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
        # Re-read every board's keymap, e.g. after changing a key in Vial.
        self.reload_action = QAction("Reload Keymap")
        self.reload_action.triggered.connect(self._reload_keymaps)
        self.menu.addAction(self.reload_action)
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

    def _on_boards_changed(self, boards):
        _log(f"boards: {boards}")
        previous = self._boards
        self._boards = {b["id"]: previous.get(b["id"], {**b, "layer": 0, "build": None, "keymap": None})
                        for b in boards}
        if self._active not in self._boards:
            # Nothing typed on yet (or the active board went away): show
            # one of the remaining boards until an event says otherwise.
            self._active = None
            if self._boards:
                self._activate(next(iter(self._boards)))
        connected = bool(self._boards)
        self.hud.set_connected(connected)
        self.tray.setIcon(make_tray_icon(connected))
        self._update_board_display()

    def _activate(self, board_id):
        """Make the HUD show this board: its keymap, and its last layer."""
        if board_id == self._active or board_id not in self._boards:
            return
        _log(f"active board -> {board_id!r}")
        self._active = board_id
        board = self._boards[board_id]
        self.hud.set_keymap(board["keymap"] or None)
        self.hud.set_layer(board["layer"])
        self._update_board_display()

    def _on_layer(self, board_id, layer):
        if board_id in self._boards:
            self._boards[board_id]["layer"] = layer
        self._activate(board_id)
        if board_id == self._active:
            self.hud.set_layer(layer)

    def _on_key(self, board_id, row, col, pressed):
        self._activate(board_id)
        if board_id == self._active:
            self.hud.set_key_state(row, col, pressed)

    def _on_keymap_loaded(self, board_id, keymap):
        board = self._boards.get(board_id)
        if board is None:
            return
        _log(f"keymap from {board_id!r}: {len(keymap) if keymap else 'none'}")
        board["keymap"] = keymap or False
        if board_id == self._active:
            self.hud.set_keymap(keymap or None)
            self._update_board_display()

    def _on_build_info(self, board_id, build_info: str):
        _log(f"buildInfo from {board_id!r}: {build_info!r}")
        if board_id in self._boards:
            self._boards[board_id]["build"] = build_info
            if board_id == self._active:
                self._update_board_display()

    def _reload_keymaps(self):
        for board in self._boards.values():
            board["keymap"] = None
        if self._active:
            self.hud.set_keymap(None)
        self._update_board_display()
        self.transport.reload_keymaps()

    def _update_board_display(self):
        """The active board's name in the HUD header and the tray tooltip.
        A trailing * means its labels are the bundled ones, not the
        board's own (it hasn't answered yet, or couldn't)."""
        board = self._boards.get(self._active)
        if board is None:
            self.hud.set_board_label("")
            self.tray.setToolTip("Corne HUD -- not connected")
            return
        name = f"{board['name']} · {board['bus']}" if board["bus"] else board["name"]
        star = "" if board["keymap"] else " *"
        self.hud.set_board_label(name + star)
        if board["keymap"]:
            source = "keymap from the keyboard"
        elif board["keymap"] is None:
            source = "reading keymap..."
        else:
            source = "keymap not available, showing bundled labels"
        build = f", built {board['build']}" if board["build"] else ""
        others = len(self._boards) - 1
        also = f" (+{others} more connected)" if others else ""
        self.tray.setToolTip(f"Corne HUD -- {name}{build}; {source}{also}")

    def _toggle_hud(self):
        pinned = not self._pinned
        _log(f"_toggle_hud() called, pinned now {pinned}")
        self._pinned = pinned
        self.toggle_action.setText("Unpin HUD" if pinned else "Pin HUD Visible")
        self.hud.set_pinned(pinned)

    def run(self):
        return self.app.exec()


def main():
    app = CorneHudApp()
    sys.exit(app.run())


if __name__ == "__main__":
    main()
