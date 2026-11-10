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

## Tests
```sh
T=$(mktemp -d)
for c in test_launcher_fit test_heartbeat_policy test_loader_address; do
  clang -o $T/$c LauncherCompatibility/$c.c && $T/$c; done
swiftc -o $T/hb "MnM on Mac/LauncherHeartbeat.swift" LauncherCompatibility/test_launcher_heartbeat.swift && $T/hb
swiftc -o $T/qp "MnM on Mac/QuitPolicy.swift" LauncherCompatibility/test_quit_policy.swift && $T/qp
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
