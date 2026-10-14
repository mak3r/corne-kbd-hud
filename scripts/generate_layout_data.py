#!/usr/bin/env python3
"""Regenerate src/corne_kbd_hud/data/mak3r_layers.json from the halcyon-corne
repo's own sources: keymap.json for keycodes/layout, and rgb_layers.csv for
the real per-key colors. keymap.json is the single layout shared by both
the Halcyon (QMK) and the Typeractive (ZMK, tya-corne) -- both firmwares
are generated from it, so the HUD reads the same file. It includes the
wireless-only keys (OUT_TOG, BT_CLR, BT_SEL_n), which do nothing on the
Halcyon. Mirrors the color logic in halcyon-corne's generate_ledmap.py --
kept as a separate copy here rather than a shared dependency, since this
is a different repo with a different release cadence.

Usage:
    python3 scripts/generate_layout_data.py \\
        --keymap ~/projects/halcyon-corne/keyboards/splitkb/halcyon/corne/keymaps/mak3r/keymap.json \\
        --csv ~/projects/halcyon-corne/keyboards/splitkb/halcyon/corne/keymaps/mak3r/rgb_layers.csv
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "src"))
from corne_kbd_hud.keycodes import key_entry  # noqa: E402 -- the HUD's own label rules

OUT_PATH = HERE.parent / "src" / "corne_kbd_hud" / "data" / "mak3r_layers.json"

LAYER_NAMES = ["Base", "Symbols", "Nav", "Function", "Mouse", "Return2", "Unused", "Unused"]

# --- keycode layout: (row, col) of keymap.json's first 42 entries per layer ---

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
    parser.add_argument("--keymap", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    args = parser.parse_args()

    text = re.sub(r"^\s*//.*$", "", args.keymap.read_text(), flags=re.M)  # keymap.json has // comments
    layers = json.loads(text)["layers"]
    assert len(layers) == 8

    colors = resolve_colors(args.csv)

    out_layers = []
    for i, layer in enumerate(layers):
        flat = layer[:42]  # keymap.json's first 42 entries are in COORDS order
        keys = []
        for (r, c), kc in zip(COORDS, flat):
            keys.append({"r": r, "c": c, **key_entry(kc), "hsv": colors[i].get((r, c))})
        out_layers.append({"name": LAYER_NAMES[i], "keys": keys})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out_layers, indent=2))
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
