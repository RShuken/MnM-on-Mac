import Foundation

// Build: swiftc -o /tmp/gss "MnM on Mac/GameSettingsSync.swift" LauncherCompatibility/test_game_settings_sync.swift
// Each graphics backend has its own Wine prefix and its own copy of the game's
// settings.json. Switching backends brought back a stale copy (background FPS 2),
// and zone loads then stalled whenever the game lost focus.
@main struct GameSettingsSyncTest {
    static func main() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent("mnm-settings-sync-" + UUID().uuidString)
        defer { try? fm.removeItem(at: root) }
        func prefix(_ name: String, settings: String?, age: TimeInterval) throws -> URL {
            let p = root.appendingPathComponent(name)
            let file = GameSettingsSync.settingsFile(in: p, user: "tester")
            try fm.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            if let settings {
                try Data(settings.utf8).write(to: file)
                try fm.setAttributes([.modificationDate: Date().addingTimeInterval(-age)], ofItemAtPath: file.path)
            }
            return p
        }
        let dxmt = try prefix("Prefix", settings: #"{"GameSettingTargetBackgroundFramerate":2}"#, age: 3600)
        let d3d = try prefix("Prefix-D3DMetal", settings: #"{"GameSettingTargetBackgroundFramerate":30}"#, age: 60)
        let dxvk = try prefix("Prefix-DXVK", settings: nil, age: 0)
        let all = [dxmt, d3d, dxvk]
        func read(_ p: URL) -> String? { try? String(contentsOf: GameSettingsSync.settingsFile(in: p, user: "tester"), encoding: .utf8) }

        // Launching DXMT takes the newer D3DMetal settings.
        let first = try GameSettingsSync.sync(into: dxmt, prefixes: all, user: "tester"); assert(first == d3d)
        assert(read(dxmt)?.contains(":30") == true)
        // A prefix with no settings yet gets them too.
        _ = try GameSettingsSync.sync(into: dxvk, prefixes: all, user: "tester")
        assert(read(dxvk)?.contains(":30") == true)
        // Already newest (or identical): nothing is copied.
        let noop = try GameSettingsSync.sync(into: d3d, prefixes: all, user: "tester"); assert(noop == nil)
        // A malformed newer file is never spread to other prefixes.
        try Data("{not json".utf8).write(to: GameSettingsSync.settingsFile(in: dxvk, user: "tester"))
        let bad = try GameSettingsSync.sync(into: dxmt, prefixes: all, user: "tester"); assert(bad == nil)
        assert(read(dxmt)?.contains(":30") == true)
        print("Game settings sync tests passed (newer wins, missing filled, no-op, malformed ignored).")
    }
}
