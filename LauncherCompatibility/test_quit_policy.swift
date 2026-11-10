import Foundation

// Build: swiftc -o /tmp/qp "MnM on Mac/QuitPolicy.swift" LauncherCompatibility/test_quit_policy.swift
// Quitting MnM on Mac while the game runs tears down its Wine session and the
// game exits with SIGTERM ("The game stopped (code 15)").
@main struct QuitPolicyTest {
    static func main() {
        // Automatic quits (launcher closed, setup window closed) never stop a running game.
        assert(!QuitPolicy.mayQuitAutomatically(gameRunning: true))
        assert(QuitPolicy.mayQuitAutomatically(gameRunning: false))

        // A user quit with no game running proceeds without a prompt.
        assert(QuitPolicy.reply(gameRunning: false, confirm: { fatalError("must not prompt") }) == .now)
        // A user quit during play asks first; declining (the default) cancels.
        assert(QuitPolicy.reply(gameRunning: true, confirm: { false }) == .cancel)
        assert(QuitPolicy.reply(gameRunning: true, confirm: { true }) == .now)
        print("Quit policy tests passed (automatic, idle quit, confirm, decline).")
    }
}
