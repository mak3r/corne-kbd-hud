# Corne HUD

A real-time keymap layer viewer for a [Halcyon Corne](https://splitkb.com/products/halcyon-corne) — similar in spirit to ZSA's Keymapp, but reading live layer state directly off the keyboard over USB HID rather than just showing a static keymap.

Sibling project to [`halcyon-corne`](https://github.com/mak3r/halcyon-corne) (the firmware). Scoped to the Corne for now; the transport layer is written so a ZMK board could plug in later without changing the HUD/rendering code, but that's unbuilt and not a near-term goal.

## Docs

- **[docs/HUD.md](docs/HUD.md)** — how the HUD works, how to run/build/deploy it, and **which HUD release pairs with which `halcyon-corne` release** (the two repos are tightly coupled).
- **[docs/PALETTE_EDITOR.md](docs/PALETTE_EDITOR.md)** — the companion per-key RGB color picker (lives in `halcyon-corne`, but its output is what this app renders).

## Status

**Working, `briefcase build` verified on macOS.** The app runs and has been tested against real hardware (layer changes render correctly, reconnects cleanly if the keyboard is unplugged/replugged). Focus-stealing on macOS (the HUD grabbing keyboard input away from whatever app you were typing in) is fixed and confirmed on hardware — see `CLAUDE.md`'s "macOS focus-stealing fix" for the mechanism. The tray icon and "Pin HUD Visible" toggle are also confirmed working. `briefcase build` produces a real, launchable `.app` bundle (see `CLAUDE.md`'s "Packaging notes" for the `min_os_version`/license gotchas this needed) — `briefcase package`/code signing/notarization is still untested.

The HUD is also movable (click and drag it anywhere; the position is remembered across restarts), highlights each key with a white outline while it's physically held down, driven by the firmware's per-keystroke broadcast (see `halcyon-corne`'s `hud_console.c`), and stays visible across every macOS Space/virtual desktop (including over full-screen apps) — all confirmed working in the packaged `.app`.

What's not built yet (see `halcyon-corne`'s original HUD plan for the fuller phase list):
- `briefcase package`, code signing, and notarization (macOS Input Monitoring permission handling in a packaged build also needs real testing)
- Resizing the HUD (currently a fixed size computed from the layout)
- Windows/Linux testing (developed and tested on macOS only so far)
- ZMK transport (a real lift — see `halcyon-corne`'s CLAUDE.md for why that's a separate, later concern)

See **[docs/HUD.md](docs/HUD.md)** for how it works, how to run it, how to build/deploy it, and how to regenerate its keymap/color data from `halcyon-corne`.

## License

[GPL-3.0-or-later](LICENSE). Note that `PySide6`, the main dependency, is itself LGPLv3 (or a paid commercial Qt license) — that's independent of this project's own license and applies regardless of how this code is licensed.
