"""Reads layer-change events from the keyboard's CONSOLE_ENABLE USB HID
interface (see halcyon-corne's hud_console.c). Runs its own thread so the
Qt event loop never blocks on a HID read; reconnects automatically if the
keyboard is unplugged or the wrong half becomes master.

Wire format: plain text lines, "LAYER:<n>\\n" (see halcyon-corne's
CLAUDE.md, "Desktop HUD layer broadcast").
"""
import time

from PySide6.QtCore import QThread, Signal

try:
    import hid
except ImportError:
    hid = None

# The "PJRC Teensy compatible" console usage page/usage QMK's CONSOLE_ENABLE
# always uses, regardless of the keyboard's VID/PID.
CONSOLE_USAGE_PAGE = 0xFF31
CONSOLE_USAGE = 0x74

RECONNECT_DELAY_S = 2.0
READ_TIMEOUT_MS = 500


def find_console_device_path():
    """Returns the HID path of the first matching console interface, or
    None if the keyboard isn't connected (or the wrong half is master)."""
    if hid is None:
        return None
    for info in hid.enumerate():
        if info.get("usage_page") == CONSOLE_USAGE_PAGE and info.get("usage") == CONSOLE_USAGE:
            return info["path"]
    return None


class HidTransport(QThread):
    """Background thread: connects to the console interface, parses
    incoming lines, and emits layerChanged(int) whenever "LAYER:<n>"
    arrives. Emits connectionChanged(bool) when the device is found/lost."""

    layerChanged = Signal(int)
    connectionChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._connected = False

    def stop(self):
        self._running = False
        self.wait(READ_TIMEOUT_MS + 500)

    def _set_connected(self, connected):
        if connected != self._connected:
            self._connected = connected
            self.connectionChanged.emit(connected)

    def run(self):
        if hid is None:
            self._set_connected(False)
            return

        while self._running:
            path = find_console_device_path()
            if path is None:
                self._set_connected(False)
                time.sleep(RECONNECT_DELAY_S)
                continue

            try:
                device = hid.Device(path=path)
            except Exception:
                self._set_connected(False)
                time.sleep(RECONNECT_DELAY_S)
                continue

            self._set_connected(True)
            buf = b""
            try:
                while self._running:
                    try:
                        data = device.read(64, timeout=READ_TIMEOUT_MS)
                    except Exception:
                        break  # device likely unplugged; fall through to reconnect
                    if not data:
                        continue
                    chunk = bytes(b for b in data if b != 0)
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self._handle_line(line.decode("utf-8", errors="replace").strip())
            finally:
                device.close()
                self._set_connected(False)

    def _handle_line(self, text):
        if text.startswith("LAYER:"):
            try:
                layer = int(text.split(":", 1)[1])
            except ValueError:
                return
            self.layerChanged.emit(layer)
