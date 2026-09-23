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
      -> app.py: CorneHudApp -- tray icon, wires transport to HUD, owns
         the QApplication event loop
```

`src/corne_kbd_hud/data/mak3r_layers.json` is **generated**, not hand-edited — see `scripts/generate_layout_data.py` and the README's "Regenerating the keymap/color data" section. It's sourced from `halcyon-corne`'s own `.vil` export and `rgb_layers.csv`, converted with the same logic as that repo's `generate_keymap_from_vil.py`/`generate_ledmap.py` (kept as a separate copy here rather than a shared dependency between repos, since they have different release cadences).

## macOS focus-stealing fix

Showing the HUD used to steal keyboard focus from whatever app the user was typing in, every time a layer key was held — confirmed via hardware testing to be a real, severe bug (typing broke as soon as any layer key was pressed). This took a long diagnostic chain to pin down, worth recording so it isn't rediscovered the hard way:

- Every Qt-level fix (`Qt.WindowDoesNotAcceptFocus`, `Qt.WA_ShowWithoutActivating`, `Qt.ToolTip` window type, `NSApplicationActivationPolicyAccessory` to hide the Dock icon) reduced symptoms but didn't eliminate them.
- Direct AppKit-level polling (`NSRunningApplication.isActive()` / `NSWorkspace.frontmostApplication()`, not just Qt's own `isActiveWindow()`, which turned out to be unreliable) proved the *application*, not just the window, was being activated by macOS on every `show()`.
- Bypassing Qt's `show()`/`raise_()` entirely for the real `NSWindow`'s `orderFront_()` (via PyObjC, `hud_window.py`'s `_native_ns_window()`/`_native_show()`/`_native_hide()`) did **not** fix it either — even though the window's `canBecomeKeyWindow` is `False` and it never becomes key, `orderFront_()` still activates the owning app as an AppKit side effect with no documented way to suppress it (confirmed `NSWindowStyleMaskNonactivatingPanel` doesn't prevent this).
- **The actual fix**: `_native_show()` captures whichever `NSRunningApplication` was frontmost immediately before calling `orderFront_()`, then immediately calls `.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)` on that captured app right after. This lets the brief activation happen and then hands it straight back, leaving the HUD window visible while keyboard focus stays with whatever the user was actually using.

If this needs revisiting (e.g. porting to Windows/Linux, where this whole mechanism doesn't apply): the fix is entirely inside `_native_show()` in `hud_window.py`; `_native_hide()`/`orderOut_()` never had this problem since hiding a window doesn't activate anything.

## macOS all-Spaces visibility

By default an `NSWindow` only shows on the Space (virtual desktop) it was last shown on — there's no OS-level setting for this on an app with no Dock icon, since the usual Mission Control "Assign To -> All Desktops" option lives on a Dock icon's right-click menu. Fixed in `_set_collection_behavior_all_spaces()` (called once, right after acquiring the native `NSWindow` in `HudWindow.__init__`) by setting `NSWindow.collectionBehavior` to `NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorStationary | NSWindowCollectionBehaviorFullScreenAuxiliary` — the same technique apps like Keymapp use. All three flags matter: `CanJoinAllSpaces` alone isn't enough to show over a Space occupied by a native full-screened app (`FullScreenAuxiliary` is also required for that), and without `Stationary` the window can visibly animate/relocate during a Space-switch transition instead of just staying put.

## Packaging notes

`briefcase build` (macOS) is verified working end to end (Briefcase 0.4.5, real launchable `.app` produced, smoke-tested, and confirmed connecting to real hardware). Several gotchas hit getting there, worth knowing if this breaks again:

- **License**: Briefcase 0.4.5 requires a PEP 639 license declaration (`license` + `license-files = ["LICENSE"]` under `[tool.briefcase]`) or the build refuses to start at all. This repo uses GPL-3.0-or-later (see `LICENSE`).
- **`min_os_version`**: PySide6's macOS wheel (6.11.2+) is tagged `macosx_13_0`. Briefcase's default `min_os_version` is `"11.0"`, which makes pip reject that wheel as incompatible with a confusing "no matching distribution" error rather than a clear version-mismatch message. Fixed by setting `min_os_version = "13.0"` under `[tool.briefcase.app.corne_kbd_hud.macOS]`.
- **Stale environment gotcha**: after a failed `briefcase build` (e.g. from the two issues above), a subsequent `briefcase build` can silently skip re-running "Installing requirements" and repackage the broken/incomplete environment as if it succeeded. If a build finishes suspiciously fast right after a prior failure, verify the dependency actually landed (e.g. `find build -iname '*pyside*'`) rather than trusting exit status alone; `rm -rf build .briefcase` forces a clean re-create.
- **Native `hid` library not found inside the bundle**: the packaged app showed permanently "disconnected" because `import hid` was silently failing -- `hid`'s bare `ctypes.cdll.LoadLibrary('libhidapi.dylib')` can't find Homebrew's install from inside the bundled interpreter (dyld's default search doesn't include `/opt/homebrew/lib`, and `DYLD_LIBRARY_PATH` set at runtime is ignored for ad-hoc-signed binaries). Fixed in `hid_transport.py` by monkeypatching `ctypes.cdll.LoadLibrary` for the duration of `import hid`, redirecting it straight to an explicit `CDLL(absolute_path)`. See that file's comment for the two other approaches that looked plausible but didn't work.
- **Input Monitoring permission and ad-hoc signing**: `app.py`'s `_request_hid_access()` explicitly calls `IOHIDRequestAccess` on the main thread (the implicit request hidapi fires from a background thread, after the app demotes itself to an accessory/no-Dock-icon app, was confirmed to never register with TCC at all -- the app never even appeared in System Settings' Input Monitoring list). In practice, once the native-library bug above is fixed, the console device opens fine even when this reads back denied -- the keyboard's `CONSOLE_ENABLE` interface is a custom vendor HID usage page, not the standard keyboard usage page Input Monitoring actually gates. Separately: because this app is only ad-hoc signed (no stable Developer ID), **every fresh `briefcase build` gets a new code-signing identity**, so macOS treats it as a different app each time for TCC purposes -- don't be surprised if a previously-granted permission needs re-granting after a rebuild.
- **Redeploying after a rebuild**: `briefcase build` only updates `build/corne_kbd_hud/macos/app/Corne HUD.app` -- if a copy was placed in `/Applications` (see README), that copy is now stale and needs to be manually replaced (`rm -rf` the old one, `cp -R` the new one) after every rebuild.
- **universal2 merge silently drops native extensions without an x86_64 toolchain** (see #1): Briefcase's default macOS build is universal2 -- it pip-installs requirements twice (`app_packages.arm64` and `app_packages.x86_64`), then lipo-merges matching files. On an Apple-Silicon-only machine with no x86_64 Python 3.14 toolchain, the x86_64-side install silently installs *nothing*, and the merge step then silently *drops* (not copies-as-fallback) any file with no x86_64 counterpart -- including `shiboken6/Shiboken.abi3.so` and other PySide6/PyObjC native extensions, which crashes the app on the very first import (`ModuleNotFoundError: No module named 'shiboken6.Shiboken'`) with no warning anywhere in the build log. Fixed by setting `universal_build = false` under `[tool.briefcase.app.corne_kbd_hud.macOS]`, which skips the x86_64 pass and the merge entirely. This means the built `.app` only runs on Apple Silicon, not Intel Macs -- fine for personal use, but revisit (fix the x86_64 toolchain, or find a Briefcase version that fails loudly instead of silently dropping files) if Intel support is ever needed. Possibly worth reporting upstream to `beeware/briefcase` too, since the silent-drop behavior looks like a general Briefcase bug, not something specific to this project.

`briefcase package`/code signing/notarization are still untested — see README's Status/not-built-yet list.

## Known gaps (see README's Status section for the fuller list)

- PySide6's event loop swallows `SIGINT` by default — `app.py` has a small `QTimer` workaround (periodic no-op lets Python's own signal handler run). This is a known PySide6/PyQt quirk, not a bug to "fix" differently.
- Only tested on macOS so far.

## Commit standards

Same as `halcyon-corne`: Conventional Commits (`feat`, `fix`, `docs`, `chore`, etc.), `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` trailer on commits Claude makes.
