import Foundation

/// The official launcher is WebView2 running under Wine and Rosetta: the
/// launcher, its browser, renderer, GPU, network and storage processes hold
/// about 2.9 GB (mostly compressed) while doing nothing during play. On a 16 GB
/// Mac that's the margin between a smooth session and constant swapping.
/// The launcher is only needed to start the game. Once the game has taken
/// over its session, close it for good: when the game exits, MnM on Mac quits
/// as it already does whenever the launcher isn't open.
enum LauncherIdlePolicy {
    static let defaultsKey = "MnMCloseLauncherWhilePlaying"
    /// Long enough for the launcher's game handoff and the game's own login.
    static let gracePeriod: TimeInterval = 45

    static var enabled: Bool {
        UserDefaults.standard.object(forKey: defaultsKey) as? Bool ?? true
    }

    static func shouldClose(enabled: Bool, launcherRunning: Bool, gameRunningFor: TimeInterval?, alreadyClosed: Bool) -> Bool {
        guard enabled, launcherRunning, !alreadyClosed, let seconds = gameRunningFor else { return false }
        return seconds >= gracePeriod
    }
}
