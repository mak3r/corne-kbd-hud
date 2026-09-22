"""The HUD itself: a frameless, always-on-top overlay showing the active
layer's key layout, colored to match the real keyboard's RGB (from
rgb_layers.csv, baked into mak3r_layers.json)."""
import colorsys
import json
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

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
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
            | Qt.WindowDoesNotAcceptFocus  # critical: without this the HUD steals keyboard
            # focus from whatever app you're actually typing into when shown/raised.
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.NoFocus)

        self._positions = compute_positions()
        self._layers = json.loads(DATA_PATH.read_text())
        self._layer_index = 0
        self._connected = False

        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

        width_units, height_units = bounding_size()
        w = int(width_units * (KEY_SIZE + GAP) + MARGIN * 2)
        h = int(height_units * (KEY_SIZE + GAP) + MARGIN * 2)
        self.setFixedSize(w, h)
        self._place_bottom_right()

        self._key_by_rc = {}
        self._rebuild_key_lookup()

    def _place_bottom_right(self):
        screen = self.screen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _rebuild_key_lookup(self):
        layer = self._layers[self._layer_index]
        self._key_by_rc = {(k["r"], k["c"]): k for k in layer["keys"]}

    def set_connected(self, connected: bool):
        self._connected = connected
        self.update()

    def set_layer(self, layer_index: int):
        if not (0 <= layer_index < len(self._layers)):
            return
        self._layer_index = layer_index
        self._rebuild_key_lookup()
        self.update()

        if layer_index == AUTO_HIDE_LAYER:
            self._auto_hide_timer.start(AUTO_HIDE_DELAY_MS)
        else:
            self._auto_hide_timer.stop()
            self.show()
            self.raise_()

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
