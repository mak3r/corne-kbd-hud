# Halcyon Corne HUD

A real-time keymap layer viewer for a [Halcyon Corne](https://splitkb.com/products/halcyon-corne) — similar in spirit to ZSA's Keymapp, but reading live layer state directly off the keyboard over USB HID rather than just showing a static keymap.

Sibling project to [`halcyon-corne`](https://github.com/mak3r/halcyon-corne) (the firmware). Scoped to the Corne for now; the transport layer is written so a ZMK board could plug in later without changing the HUD/rendering code, but that's unbuilt and not a near-term goal.

## How it works

The `mak3r` keymap in `halcyon-corne` broadcasts the active layer number over QMK's `CONSOLE_ENABLE` USB HID interface (see that repo's `hud_console.c` and `CLAUDE.md`) — deliberately a *separate* interface from VIA/Vial's own raw HID channel, confirmed on hardware to keep vial.rocks working normally while this is also connected. This app listens on that interface and shows a small always-on-top overlay of the current layer's keys, colored to match the real keyboard's RGB.

```
firmware (layer_state_set_user)
  -> CONSOLE_ENABLE USB HID interface ("LAYER:<n>\n" text lines)
    -> hid_transport.py (background QThread, auto-reconnects)
      -> hud_window.py (paints the overlay)
```

## Status

**Working, `briefcase build` verified on macOS.** The app runs and has been tested against real hardware (layer changes render correctly, reconnects cleanly if the keyboard is unplugged/replugged). Focus-stealing on macOS (the HUD grabbing keyboard input away from whatever app you were typing in) is fixed and confirmed on hardware — see `CLAUDE.md`'s "macOS focus-stealing fix" for the mechanism. The tray icon and "Pin HUD Visible" toggle are also confirmed working. `briefcase build` produces a real, launchable `.app` bundle (see `CLAUDE.md`'s "Packaging notes" for the `min_os_version`/license gotchas this needed) — `briefcase package`/code signing/notarization is still untested.

What's not built yet (see `halcyon-corne`'s original HUD plan for the fuller phase list):
- `briefcase package`, code signing, and notarization (macOS Input Monitoring permission handling in a packaged build also needs real testing)
- Per-key press flashes (only layer changes are shown right now, not individual keypresses)
- Resizing the HUD (currently a fixed size computed from the layout)
- Moving the HUD (currently fixed at the bottom-right corner of the screen)
- Visibility across all Spaces/virtual desktops (currently only shows on the Space it was displayed on)
- Windows/Linux testing (developed and tested on macOS only so far)
- ZMK transport (a real lift — see `halcyon-corne`'s CLAUDE.md for why that's a separate, later concern)

## Running it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6 hid
python3 -m halcyon_hud   # (with src/ on PYTHONPATH, or `pip install -e .` first)
```

On macOS, `hid` needs the native `hidapi` library — `brew install hidapi` if the import fails. The first time it opens the keyboard's HID interface, macOS may prompt for **Input Monitoring** permission (System Settings → Privacy & Security).

A tray icon appears (teal = keyboard connected, grey = not); its menu toggles the HUD and quits the app. The HUD itself also auto-shows when you leave the base layer and auto-hides ~1.5s after returning to it.

## Regenerating the keymap/color data

`src/halcyon_hud/data/mak3r_layers.json` (keycodes + real per-key colors) is generated from `halcyon-corne`'s own sources, not hand-maintained:

```bash
python3 scripts/generate_layout_data.py \
    --vil ~/projects/halcyon/corne-vial/mak3rs.vil \
    --csv ~/projects/halcyon-corne/keyboards/splitkb/halcyon/corne/keymaps/mak3r/rgb_layers.csv
```

Run this after editing the keymap or colors over there, then commit the regenerated JSON here.

## Packaging (untested scaffold)

```bash
pip install briefcase
briefcase dev      # run in dev mode
briefcase build    # build a native app
briefcase package  # package for distribution
```
