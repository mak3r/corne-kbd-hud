"""QMK keycode names -> HUD key labels, and QMK keycode numbers -> names.

Every keymap the HUD shows ends up as QMK keycode *names*, spelled the way
halcyon-corne's keymap.json spells them (KC_ESCAPE, LCTL_T(KC_D), MO(1)):
- the bundled mak3r_layers.json (scripts/generate_layout_data.py, which
  imports the label code from here)
- the Typeractive's MAP: lines (tya-corne sends keymap.json's own names)
- the Halcyon's live keymap, read as 16-bit numbers and turned back into
  names by decode()

so one set of label rules covers all three.
"""
import json
import re
from pathlib import Path

RENAMES = {
    "BSPC": "Bksp", "ENTER": "Enter", "ESCAPE": "Esc", "SPACE": "Space", "QUOTE": "'",
    "SCLN": ";", "SLASH": "/", "COMMA": ",", "DOT": ".", "GRAVE": "`", "BSLS": "\\",
    "LBRC": "[", "RBRC": "]", "MUTE": "Mute", "NO": "·", "TRNS": "―", "DELETE": "Del",
    "PGUP": "PgUp", "PGDN": "PgDn", "HOME": "Home", "END": "End",
    "LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓",
    "KP_MINUS": "-", "KP_EQUAL": "=", "KP_1": "1", "KP_2": "2", "KP_3": "3", "KP_4": "4", "KP_5": "5",
    "KP_6": "6", "KP_7": "7", "KP_8": "8", "KP_9": "9", "KP_0": "0",
    "KP_DOT": ".", "KP_PLUS": "+", "KP_SLASH": "/", "KP_ASTERISK": "*", "KP_COMMA": ",", "KP_ENTER": "Enter",
    "VOLU": "Vol+", "VOLD": "Vol-", "MPLY": "Play", "MRWD": "Rwd", "MFFD": "Ffd", "PSCR": "PrSc",
    "MINUS": "-", "EQUAL": "=",
    # Mouse buttons -- legacy KC_BTNn / KC_MS_BTNn and current QMK MS_BTNn
    # (the latter has no KC_ prefix; short_label strips "MS_" for it).
    "BTN1": "LClk", "BTN2": "RClk", "BTN3": "MClk",
    "MS_BTN1": "LClk", "MS_BTN2": "RClk", "MS_BTN3": "MClk",
    # Mouse movement / wheel (layer 4, J/K/L/; and M/,/./slash).
    "MS_LEFT": "Ms←", "MS_DOWN": "Ms↓", "MS_UP": "Ms↑", "MS_RIGHT": "Ms→",
    "MS_WH_LEFT": "Wh←", "MS_WH_DOWN": "Wh↓", "MS_WH_UP": "Wh↑", "MS_WH_RIGHT": "Wh→",
    # Wireless-only keys in the shared layout (Typeractive; nothing on the Halcyon).
    "OUT_TOG": "USB/BT", "BT_CLR": "BT Clr",
    "BT_SEL_0": "BT 0", "BT_SEL_1": "BT 1", "BT_SEL_2": "BT 2", "BT_SEL_3": "BT 3", "BT_SEL_4": "BT 4",
}
MOD_SYMS = {
    "LSFT": "⇧", "RSFT": "⇧", "LCTL": "⌃", "RCTL": "⌃",
    "LGUI": "⌘", "RGUI": "⌘", "LALT": "⌥", "RALT": "⌥",
    "SGUI": "⇧⌘", "SCTL": "⇧⌃", "SALT": "⇧⌥",
    "MEH": "⌃⌥⇧", "HYPR": "⌃⌥⇧⌘",
}
# Standard US QWERTY shifted symbols -- for plain LSFT/RSFT(KC_x) only, where
# the key produces a genuinely different character (KC_1 -> "!"), not a
# modifier shortcut. Multi-modifier combos like SGUI/LCTL/LGUI are real
# shortcuts (Cmd+C, Shift+Cmd+4) and should keep showing as symbol+key, not
# be run through this table. hud_window.py also uses it live, while Shift
# is held.
SHIFT_SYMBOLS = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "GRAVE": "~", "MINUS": "_", "EQUAL": "+",
    "LBRC": "{", "RBRC": "}", "BSLS": "|",
    "SCLN": ":", "QUOTE": "\"", "COMMA": "<", "DOT": ">", "SLASH": "?",
}
# Whole names short_label() handles specially, besides the tables above.
SPECIAL_NAMES = {"QK_BOOT", "QK_CAPS_WORD_TOGGLE"}


def plain_kc_label(inner):
    if inner in RENAMES:
        return RENAMES[inner]
    if inner in MOD_SYMS:
        return MOD_SYMS[inner]
    return inner.capitalize() if len(inner) > 1 else inner


