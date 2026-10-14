"""Reading a keyboard's own keymap, so the HUD shows the keys that are
really on the board rather than only the bundled mak3r_layers.json.

Two ways, one per firmware:
- Halcyon (QMK/Vial): Vial's raw HID interface (usage page 0xFF60, usage
  0x61) answers VIA's standard keymap-read request, with no unlock needed.
  It's read-only and only sent when the HUD connects or on "Reload keymap",
  so the risk to a Vial app open at the same time is small. (QMK's console
  interface, which the HUD listens on, can only send, so the request can't
  go there.) See read_via_keymap().
- Typeractive (ZMK, tya-corne): the HUD writes "MAP?" to the console
  interface itself, and the firmware answers with MAP: lines on it. The
  reader thread collects those in hid_transport.py; MapCollector here does
  the parsing.

Both produce {(layer, row, col): keycode name}, in the Halcyon's matrix
row/col space, the same as KEY: events.
"""
import time

from .keycodes import decode

VIA_USAGE_PAGE = 0xFF60
VIA_USAGE = 0x61
VIA_REPORT_SIZE = 32

ID_GET_LAYER_COUNT = 0x11
ID_GET_BUFFER = 0x12
BUFFER_CHUNK = 28  # most bytes one get_buffer reply can carry

# The Halcyon's matrix, as Vial's dynamic keymap stores it (vial.json).
MATRIX_ROWS = 10
MATRIX_COLS = 6
REAL_KEYS = (
    {(r, c) for r in (0, 1, 2, 5, 6, 7) for c in range(6)}
    | {(3, c) for c in (3, 4, 5)}
    | {(8, c) for c in (3, 4, 5)}
)

VIA_TIMEOUT_S = 2.0
MAP_REQUEST = b"MAP?\n"


def find_via_path(hid, console_info):
    """The VIA interface on the same physical board as this console
    interface (same VID/PID/serial), or None. Only QMK/Vial boards have
    one."""
    for info in hid.enumerate(console_info["vendor_id"], console_info["product_id"]):
        if (info.get("usage_page") == VIA_USAGE_PAGE and info.get("usage") == VIA_USAGE
                and info.get("serial_number") == console_info.get("serial_number")):
            return info["path"]
    return None


def _via_request(device, command, payload=b""):
    report = bytes([command]) + payload
    device.write(b"\x00" + report.ljust(VIA_REPORT_SIZE, b"\x00"))
    deadline = time.monotonic() + VIA_TIMEOUT_S
    while time.monotonic() < deadline:
        reply = device.read(VIA_REPORT_SIZE, timeout=int(VIA_TIMEOUT_S * 1000))
        # A reply echoes the request's command byte and arguments. Skip
        # anything else, e.g. a reply meant for a Vial app that's also open.
        if reply and bytes(reply[: len(report)]) == report:
            return bytes(reply)
    raise TimeoutError(f"no VIA reply to command 0x{command:02X}")


def read_via_keymap(hid, via_path):
    """The Halcyon's live keymap, read through VIA. Raises on failure."""
    device = hid.Device(path=via_path)
    try:
        layers = _via_request(device, ID_GET_LAYER_COUNT)[1]
        size = layers * MATRIX_ROWS * MATRIX_COLS * 2
        buf = bytearray()
        for offset in range(0, size, BUFFER_CHUNK):
            n = min(BUFFER_CHUNK, size - offset)
            reply = _via_request(device, ID_GET_BUFFER, bytes([offset >> 8, offset & 0xFF, n]))
            buf += reply[4:4 + n]
    finally:
        device.close()

    keymap = {}
    for layer in range(layers):
        for r in range(MATRIX_ROWS):
            for c in range(MATRIX_COLS):
                if (r, c) in REAL_KEYS:
                    i = ((layer * MATRIX_ROWS + r) * MATRIX_COLS + c) * 2
                    keymap[(layer, r, c)] = decode((buf[i] << 8) | buf[i + 1])
    return keymap


def map_request_report():
    """The "MAP?" output report for the Typeractive's console interface
    (no report ID, so hidapi wants a leading 0x00)."""
    return b"\x00" + MAP_REQUEST.ljust(VIA_REPORT_SIZE, b"\x00")


class MapCollector:
    """Collects "MAP:<layer>,<row>,<col>,<keycode>" lines until
    "MAPEND:<count>". The keycode itself can contain commas (LT(1,KC_SPC)),
    so only the first three are separators."""

    def __init__(self):
        self.keymap = {}

    def add_line(self, text):
        """Returns the finished keymap on MAPEND (None if the count doesn't
        match), else None."""
        if text.startswith("MAP:"):
            try:
                layer, row, col, kc = text[4:].split(",", 3)
                self.keymap[(int(layer), int(row), int(col))] = kc
            except ValueError:
                pass
            return None
        if text.startswith("MAPEND:"):
            keymap, self.keymap = self.keymap, {}
            try:
                expected = int(text.split(":", 1)[1])
            except ValueError:
                return None
            return keymap if len(keymap) == expected else None
        return None
