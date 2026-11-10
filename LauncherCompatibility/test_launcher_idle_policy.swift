import Foundation

// Build: swiftc -o /tmp/lip "MnM on Mac/LauncherIdlePolicy.swift" LauncherCompatibility/test_launcher_idle_policy.swift
// The official launcher (WebView2 under Wine) holds about 2.9 GB while the game runs.
@main struct LauncherIdlePolicyTest {
    static func main() {
        let p = LauncherIdlePolicy.self
        // Close once the game has run long enough to have taken over its session.
        assert(p.shouldClose(enabled: true, launcherRunning: true, gameRunningFor: 45, alreadyClosed: false))
        // Too early: the launcher may still be handing over the session.
        assert(!p.shouldClose(enabled: true, launcherRunning: true, gameRunningFor: 10, alreadyClosed: false))
        // No game, no launcher, already closed, or turned off: leave it alone.
        assert(!p.shouldClose(enabled: true, launcherRunning: true, gameRunningFor: nil, alreadyClosed: false))
        assert(!p.shouldClose(enabled: true, launcherRunning: false, gameRunningFor: 120, alreadyClosed: false))
        assert(!p.shouldClose(enabled: true, launcherRunning: true, gameRunningFor: 120, alreadyClosed: true))
        assert(!p.shouldClose(enabled: false, launcherRunning: true, gameRunningFor: 120, alreadyClosed: false))
        print("Launcher idle policy tests passed (grace period, guards).")
    }
}
