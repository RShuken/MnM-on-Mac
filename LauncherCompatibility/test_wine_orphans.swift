import Foundation

// Build: swiftc -o /tmp/wo "MnM on Mac/WineOrphans.swift" LauncherCompatibility/test_wine_orphans.swift
// Wine services (winedevice.exe) were left running after their wineserver was gone,
// including from old replaced engine copies (LauncherCompatibility.previous-*).
@main struct WineOrphansTest {
    static func main() {
        let rt = "/U/Library/Application Support/MnM on Mac/Runtime/sik"
        let procs: [(Int32, String)] = [
            (1102, rt + "/LauncherCompatibility/engine/bin/wineserver"),
            (1113, rt + "/LauncherCompatibility/engine/bin/wine"),          // live launcher session
            (26684, rt + "/LauncherCompatibility.previous-4E8F/engine/bin/wine"), // old engine copy
            (51079, rt + "/engine/bin/wine"),                                // game engine, no server
            (900, "/Applications/Other.app/Contents/MacOS/wine"),            // not ours
        ]
        let orphans = WineOrphans.find(processes: procs, runtimeRoot: rt)
        assert(orphans.sorted() == [26684, 51079], "got \(orphans)")
        // A wineserver keeps its own engine's processes alive.
        let withGame = procs + [(51100, rt + "/engine/bin/wineserver")]
        assert(WineOrphans.find(processes: withGame, runtimeRoot: rt) == [26684])
        print("Wine orphan tests passed (old engine, serverless engine, live session kept, foreign ignored).")
    }
}
