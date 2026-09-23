"""Reads layer-change events from the keyboard's CONSOLE_ENABLE USB HID
interface (see halcyon-corne's hud_console.c). Runs its own thread so the
Qt event loop never blocks on a HID read; reconnects automatically if the
keyboard is unplugged or the wrong half becomes master.

Wire format: plain text lines, "LAYER:<n>\\n" (see halcyon-corne's
CLAUDE.md, "Desktop HUD layer broadcast").
"""
import ctypes
import sys
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .hud_window import _log

# The `hid` package (a ctypes wrapper) loads the native hidapi library with
# `ctypes.cdll.LoadLibrary('libhidapi.dylib')` -- a BARE name, not a path --
# relying on dyld's own search to find it. That works for a plain `python3`
# process launched from a shell, which inherits Homebrew's environment --
# but a Briefcase-packaged .app's bundled/ad-hoc-signed interpreter can't
# find it at all (confirmed: ImportError listing every name variant it
# tried). Two things that looked like they'd fix this did NOT, both
# confirmed on hardware:
#   - Preloading the real file by absolute path first: doesn't help,
#     because (confirmed via `otool -D`) the dylib's own recorded install
#     name (/opt/homebrew/opt/hidapi/lib/libhidapi.0.dylib) differs from
#     the bare name `hid` asks for, so dyld doesn't recognize it as
#     already loaded and repeats its own failing bare-name search.
#   - Setting DYLD_LIBRARY_PATH at runtime: macOS ignores DYLD_* env vars
#     for ad-hoc-signed/hardened-runtime binaries as a security measure,
#     regardless of when they're set.
# What actually works: bypass dyld's bare-name search entirely by
# monkeypatching ctypes.cdll.LoadLibrary for the duration of `import hid`,
# so hid's own `ctypes.cdll.LoadLibrary('libhidapi.dylib')` call gets
# redirected straight to an explicit CDLL(absolute_path) we control. This
# assumes hidapi was installed the way the README's "Running it" section
# already asks for (`brew install hidapi`), which holds for anyone
# building this themselves; it is not a portable fix for handing the
# built .app to someone without Homebrew.
def _import_hid_with_homebrew_hidapi():
    if sys.platform != "darwin":
        import hid as hid_module

        return hid_module

    homebrew_dylib = None
    for candidate in (
        "/opt/homebrew/lib/libhidapi.dylib",  # Homebrew, Apple Silicon
        "/usr/local/lib/libhidapi.dylib",  # Homebrew, Intel
    ):
        if Path(candidate).exists():
            homebrew_dylib = candidate
            break

    if homebrew_dylib is None:
        _log("HidTransport: no Homebrew libhidapi.dylib found (checked /opt/homebrew/lib, /usr/local/lib)")
        import hid as hid_module  # let it fail with hid's own ImportError

        return hid_module

    original_load_library = ctypes.cdll.LoadLibrary

    def redirect_load_library(name):
        if name == "libhidapi.dylib":
            _log(f"HidTransport: redirecting hid's libhidapi.dylib load to {homebrew_dylib}")
            return ctypes.CDLL(homebrew_dylib)
        return original_load_library(name)

    ctypes.cdll.LoadLibrary = redirect_load_library
    try:
        import hid as hid_module

        return hid_module
    finally:
        ctypes.cdll.LoadLibrary = original_load_library


try:
    hid = _import_hid_with_homebrew_hidapi()
except ImportError as e:
    hid = None
    _log(f"HidTransport: 'import hid' failed: {e!r}")

# The "PJRC Teensy compatible" console usage page/usage QMK's CONSOLE_ENABLE
# always uses, regardless of the keyboard's VID/PID.
CONSOLE_USAGE_PAGE = 0xFF31
CONSOLE_USAGE = 0x74

RECONNECT_DELAY_S = 2.0
RECONNECT_POLL_S = 0.1  # how often the reconnect wait re-checks _running
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
    arrives, or keyEvent(row, col, pressed) whenever "KEY:<row>,<col>,<0|1>"
    arrives (see halcyon-corne's hud_console.c). Emits connectionChanged(bool)
    when the device is found/lost."""

    layerChanged = Signal(int)
    keyEvent = Signal(int, int, bool)
    connectionChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._connected = False

    def stop(self):
        self._running = False
        self.wait()  # unbounded: every internal wait is now interruptible,
        # so this always returns promptly rather than needing a guessed
        # timeout -- an earlier version used a fixed timeout here and could
        # abort the process if _running hadn't been rechecked yet.

    def _interruptible_sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(min(RECONNECT_POLL_S, max(0.0, deadline - time.monotonic())))

    def _set_connected(self, connected):
        if connected != self._connected:
            self._connected = connected
            self.connectionChanged.emit(connected)

    def run(self):
        _log(f"HidTransport.run() starting, hid module = {hid!r}")
        if hid is None:
            self._set_connected(False)
            return

        while self._running:
            try:
                path = find_console_device_path()
            except Exception as e:
                _log(f"HidTransport: find_console_device_path() raised: {e!r}")
                path = None
            if path is None:
                self._set_connected(False)
                self._interruptible_sleep(RECONNECT_DELAY_S)
                continue

            try:
                device = hid.Device(path=path)
            except Exception as e:
                _log(f"HidTransport: hid.Device(path={path!r}) raised: {e!r}")
                self._set_connected(False)
                self._interruptible_sleep(RECONNECT_DELAY_S)
                continue

            _log(f"HidTransport: opened device at path={path!r}")
            self._set_connected(True)
            buf = b""
            try:
                while self._running:
                    try:
                        data = device.read(64, timeout=READ_TIMEOUT_MS)
                    except Exception as e:
                        _log(f"HidTransport: device.read() raised: {e!r}")
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
                _log("HidTransport: device closed, disconnected")

    def _handle_line(self, text):
        if text.startswith("LAYER:"):
            try:
                layer = int(text.split(":", 1)[1])
            except ValueError:
                return
            self.layerChanged.emit(layer)
        elif text.startswith("KEY:"):
            try:
                row_str, col_str, pressed_str = text.split(":", 1)[1].split(",")
                self.keyEvent.emit(int(row_str), int(col_str), pressed_str == "1")
            except ValueError:
                return
