"""Reads every connected keyboard's console interface: QMK's
CONSOLE_ENABLE on the Halcyon (halcyon-corne's hud_console.c), and the
Typeractive's raw HID interface that impersonates it (tya-corne's
hud_broadcast.c), over USB or Bluetooth.

HidManager re-enumerates every couple of seconds and runs one BoardReader
thread per console interface it finds, so the Qt event loop never blocks
on a HID read and unplugged boards drop out on their own. All signals are
re-emitted from HidManager, tagged with the reader's board id.

Wire format: plain text lines. From the keyboard: "LAYER:<n>",
"KEY:<row>,<col>,<0|1>", "BUILD:<date> <time>", and (Typeractive, asked for
with "MAP?") "MAP:<layer>,<row>,<col>,<keycode>" ... "MAPEND:<count>". See
halcyon-corne's CLAUDE.md, "Desktop HUD layer + keystroke broadcast", and
keymap_fetch.py.
"""
import ctypes
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .hud_window import _log
from .keymap_fetch import MapCollector, find_via_path, map_request_report, read_via_keymap

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

SCAN_INTERVAL_S = 2.0
SCAN_POLL_S = 0.1  # how often the scan wait re-checks _running
READ_TIMEOUT_MS = 500
MAP_TIMEOUT_S = 10.0  # a whole MAP: dump, which is slow over Bluetooth

# HID product string -> the name the HUD shows. Anything else shows its own
# product string.
FRIENDLY_NAMES = {
    "Halcyon Corne rev2": "Halcyon",
    "mak3rs-corne": "Typeractive",
}
BUS_NAMES = {"USB": "USB", "BLUETOOTH": "BLE"}


def find_console_devices():
    """hid.enumerate() entries for every connected console interface."""
    if hid is None:
        return []
    return [info for info in hid.enumerate()
            if info.get("usage_page") == CONSOLE_USAGE_PAGE and info.get("usage") == CONSOLE_USAGE]


def describe_board(info):
    """{"id", "name", "bus"} for one console interface. The id is its HID
    path: a board connected over both USB and Bluetooth is two boards here,
    and the HUD follows whichever one events actually arrive on."""
    path = info["path"]
    bus = getattr(info.get("bus_type"), "name", None)
    return {
        "id": path.decode(errors="replace") if isinstance(path, bytes) else str(path),
        "name": FRIENDLY_NAMES.get(info.get("product_string"), info.get("product_string") or "keyboard"),
        "bus": BUS_NAMES.get(bus, bus),
    }


