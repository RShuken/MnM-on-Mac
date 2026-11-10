# RShuken fork of MnM on Mac

Based on upstream 2.0.6. Fixes the game closing mid-play ("The game stopped (code 15)").

## What was wrong
1. The Mac app wrote the launcher heartbeat (`heartbeat.txt`) from a main-thread UI timer. While the game is in front, the app is in the background, where macOS can nap or throttle it, and any main-thread stall also stops the timer.
2. The Windows helper (`windows_launcher.c`) closed the official launcher if the heartbeat was more than 20 s old.
3. Once the launcher closed, the app could quit itself and tear down the Wine session, so the game got SIGTERM (code 15).
4. A minimized launcher reports a 0x0 client under Wine. `scale_update` kept resizing the iconic window on every frame and gave WebView2 a negative height (`client=0x0 web=0x-176`).

## Fixes
- `MnM on Mac/LauncherHeartbeat.swift`: the heartbeat runs on its own dispatch queue and holds an App Nap opt-out (idle sleep is still allowed).
- `LauncherCompatibility/heartbeat_policy.h`: the controller heartbeat limit is now 60 s (it was 20 s).
- `MnM on Mac/QuitPolicy.swift` + `applicationShouldTerminate`: the app never quits by itself while the game runs. A user quit during play asks first, and the default button is "Keep Playing".
- `LauncherCompatibility/launcher_fit.h` (`launcher_layout_ready`): no layout while the launcher is minimized or 0x0.
- The updater checks this fork's releases, not upstream's.

## Launcher efficiency (2026-11-10)
- WebView2 Storage Service crash loop: fixed with `--no-sandbox` for the launcher web view only (see commit 11ceed7).
- Bridge timer (`MnMWebViewBridge.dll`): idle launcher CPU went from 23.2% to 3.8% (the bare launcher idles at 1.6%).
  - Frames are event-driven: the browser posts `MnMWebViewFrameReady`, and the host paints at once. After that the timer only does 4 Hz housekeeping instead of polling at 30 Hz.
  - The footer is re-raised only when a visible window actually overlaps it (it was 1,800 `SetWindowPos` calls a minute).
  - Layout runs at 2 Hz, and also on WM_SIZE/DPI/display changes. The footer repaints only when the Mac app's state changes.
  - Once-a-minute `bridge stats` lines in `Logs/launcher-startup.log` report ticks, frames, raises and MB copied.

## Memory, heat and reliability (2026-11-10)
- The official launcher closes 45 s after the game starts (LauncherIdlePolicy). It used 2,976 MB, and system compressed memory dropped 5.67 -> 3.15 GB.
- Game settings sync across backend prefixes before each launch (GameSettingsSync). A stale copy with background FPS 2 had stalled loads.
- Default backend is DXMT + MetalFX: ~21 W vs ~28 W for D3DMetal at similar FPS, with the same ~8.7 GB game footprint.
- The gear menu is a native Windows menu in the footer (the macOS menu dropped clicks). It adds MetalFX Quality: Sharper 1.3x / Balanced 1.5x / Cooler 2.0x.
- DXMT paces frames at the game's own cap (`d3d11.preferredMaxFrameRate` from GameSettingTargetFramerate).
- Orphaned Wine processes from earlier sessions are cleaned up at startup (WineOrphans).
- A notification at game start names apps holding more than 2 GB (MemoryHeadsUp).

## Performance log (per game session)
- `MnM on Mac/PerfMonitor/mnm_perf.py` (+ `gpu_top.py`) ship inside the app. With gear menu -> Performance Log on (`MnMPerformanceLog`, default off), the app starts the monitor when the game starts (`watch --once`). It logs to `~/Library/Application Support/MnM on Mac/Logs/perf/`, writes `perf-*-report.txt` when the game exits, keeps the newest 40 sessions, and quits. Nothing runs at login. It needs Xcode or the Command Line Tools (python3, footprint).
- FPS comes from `MnMFrameStats.dylib` (LauncherCompatibility/frame_stats.m), which is injected into the game's Wine process only while Performance Log is on. It timestamps every `-[CAMetalLayer nextDrawable]` (one per frame on DXMT and D3DMetal) and appends one line per second to `Logs/perf/frames-*.log` (unix_ms, frames, avg_ms, max_ms, >50 ms, >100 ms). It links nothing but libobjc/libSystem and hooks only after the process itself loads QuartzCore. Instruments snapshots (400 MB, ~55 s, and they blocked sampling) are no longer used in the loop.

## Tests
```sh
T=$(mktemp -d)
for c in test_launcher_fit test_heartbeat_policy test_loader_address; do
  clang -o $T/$c LauncherCompatibility/$c.c && $T/$c; done
swiftc -o $T/hb "MnM on Mac/LauncherHeartbeat.swift" LauncherCompatibility/test_launcher_heartbeat.swift && $T/hb
swiftc -o $T/qp "MnM on Mac/QuitPolicy.swift" LauncherCompatibility/test_quit_policy.swift && $T/qp
for t in LauncherIdlePolicy:launcher_idle_policy GameSettingsSync:game_settings_sync WineOrphans:wine_orphans MemoryHeadsUp:memory_headsup; do
  swiftc -o $T/x "MnM on Mac/${t%%:*}.swift" LauncherCompatibility/test_${t##*:}.swift && $T/x; done
```

## Building
The Windows helpers must be linked with LLVM `lld`, as upstream's are. GNU ld output (plain mingw-w64 gcc) builds fine, but the DLL hangs `LoadLibraryW` during injection into the suspended launcher, and the launcher never opens.

```sh
brew install mingw-w64 lld        # plus Homebrew llvm@20 for clang
cat > ~/x86_64-w64-mingw32-clang <<'EOF'
#!/bin/sh
exec /opt/homebrew/opt/llvm@20/bin/clang --target=x86_64-w64-mingw32 \
  --sysroot=/opt/homebrew/opt/mingw-w64/toolchain-x86_64 \
  -fuse-ld=lld --ld-path=/opt/homebrew/opt/lld/bin/ld.lld "$@"
EOF
chmod +x ~/x86_64-w64-mingw32-clang
E="$HOME/Library/Application Support/MnM on Mac/Runtime/sikarugir-10.0_6-dxmt-0.80/engine/lib/wine/x86_64-windows"
python3 LauncherCompatibility/build_assets.py --compiler ~/x86_64-w64-mingw32-clang --user32 "$E/user32.dll" --ole32 "$E/ole32.dll"
xcodebuild -project "MnM on Mac.xcodeproj" -scheme "MnM on Mac" -configuration Release \
  -derivedDataPath ../build CODE_SIGN_IDENTITY=- CODE_SIGN_STYLE=Manual DEVELOPMENT_TEAM= build
```
Then quit MnM on Mac and copy `../build/Build/Products/Release/MnM on Mac.app` to `/Applications`.

## Syncing upstream
`git fetch upstream && git merge upstream/main`, then rebuild the helpers (they're generated files).
