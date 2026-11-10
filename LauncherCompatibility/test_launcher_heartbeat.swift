import Foundation

// Build: swiftc -o /tmp/hb "MnM on Mac/LauncherHeartbeat.swift" LauncherCompatibility/test_launcher_heartbeat.swift
// The Windows helper closes the launcher when heartbeat.txt goes stale. The
// heartbeat must keep beating while the app's main thread is busy or throttled.
@main struct LauncherHeartbeatTest {
    static func modified(_ url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }
    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("mnm-heartbeat-test-" + UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? manager.removeItem(at: root) }
        let file = root.appendingPathComponent("heartbeat.txt")

        let heartbeat = LauncherHeartbeat(directory: root, interval: 0.5)
        heartbeat.start()
        // Written immediately, so the helper never sees a missing file at startup.
        Thread.sleep(forTimeInterval: 0.2)
        guard let first = modified(file) else { fatalError("heartbeat was not written on start") }
        let attributes = try manager.attributesOfItem(atPath: file.path)
        assert((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600, "heartbeat must be private")

        // Block the main thread, as a stalled or napping UI run loop would.
        Thread.sleep(forTimeInterval: 2.0)
        guard let later = modified(file), later.timeIntervalSince(first) >= 1.0 else {
            fatalError("heartbeat stopped while the main thread was blocked")
        }

        heartbeat.stop()
        let stopped = modified(file)
        Thread.sleep(forTimeInterval: 1.2)
        assert(modified(file) == stopped, "heartbeat kept writing after stop")
        print("Launcher heartbeat tests passed (immediate, main thread blocked, private, stop).")
    }
}
