import Foundation

// Build: swiftc -o /tmp/mh "MnM on Mac/MemoryHeadsUp.swift" LauncherCompatibility/test_memory_headsup.swift
@main struct MemoryHeadsUpTest {
    static func main() {
        let gb = 1024.0 * 1024 * 1024
        let procs: [(path: String, bytes: UInt64)] = [
            ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", UInt64(0.5 * gb)),
            ("/Applications/Google Chrome.app/Contents/Frameworks/X.framework/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)", UInt64(1.2 * gb)),
            ("/Applications/Claude.app/Contents/MacOS/Claude", UInt64(1.4 * gb)),
            ("/Users/u/Library/Application Support/MnM on Mac/Runtime/x/engine/bin/wine", UInt64(8 * gb)), // the game: excluded
            ("/Applications/MnM on Mac.app/Contents/MacOS/MnM on Mac", UInt64(0.1 * gb)),                 // us: excluded
            ("/usr/libexec/someDaemon", UInt64(3 * gb)),                                                // not an app
            ("/Applications/Tiny.app/Contents/MacOS/Tiny", UInt64(0.1 * gb)),                           // under the floor
        ]
        let apps = MemoryHeadsUp.group(procs, excluding: ["MnM on Mac"], minimumBytes: UInt64(0.3 * gb))
        assert(apps.map(\.name) == ["Google Chrome", "Claude"], "got \(apps)")
        assert(abs(Double(apps[0].bytes) / gb - 1.7) < 0.01)
        let text = MemoryHeadsUp.message(apps)
        assert(text?.contains("3.1 GB") == true && text?.contains("Google Chrome 1.7 GB") == true, text ?? "nil")
        assert(MemoryHeadsUp.message([("Small", UInt64(0.5 * gb))]) == nil)  // below 2 GB total: stay quiet
        print("Memory heads-up tests passed (grouping, exclusions, threshold, message).")
    }
}
