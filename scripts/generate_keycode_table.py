#!/usr/bin/env python3
"""Regenerate src/corne_kbd_hud/data/qmk_keycodes.json: every QMK keycode
number the Halcyon's firmware can report, with all of its names.

The HUD reads the Halcyon's live keymap as 16-bit keycode numbers (Vial's
keymap-read request), and keycodes.decode() turns them back into names.
The numbers change between QMK keycode versions, so this table comes from
the same vial-qmk checkout the firmware is built against. Rerun it after
updating that checkout.

Names come from QMK's own loader (the latest keycode version, so the same
numbers as the generated quantum/keycodes.h), plus the legacy aliases in
quantum/quantum_keycodes_legacy.h, which keymap.json still uses
(KC_MS_LEFT, KC_BTN1, ...).

Usage:
    python3 scripts/generate_keycode_table.py ~/projects/vial-qmk-build
"""
import json
import os
import re
import sys
from pathlib import Path

OUT_PATH = Path(__file__).resolve().parent.parent / "src" / "corne_kbd_hud" / "data" / "qmk_keycodes.json"


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    qmk_home = Path(sys.argv[1]).expanduser().resolve()
    sys.path.insert(0, str(qmk_home / "lib" / "python"))
    os.chdir(qmk_home)  # QMK's loader finds data/constants/ relative to the cwd
    from qmk.keycodes import load_spec

    spec = load_spec("latest")
    names = {}  # int value -> [canonical name, aliases...]
    by_name = {}
    for value_s, info in spec["keycodes"].items():
        value = int(value_s, 16)
        names[value] = [info["key"], *info.get("aliases", [])]
        for name in names[value]:
            by_name[name] = value

    legacy = (qmk_home / "quantum" / "quantum_keycodes_legacy.h").read_text()
    for alias, target in re.findall(r"^#define\s+(\w+)\s+(\w+)\s*$", legacy, flags=re.M):
        if target in by_name and alias not in by_name:
            names[by_name[target]].append(alias)
            by_name[alias] = by_name[target]

    table = {f"0x{value:04X}": names[value] for value in sorted(names)}
    OUT_PATH.write_text(json.dumps({"version": spec["version"], "keycodes": table}, indent=1) + "\n")
    print(f"wrote {OUT_PATH} ({len(table)} keycodes, QMK keycode version {spec['version']})")


if __name__ == "__main__":
    main()
