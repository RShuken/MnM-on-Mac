import Foundation

/// Quitting MnM on Mac tears down the Wine session it owns. If the game is
/// running at that moment, it gets SIGTERM ("The game stopped (code 15)").
/// Every quit path goes through this policy.
enum QuitPolicy {
    enum Reply: Equatable { case now, cancel }

    /// Quits the app starts by itself, e.g. after the official launcher closes
    /// or a setup window is dismissed. These never stop a running game.
    static func mayQuitAutomatically(gameRunning: Bool) -> Bool { !gameRunning }

    /// A user-requested quit. While the game runs, ask first. `confirm` must
    /// default to "no", so a key pressed in-game cannot confirm by accident.
    static func reply(gameRunning: Bool, confirm: () -> Bool) -> Reply {
        guard gameRunning else { return .now }
        return confirm() ? .now : .cancel
    }
}
