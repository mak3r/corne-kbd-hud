# CLAUDE.md — Agent Instructions

Solo hobby project, sibling repo to [`halcyon-corne`](../halcyon-corne) (the firmware). Same working style: Claude works directly on `main`, human reviews before anything gets pushed/tagged/released.

## Project purpose

A real-time desktop HUD showing the active keymap layer on a Halcyon Corne, reading layer state live off the keyboard over USB HID — not a static keymap viewer. Scoped to the Corne specifically for now (see the user's own framing in the planning conversation: "Currently I'm only concerned with the corne keyboards"). The transport/rendering split is deliberately generic enough that a ZMK board could plug in later without changing the HUD code, but building that is explicitly out of scope until there's an actual ZMK board in the picture — don't design further ahead of that than the current transport abstraction already allows.

This repo is desktop tooling, not firmware — that split is intentional, mirroring why `halcyon-corne`'s own CLAUDE.md treats a hypothetical ZMK variant as a separate sibling repo rather than a branch/subfolder. Don't merge this back into `halcyon-corne`.

## Why CONSOLE_ENABLE, not VIA/Vial's raw HID

This was a real architecture decision, not arbitrary — worth understanding before touching `hid_transport.py`. `halcyon-corne` uses VIA/Vial for live keymap editing (vial.rocks), which already owns the entire raw HID interface (`via.c` defines the only `raw_hid_receive()` in that build). Pushing unsolicited HUD reports onto that same channel risked corrupting Vial's own request/response protocol state while vial.rocks was open — a real, verified risk, not speculative. `CONSOLE_ENABLE` is a stock, cross-platform core QMK feature that creates a genuinely separate USB HID interface (usage page `0xFF31`, usage `0x74` — confirmed via `usb_descriptor.c` in a real `vial-qmk` checkout), so it can't collide with Vial's protocol by construction. Confirmed on real hardware: layer changes report correctly and vial.rocks keeps working normally with this HUD's transport connected at the same time, on either physical half as split master.

See `halcyon-corne`'s `CLAUDE.md` ("Desktop HUD layer broadcast" + "Split state sync gotchas") for the firmware side of this.

## Architecture

```
firmware (halcyon-corne's hud_console.c, layer_state_set_user hook)
  -> CONSOLE_ENABLE USB HID interface ("LAYER:<n>\n" text lines)
    -> hid_transport.py: HidTransport(QThread) -- finds the interface by
       usage_page/usage (not VID/PID, so it doesn't matter which half is
       master), reconnects automatically, emits layerChanged(int) /
       connectionChanged(bool) Qt signals
      -> hud_window.py: HudWindow(QWidget) -- frameless/always-on-top,
         paints keys from keyboard_layout.py's geometry + mak3r_layers.json's
         keycodes/colors, auto-hides on the base layer
      -> app.py: HalcyonHudApp -- tray icon, wires transport to HUD, owns
         the QApplication event loop
```

`src/halcyon_hud/data/mak3r_layers.json` is **generated**, not hand-edited — see `scripts/generate_layout_data.py` and the README's "Regenerating the keymap/color data" section. It's sourced from `halcyon-corne`'s own `.vil` export and `rgb_layers.csv`, converted with the same logic as that repo's `generate_keymap_from_vil.py`/`generate_ledmap.py` (kept as a separate copy here rather than a shared dependency between repos, since they have different release cadences).

## macOS focus-stealing fix

Showing the HUD used to steal keyboard focus from whatever app the user was typing in, every time a layer key was held — confirmed via hardware testing to be a real, severe bug (typing broke as soon as any layer key was pressed). This took a long diagnostic chain to pin down, worth recording so it isn't rediscovered the hard way:

- Every Qt-level fix (`Qt.WindowDoesNotAcceptFocus`, `Qt.WA_ShowWithoutActivating`, `Qt.ToolTip` window type, `NSApplicationActivationPolicyAccessory` to hide the Dock icon) reduced symptoms but didn't eliminate them.
- Direct AppKit-level polling (`NSRunningApplication.isActive()` / `NSWorkspace.frontmostApplication()`, not just Qt's own `isActiveWindow()`, which turned out to be unreliable) proved the *application*, not just the window, was being activated by macOS on every `show()`.
- Bypassing Qt's `show()`/`raise_()` entirely for the real `NSWindow`'s `orderFront_()` (via PyObjC, `hud_window.py`'s `_native_ns_window()`/`_native_show()`/`_native_hide()`) did **not** fix it either — even though the window's `canBecomeKeyWindow` is `False` and it never becomes key, `orderFront_()` still activates the owning app as an AppKit side effect with no documented way to suppress it (confirmed `NSWindowStyleMaskNonactivatingPanel` doesn't prevent this).
- **The actual fix**: `_native_show()` captures whichever `NSRunningApplication` was frontmost immediately before calling `orderFront_()`, then immediately calls `.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)` on that captured app right after. This lets the brief activation happen and then hands it straight back, leaving the HUD window visible while keyboard focus stays with whatever the user was actually using.

If this needs revisiting (e.g. porting to Windows/Linux, where this whole mechanism doesn't apply): the fix is entirely inside `_native_show()` in `hud_window.py`; `_native_hide()`/`orderOut_()` never had this problem since hiding a window doesn't activate anything.

## Known gaps (see README's Status section for the fuller list)

- `pyproject.toml`'s Briefcase config is a best-effort scaffold, never actually run through `briefcase build`. Expect to need fixes when packaging is actually attempted.
- PySide6's event loop swallows `SIGINT` by default — `app.py` has a small `QTimer` workaround (periodic no-op lets Python's own signal handler run). This is a known PySide6/PyQt quirk, not a bug to "fix" differently.
- Only tested on macOS so far.

## Commit standards

Same as `halcyon-corne`: Conventional Commits (`feat`, `fix`, `docs`, `chore`, etc.), `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` trailer on commits Claude makes.
