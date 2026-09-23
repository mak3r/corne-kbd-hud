"""The HUD itself: a frameless, always-on-top overlay showing the active
layer's key layout, colored to match the real keyboard's RGB (from
rgb_layers.csv, baked into mak3r_layers.json)."""
import colorsys
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRectF, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}] {msg}", file=sys.stderr, flush=True)


def _native_ns_window(widget):
    """The underlying NSWindow for a Qt widget, via PyObjC. Qt's own
    show()/raise_() were confirmed (empirically, with AppKit-level
    polling) to activate the owning application as a side effect on
    macOS, regardless of window type/flags -- every Qt-level knob for
    this turned out to control window-level focus, not app activation.
    orderFront_()/orderOut_() on the real NSWindow are the actual Cocoa
    APIs documented to show/hide a window WITHOUT activating its app,
    which is how legitimate non-activating overlays are built. Returns
    None (caller should fall back to Qt's own show()/hide()) if PyObjC
    isn't available or this isn't macOS."""
    if sys.platform != "darwin":
        return None
    try:
        import objc

        view_ptr = int(widget.winId())
        ns_view = objc.objc_object(c_void_p=view_ptr)
        return ns_view.window()
    except Exception as e:
        _log(f"_native_ns_window failed: {e!r}")
        return None


def _set_collection_behavior_all_spaces(ns_window):
    """Make the overlay follow across every Space/virtual desktop, and even
    show over apps in native full-screen mode -- the same NSWindow
    technique apps like Keymapp use for an always-visible utility overlay.
    There's no OS-level setting for this on an app with no Dock icon (the
    usual Mission Control "Assign To -> All Desktops" option lives on a
    Dock icon's right-click menu, which this app deliberately doesn't
    have); it has to be requested by the window itself. Without this, a
    window only ever shows on the Space it was last shown on."""
    try:
        from AppKit import (
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorStationary,
        )

        ns_window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        _log("set NSWindowCollectionBehavior for all-Spaces visibility")
    except Exception as e:
        _log(f"_set_collection_behavior_all_spaces failed: {e!r}")


from .keyboard_layout import bounding_size, compute_positions

DATA_PATH = Path(__file__).parent / "data" / "mak3r_layers.json"

KEY_SIZE = 34
GAP = 4
MARGIN = 14
CORNER_RADIUS = 7

AUTO_HIDE_LAYER = 0
AUTO_HIDE_DELAY_MS = 1500

BG_UNLIT = QColor(38, 42, 53)
BORDER = QColor(54, 60, 74)
TEXT_LIGHT = QColor(243, 244, 248)
TEXT_DARK = QColor(20, 21, 26)
PRESSED_OUTLINE = QColor(255, 255, 255)


def hsv_to_qcolor(hsv):
    """QMK's HSV is 0-255 per channel; Python's colorsys wants 0-1."""
    if not hsv:
        return None
    h, s, v = hsv
    if h == 0 and s == 0 and v == 0:
        return None
    r, g, b = colorsys.hsv_to_rgb(h / 255, s / 255, v / 255)
    return QColor(int(r * 255), int(g * 255), int(b * 255))


def text_color_for(bg: QColor) -> QColor:
    luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
    return TEXT_DARK if luminance > 150 else TEXT_LIGHT


class HudWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.ToolTip  # Qt.Tool + WindowDoesNotAcceptFocus still let something
            # steal focus on macOS -- Qt.ToolTip is the window category Qt
            # itself guarantees never takes focus, on every platform, since
            # that's literally what tooltips require to work at all.
            | Qt.NoDropShadowWindowHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # ...but on macOS, .show() can still ACTIVATE (steal focus to) a window
        # regardless of the flag above unless this is also set -- this is the
        # one that actually stops every layer-key show() from stealing focus.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)

        self._positions = compute_positions()
        self._layers = json.loads(DATA_PATH.read_text())
        self._layer_index = 0
        self._connected = False
        self._pinned = False
        self._pressed_rc = set()
        self._drag_offset = None

        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(lambda: self._native_hide())

        width_units, height_units = bounding_size()
        w = int(width_units * (KEY_SIZE + GAP) + MARGIN * 2)
        h = int(height_units * (KEY_SIZE + GAP) + MARGIN * 2)
        self.setFixedSize(w, h)

        self._settings = QSettings("mak3r", "CorneHUD")
        self._restore_position()

        self._key_by_rc = {}
        self._rebuild_key_lookup()

        # Force Qt to create the native window/view (winId() only valid
        # after this) and keep Qt's OWN idea of "shown" true for the app's
        # whole lifetime, so update()/paintEvent keep working normally --
        # actual on-screen visibility is controlled separately, below, via
        # the native NSWindow directly (orderFront_/orderOut_), decoupled
        # from Qt's show()/hide() state entirely.
        self.show()
        self._ns_window = _native_ns_window(self)
        if self._ns_window is not None:
            self._ns_window.orderOut_(None)  # screen-hidden; Qt still thinks "shown"
            _set_collection_behavior_all_spaces(self._ns_window)
        else:
            self.hide()  # no native handle available -- fall back to Qt's own tracking
        _log(f"native NSWindow acquired: {self._ns_window is not None}")

    def _native_show(self):
        if self._ns_window is not None:
            # orderFront_() alone was confirmed (via direct AppKit polling)
            # to still activate this app -- canBecomeKeyWindow is False,
            # the window never becomes key, yet NSRunningApplication's
            # isActive() flips True anyway. AppKit doesn't expose a way to
            # order a window front without that side effect, so instead we
            # let it happen and immediately hand activation back to
            # whatever app was frontmost a moment ago -- confirmed in
            # isolated testing to leave the window visible while restoring
            # the previous app as the one receiving keystrokes.
            prev_frontmost = None
            try:
                from AppKit import NSRunningApplication, NSWorkspace

                candidate = NSWorkspace.sharedWorkspace().frontmostApplication()
                if candidate is not None and candidate.processIdentifier() != NSRunningApplication.currentApplication().processIdentifier():
                    prev_frontmost = candidate
            except Exception as e:
                _log(f"_native_show: frontmost lookup failed: {e!r}")

            self._ns_window.orderFront_(None)

            if prev_frontmost is not None:
                try:
                    from AppKit import NSApplicationActivateIgnoringOtherApps

                    prev_frontmost.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
                except Exception as e:
                    _log(f"_native_show: hand-back activation failed: {e!r}")
        else:
            self.show()
            self.raise_()

    def _native_hide(self):
        if self._ns_window is not None:
            self._ns_window.orderOut_(None)
        else:
            self.hide()

    def _place_bottom_right(self):
        screen = self.screen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _restore_position(self):
        """Use the last dragged-to position if one was saved and it's still
        roughly on-screen (e.g. a monitor that was unplugged since last
        run) -- otherwise fall back to the original bottom-right default."""
        saved = self._settings.value("window_pos")
        if saved is not None:
            point = saved if isinstance(saved, QPoint) else QPoint(*saved)
            for screen in self.screen().virtualSiblings():
                if screen.availableGeometry().intersects(
                    QRectF(point.x(), point.y(), self.width(), self.height()).toRect()
                ):
                    self.move(point)
                    return
        self._place_bottom_right()

    def _save_position(self):
        self._settings.setValue("window_pos", self.pos())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._save_position()
            event.accept()

    def _rebuild_key_lookup(self):
        layer = self._layers[self._layer_index]
        self._key_by_rc = {(k["r"], k["c"]): k for k in layer["keys"]}

    def set_connected(self, connected: bool):
        self._connected = connected
        self.update()

    def set_key_state(self, row: int, col: int, pressed: bool):
        """Highlight a key while it's physically held down, as a visual
        checkpoint while learning a new layout -- driven by the firmware's
        per-keystroke KEY:<row>,<col>,<pressed> broadcast (see
        hid_transport.py), not just layer changes."""
        if pressed:
            self._pressed_rc.add((row, col))
        else:
            self._pressed_rc.discard((row, col))
        self.update()

    def set_pinned(self, pinned: bool):
        """Keep the HUD visible regardless of layer -- overrides auto-hide
        until unpinned. For learning a new layout, where you want the
        reference up continuously, not just flashing on layer changes."""
        _log(f"set_pinned({pinned}) called")
        self._pinned = pinned
        if pinned:
            self._auto_hide_timer.stop()
            self._native_show()
        elif self._layer_index == AUTO_HIDE_LAYER:
            self._auto_hide_timer.start(AUTO_HIDE_DELAY_MS)

    def set_layer(self, layer_index: int):
        if not (0 <= layer_index < len(self._layers)):
            return
        _log(f"set_layer({layer_index}) called, pinned={self._pinned}")
        self._layer_index = layer_index
        self._rebuild_key_lookup()
        self.update()

        if self._pinned:
            return  # stays visible/showing this layer regardless of which one

        if layer_index == AUTO_HIDE_LAYER:
            self._auto_hide_timer.start(AUTO_HIDE_DELAY_MS)
        else:
            self._auto_hide_timer.stop()
            _log("  -> calling _native_show()")
            self._native_show()
            _log(f"  -> after native show: isActiveWindow={self.isActiveWindow()}, isVisible={self.isVisible()}")

    def event(self, e):
        if e.type() in (
            QEvent.FocusIn, QEvent.FocusOut, QEvent.WindowActivate,
            QEvent.WindowDeactivate, QEvent.Show, QEvent.Hide,
        ):
            _log(f"HudWindow event: {e.type()}")
        return super().event(e)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Panel background.
        panel = QPainterPath()
        panel.addRoundedRect(QRectF(0, 0, self.width(), self.height()), 12, 12)
        painter.fillPath(panel, QColor(15, 17, 21, 235))

        font = QFont("Menlo" if self._has_font("Menlo") else "Monospace")
        font.setPixelSize(11)
        painter.setFont(font)
        metrics = QFontMetrics(font)

        for pos in self._positions:
            key = self._key_by_rc.get((pos.row, pos.col))
            if key is None:
                continue

            cx = MARGIN + pos.x * (KEY_SIZE + GAP) + KEY_SIZE / 2
            cy = MARGIN + pos.y * (KEY_SIZE + GAP) + KEY_SIZE / 2

            painter.save()
            painter.translate(cx, cy)
            if pos.rotation:
                painter.rotate(pos.rotation)

            rect = QRectF(-KEY_SIZE / 2, -KEY_SIZE / 2, KEY_SIZE, KEY_SIZE)
            path = QPainterPath()
            path.addRoundedRect(rect, CORNER_RADIUS, CORNER_RADIUS)

            fill = hsv_to_qcolor(key.get("hsv")) or BG_UNLIT
            painter.fillPath(path, fill)

            is_pressed = (pos.row, pos.col) in self._pressed_rc
            if is_pressed:
                pen = painter.pen()
                pen.setColor(PRESSED_OUTLINE)
                pen.setWidth(2)
                painter.setPen(pen)
            else:
                painter.setPen(BORDER)
            painter.drawPath(path)

            painter.setPen(text_color_for(fill))
            label = key["label"]
            # Shrink very long labels (mod combos) to fit.
            while metrics.horizontalAdvance(label) > KEY_SIZE - 4 and len(label) > 1:
                label = label[:-1]
            painter.drawText(rect, Qt.AlignCenter, label)

            painter.restore()

        # Layer name + connection status, top-left corner.
        painter.setPen(TEXT_LIGHT)
        header_font = QFont(font)
        header_font.setPixelSize(12)
        header_font.setBold(True)
        painter.setFont(header_font)
        layer_name = self._layers[self._layer_index]["name"]
        painter.drawText(
            QRectF(MARGIN, 2, self.width() - MARGIN * 2, 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            f"Layer {self._layer_index} · {layer_name}",
        )
        if not self._connected:
            painter.setPen(QColor(224, 113, 107))
            painter.drawText(
                QRectF(MARGIN, 2, self.width() - MARGIN * 2, 16),
                Qt.AlignRight | Qt.AlignVCenter,
                "disconnected",
            )

    @staticmethod
    def _has_font(name):
        from PySide6.QtGui import QFontDatabase

        return name in QFontDatabase.families()