class BoardReader(QThread):
    """Reads one console interface until it goes away, and fetches that
    board's keymap when it opens and whenever request_keymap() is called:
    through VIA if the board has a VIA interface (Halcyon), else by writing
    "MAP?" and collecting the MAP: reply (Typeractive). keymapLoaded carries
    {(layer, row, col): keycode name}, or None if the board couldn't say."""

    layerChanged = Signal(str, int)
    keyEvent = Signal(str, int, int, bool)
    buildInfo = Signal(str, str)
    keymapLoaded = Signal(str, object)

    def __init__(self, info):
        super().__init__()
        self.info = info
        self.board = describe_board(info)
        self._running = True
        self._keymap_requested = True  # fetch once on open
        self._map_deadline = None
        self._collector = MapCollector()

    def stop(self):
        self._running = False

    def request_keymap(self):
        self._keymap_requested = True

    def run(self):
        board_id = self.board["id"]
        try:
            device = hid.Device(path=self.info["path"])
        except Exception as e:
            _log(f"BoardReader: hid.Device(path={board_id!r}) raised: {e!r}")
            return
        _log(f"BoardReader: opened {self.board}")
        buf = b""
        try:
            while self._running:
                if self._keymap_requested:
                    self._keymap_requested = False
                    self._fetch_keymap(device)
                if self._map_deadline and time.monotonic() > self._map_deadline:
                    _log(f"BoardReader: no MAP: reply from {board_id!r} within {MAP_TIMEOUT_S}s")
                    self._map_deadline = None
                    self.keymapLoaded.emit(board_id, None)
                try:
                    data = device.read(64, timeout=READ_TIMEOUT_MS)
                except Exception as e:
                    _log(f"BoardReader: device.read() raised: {e!r}")
                    break  # device likely unplugged
                if not data:
                    continue
                buf += bytes(b for b in data if b != 0)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._handle_line(line.decode("utf-8", errors="replace").strip())
        finally:
            device.close()
            _log(f"BoardReader: closed {board_id!r}")

    def _fetch_keymap(self, device):
        board_id = self.board["id"]
        via_path = find_via_path(hid, self.info)
        if via_path is not None:
            try:
                keymap = read_via_keymap(hid, via_path)
            except Exception as e:
                _log(f"BoardReader: VIA keymap read from {board_id!r} failed: {e!r}")
                keymap = None
            else:
                _log(f"BoardReader: read {len(keymap)} keys from {board_id!r} over VIA")
            self.keymapLoaded.emit(board_id, keymap)
            return
        try:
            device.write(map_request_report())
        except Exception as e:
            _log(f"BoardReader: writing MAP? to {board_id!r} failed: {e!r}")
            self.keymapLoaded.emit(board_id, None)
            return
        _log(f"BoardReader: sent MAP? to {board_id!r}")
        self._map_deadline = time.monotonic() + MAP_TIMEOUT_S

    def _handle_line(self, text):
        board_id = self.board["id"]
        if text.startswith("LAYER:"):
            try:
                layer = int(text.split(":", 1)[1])
            except ValueError:
                return
            self.layerChanged.emit(board_id, layer)
        elif text.startswith("KEY:"):
            try:
                row_str, col_str, pressed_str = text.split(":", 1)[1].split(",")
                self.keyEvent.emit(board_id, int(row_str), int(col_str), pressed_str == "1")
            except ValueError:
                return
        elif text.startswith("BUILD:"):
            self.buildInfo.emit(board_id, text.split(":", 1)[1].strip())
        elif text.startswith(("MAP:", "MAPEND:")):
            # Accepted even when this reader didn't ask: a Typeractive on
            # both USB and Bluetooth answers a MAP? from either one on
            # whichever it's currently sending on.
            keymap = self._collector.add_line(text)
            if text.startswith("MAPEND:"):
                self._map_deadline = None
                _log(f"BoardReader: MAP: dump from {board_id!r} "
                     f"{'complete, ' + str(len(keymap)) + ' keys' if keymap else 'incomplete'}")
                self.keymapLoaded.emit(board_id, keymap)


class HidManager(QThread):
    """Finds console interfaces and runs a BoardReader for each. Lives in
    the main thread (its run() doesn't), so the readers' signals, relayed
    through these, arrive on the main thread. boardsChanged carries the
    describe_board() dicts of every board currently open."""

    layerChanged = Signal(str, int)
    keyEvent = Signal(str, int, int, bool)
    buildInfo = Signal(str, str)
    keymapLoaded = Signal(str, object)
    boardsChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._readers = {}  # HID path -> BoardReader
        self._lock = threading.Lock()

    def stop(self):
        self._running = False
        self.wait()  # unbounded: every wait in run() re-checks _running often
        for reader in self._readers.values():
            reader.stop()
            reader.wait()

    def reload_keymaps(self):
        with self._lock:
            for reader in self._readers.values():
                reader.request_keymap()

    def _interruptible_sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(min(SCAN_POLL_S, max(0.0, deadline - time.monotonic())))

    def _scan(self):
        try:
            found = {info["path"]: info for info in find_console_devices()}
        except Exception as e:
            _log(f"HidManager: enumerate raised: {e!r}")
            return
        changed = False
        with self._lock:
            for path, reader in list(self._readers.items()):
                if reader.isFinished():
                    del self._readers[path]
                    changed = True
            for path, info in found.items():
                if path in self._readers:
                    continue
                reader = BoardReader(info)
                reader.layerChanged.connect(self.layerChanged)
                reader.keyEvent.connect(self.keyEvent)
                reader.buildInfo.connect(self.buildInfo)
                reader.keymapLoaded.connect(self.keymapLoaded)
                self._readers[path] = reader
                reader.start()
                changed = True
            boards = [r.board for r in self._readers.values()]
        if changed:
            self.boardsChanged.emit(boards)

    def run(self):
        _log(f"HidManager.run() starting, hid module = {hid!r}")
        if hid is None:
            self.boardsChanged.emit([])
            return
        while self._running:
            self._scan()
            self._interruptible_sleep(SCAN_INTERVAL_S)