def short_label(kc):
    m = re.match(r"^(MO|OSL|TG|TO|DF)\((\d+)\)$", kc)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    m = re.match(r"^([A-Z_]+)\(KC_(\w+)\)$", kc)
    if m:
        prefix, inner = m.group(1), m.group(2)
        if prefix in ("LSFT", "RSFT") and inner in SHIFT_SYMBOLS:
            return SHIFT_SYMBOLS[inner]
        sym = MOD_SYMS.get(prefix, prefix + "+")
        return f"{sym}{plain_kc_label(inner)}"
    if kc.startswith("KC_"):
        return plain_kc_label(kc[3:])
    if kc.startswith("MS_") or kc in RENAMES:
        return plain_kc_label(kc)
    if kc == "QK_BOOT":
        return "Boot"
    if kc == "QK_CAPS_WORD_TOGGLE":
        return "CapsWrd"
    if kc.startswith("RM_"):
        return kc.replace("RM_", "RGB")
    return kc


def modtap_label(kc):
    """The tap key's label for a mod-tap or layer-tap, else None."""
    m = re.match(r"^(?:LCTL_T|RCTL_T|LSFT_T|LALT_T|LGUI_T)\(KC_(\w+)\)$", kc)
    if m:
        return plain_kc_label(m.group(1))
    m = re.match(r"^LT\(\d+,\s*KC_(\w+)\)$", kc)
    if m:
        return plain_kc_label(m.group(1))
    return None


def key_entry(kc):
    """The kc/label/modtap fields of one HUD key, from its keycode name."""
    mt = modtap_label(kc)
    return {"kc": kc, "label": mt if mt else short_label(kc), "modtap": bool(mt)}


# --- QMK keycode numbers -> names (for the Halcyon's live keymap) ---

KEYCODES_PATH = Path(__file__).parent / "data" / "qmk_keycodes.json"
_names_by_value = None

# QMK's 5-bit modifier field: bit 4 = right-hand, bits 0-3 = Ctrl/Shift/Alt/GUI.
MOD_NAMES = {
    0x01: "LCTL", 0x02: "LSFT", 0x04: "LALT", 0x08: "LGUI",
    0x11: "RCTL", 0x12: "RSFT", 0x14: "RALT", 0x18: "RGUI",
    0x03: "SCTL", 0x06: "SALT", 0x0A: "SGUI", 0x07: "MEH", 0x0F: "HYPR",
}
LAYER_RANGES = [  # (base, function), 32 layers each
    (0x5200, "TO"), (0x5220, "MO"), (0x5240, "DF"), (0x5260, "TG"), (0x5280, "OSL"), (0x52C0, "TT"),
]


def _load_names():
    global _names_by_value
    if _names_by_value is None:
        table = json.loads(KEYCODES_PATH.read_text())["keycodes"]
        _names_by_value = {int(v, 16): names for v, names in table.items()}
    return _names_by_value


def _labelled(name):
    """True if short_label() has a real label for this name, not a fallback."""
    inner = name[3:] if name.startswith("KC_") else name
    return (inner in RENAMES or inner in MOD_SYMS or inner in SHIFT_SYMBOLS
            or name in SPECIAL_NAMES or name.startswith(("RM_", "MS_")))


def preferred_name(names):
    """The spelling of one keycode that keymap.json would use: one the label
    rules know (KC_BSPC over KC_BACKSPACE, QK_BOOT over QK_BOOTLOADER),
    else a KC_ name, else the shortest."""
    return min(names, key=lambda n: (not _labelled(n), not n.startswith("KC_"), len(n)))


def _basic_name(value):
    names = _load_names().get(value)
    return preferred_name(names) if names else f"0x{value:04X}"


def _mod_name(mods):
    return MOD_NAMES.get(mods)


def decode(value):
    """A QMK keycode number -> its name, in keymap.json's style. Unknown
    values come back as "0xNNNN"."""
    if value <= 0x00FF:
        return _basic_name(value)
    if 0x0100 <= value <= 0x1FFF:  # QK_MODS: LSFT(KC_1), SGUI(KC_4), ...
        mod = _mod_name((value >> 8) & 0x1F)
        if mod:
            return f"{mod}({_basic_name(value & 0xFF)})"
    elif 0x2000 <= value <= 0x3FFF:  # QK_MOD_TAP: LCTL_T(KC_D)
        mod = _mod_name((value >> 8) & 0x1F)
        if mod:
            return f"{mod}_T({_basic_name(value & 0xFF)})"
    elif 0x4000 <= value <= 0x4FFF:  # QK_LAYER_TAP: LT(1,KC_SPC)
        return f"LT({(value >> 8) & 0x0F},{_basic_name(value & 0xFF)})"
    else:
        for base, func in LAYER_RANGES:
            if base <= value <= base + 0x1F:
                return f"{func}({value - base})"
    return _basic_name(value)
