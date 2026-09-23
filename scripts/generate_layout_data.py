#!/usr/bin/env python3
"""Regenerate src/corne_kbd_hud/data/mak3r_layers.json from the halcyon-corne
repo's own sources: a Vial export (.vil) for keycodes/layout, and
rgb_layers.csv for the real per-key colors. Mirrors the conversion logic
in halcyon-corne's generate_keymap_from_vil.py / generate_ledmap.py --
kept as a separate copy here rather than a shared dependency, since this
is a different repo with a different release cadence.

Usage:
    python3 scripts/generate_layout_data.py \\
        --vil ~/projects/halcyon/corne-vial/mak3rs.vil \\
        --csv ~/projects/halcyon-corne/keyboards/splitkb/halcyon/corne/keymaps/mak3r/rgb_layers.csv
"""
import argparse
import csv
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
OUT_PATH = HERE.parent / "src" / "corne_kbd_hud" / "data" / "mak3r_layers.json"

LAYER_NAMES = ["Base", "Symbols", "Nav", "Function", "Return", "Return2", "Unused", "Unused"]

# --- keycode layout (same derivation as halcyon-corne's generate_keymap_from_vil.py) ---

COORDS = (
    [(0, c) for c in range(6)]
    + [(5, c) for c in [5, 4, 3, 2, 1, 0]]
    + [(1, c) for c in range(6)]
    + [(6, c) for c in [5, 4, 3, 2, 1, 0]]
    + [(2, c) for c in range(6)]
    + [(7, c) for c in [5, 4, 3, 2, 1, 0]]
    + [(3, c) for c in [3, 4, 5]]
    + [(8, c) for c in [5, 4, 3]]
)
assert len(COORDS) == 42

RENAMES = {
    "BSPC": "Bksp", "ENTER": "Enter", "ESCAPE": "Esc", "SPACE": "Space", "QUOTE": "'",
    "SCLN": ";", "SLASH": "/", "COMMA": ",", "DOT": ".", "GRAVE": "`", "BSLS": "\\",
    "LBRC": "[", "RBRC": "]", "MUTE": "Mute", "NO": "·", "TRNS": "―", "DELETE": "Del",
    "PGUP": "PgUp", "PGDN": "PgDn", "HOME": "Home", "END": "End",
    "LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓",
    "KP_MINUS": "-", "KP_EQUAL": "=", "KP_1": "1", "KP_2": "2", "KP_3": "3", "KP_4": "4", "KP_5": "5",
    "KP_6": "6", "KP_7": "7", "KP_8": "8", "KP_9": "9", "KP_0": "0",
    "VOLU": "Vol+", "VOLD": "Vol-", "MPLY": "Play", "MRWD": "Rwd", "MFFD": "Ffd", "PSCR": "PrSc",
    "MINUS": "-", "EQUAL": "=",
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
# be run through this table.
SHIFT_SYMBOLS = {
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "GRAVE": "~", "MINUS": "_", "EQUAL": "+",
    "LBRC": "{", "RBRC": "}", "BSLS": "|",
    "SCLN": ":", "QUOTE": "\"", "COMMA": "<", "DOT": ">", "SLASH": "?",
}
FIXUPS = {
    "KC_LSHIFT": "KC_LSFT", "KC_RSHIFT": "KC_RSFT", "KC_BSPACE": "KC_BSPC",
    "KC_SCOLON": "KC_SCLN", "KC_LBRACKET": "KC_LBRC", "KC_RBRACKET": "KC_RBRC",
    "KC_BSLASH": "KC_BSLS", "KC_PGDOWN": "KC_PGDN", "KC_PSCREEN": "KC_PSCR",
}
ALIAS_RE = re.compile(r"\b(" + "|".join(FIXUPS) + r")\b")


def fix(kc):
    return ALIAS_RE.sub(lambda m: FIXUPS[m.group(1)], kc)


def flatten42(grid):
    out = []
    out += grid[0][0:6]
    out += list(reversed(grid[5]))
    out += grid[1][0:6]
    out += list(reversed(grid[6]))
    out += grid[2][0:6]
    out += list(reversed(grid[7]))
    out += grid[3][3:6]
    out += list(reversed(grid[8]))[0:3]
    assert len(out) == 42
    return [fix(v) for v in out]


def plain_kc_label(inner):
    if inner in RENAMES:
        return RENAMES[inner]
    if inner in MOD_SYMS:
        return MOD_SYMS[inner]
    return inner.capitalize() if len(inner) > 1 else inner


def short_label(kc):
    m = re.match(r"^(MO|OSL|TG|TO)\((\d+)\)$", kc)
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
    if kc == "QK_BOOT":
        return "Boot"
    if kc == "QK_CAPS_WORD_TOGGLE":
        return "CapsWrd"
    if kc.startswith("RM_"):
        return kc.replace("RM_", "RGB")
    return kc


def modtap_label(kc):
    m = re.match(r"^(?:LCTL_T|RCTL_T|LSFT_T|LALT_T|LGUI_T)\(KC_(\w+)\)$", kc)
    if m:
        return plain_kc_label(m.group(1))
    return None


# --- color layout (same derivation as halcyon-corne's generate_ledmap.py) ---

REAL_COORDS = set()
for r in range(3):
    for c in range(6):
        REAL_COORDS.add((r, c))
        REAL_COORDS.add((r + 5, c))
for c in [3, 4, 5]:
    REAL_COORDS.add((3, c))
    REAL_COORDS.add((8, c))


def load_csv_rows(csv_path):
    with open(csv_path, newline="") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("layer,"):
                continue
            layer_s, row_s, col_s, h_s, s_s, v_s = next(csv.reader([line]))
            yield (
                int(layer_s),
                None if row_s == "*" else int(row_s),
                None if col_s == "*" else int(col_s),
                int(h_s), int(s_s), int(v_s),
            )


def resolve_colors(csv_path):
    resolved = {i: {} for i in range(8)}
    for layer, row, col, h, s, v in load_csv_rows(csv_path):
        rows = range(10) if row is None else [row]
        cols = range(6) if col is None else [col]
        for r in rows:
            for c in cols:
                if (r, c) in REAL_COORDS:
                    resolved[layer][(r, c)] = [h, s, v]
    return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vil", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()

    vil = json.loads(args.vil.read_text())
    layers = vil["layout"]
    assert len(layers) == 8

    colors = resolve_colors(args.csv)

    out_layers = []
    for i, layer in enumerate(layers):
        flat = flatten42(layer)
        keys = []
        for (r, c), kc in zip(COORDS, flat):
            mt = modtap_label(kc)
            hsv = colors[i].get((r, c))
            keys.append({
                "r": r, "c": c, "kc": kc,
                "label": mt if mt else short_label(kc),
                "modtap": bool(mt),
                "hsv": hsv,
            })
        out_layers.append({"name": LAYER_NAMES[i], "keys": keys})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out_layers, indent=2))
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
