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

A tray icon appears in the menu bar (a filled teal dot = keyboard connected, a terracotta ring = not) — look at the far right of the menu bar on a wide/multi-monitor setup, since macOS only ever places status icons on the primary display's menu bar. Its menu toggles "Pin HUD Visible" and quits the app. The HUD itself also auto-shows when you leave the base layer and auto-hides ~1.5s after returning to it, unless pinned.

## Regenerating the keymap/color data

`src/halcyon_hud/data/mak3r_layers.json` (keycodes + real per-key colors) is generated from `halcyon-corne`'s own sources, not hand-maintained:

```bash
python3 scripts/generate_layout_data.py \
    --vil ~/projects/halcyon/corne-vial/mak3rs.vil \
    --csv ~/projects/halcyon-corne/keyboards/splitkb/halcyon/corne/keymaps/mak3r/rgb_layers.csv
```

Run this after editing the keymap or colors over there, then commit the regenerated JSON here.

## Building and deploying (macOS)

Verified working end-to-end (Briefcase 0.4.5): `briefcase build` produces a real, launchable `.app`.

```bash
python3 -m pip install --break-system-packages briefcase   # or install into a venv instead
briefcase build
```

This builds `build/halcyon_hud/macos/app/Halcyon Corne HUD.app`, ad-hoc signed (no Apple Developer account needed for this). To run it:

```bash
open "build/halcyon_hud/macos/app/Halcyon Corne HUD.app"
```

Or drag/copy that `.app` into `/Applications` to launch it like any normally installed app (from Spotlight/Launchpad) — it won't show up in the Dock, by design (see `app.py`'s `_hide_from_dock()`), only as the tray icon. **After every rebuild**, re-copy it over the `/Applications` copy — `briefcase build` only updates the one under `build/`, so the deployed copy goes stale otherwise.

The first launch after a fresh rebuild may show a **"Halcyon Corne HUD would like to receive keystrokes from any application"** system prompt (Input Monitoring) — click **Open System Settings** and enable it, then quit and reopen the app for the grant to take effect. Because the app is only ad-hoc signed (no paid Apple Developer account), macOS treats each fresh build as a new app for permission purposes, so this can recur after rebuilds.

**Gotchas already handled in this repo's code** (see `CLAUDE.md`'s "Packaging notes" for the full detail if this ever breaks):
- Briefcase requires a PEP 639 `license`/`license-files` declaration or it refuses to build at all.
- PySide6's macOS wheel needs `min_os_version = "13.0"` — Briefcase's default of `11.0` makes pip reject it with a confusing "no matching distribution" error.
- If a build fails partway through and a later `briefcase build` finishes suspiciously fast, it may have silently skipped reinstalling requirements against a broken cached environment — force a clean rebuild with `rm -rf build .briefcase`.
- The packaged app can't find Homebrew's native `hid` library by default (shows permanently "disconnected") — `hid_transport.py` works around this by loading it explicitly rather than relying on the OS's default search.

**Not set up / not needed for personal use**: `briefcase package` (a distributable signed `.dmg`/`.pkg`) requires an active Apple Developer Program membership ($99/year) for a Developer ID certificate + notarization — irrelevant unless you're planning to hand the built app to someone else to run on their own Mac. A locally-built, ad-hoc-signed `.app` like the one above runs fine on the machine that built it, since Gatekeeper's strict signature/notarization check only triggers on files carrying the "downloaded from the internet" quarantine flag.

Linux (and Windows) builds are deferred — see the not-built-yet list above.

## License

[GPL-3.0-or-later](LICENSE). Note that `PySide6`, the main dependency, is itself LGPLv3 (or a paid commercial Qt license) — that's independent of this project's own license and applies regardless of how this code is licensed.
