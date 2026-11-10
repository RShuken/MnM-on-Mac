import Foundation

/// Keeps `heartbeat.txt` fresh for the Windows launcher helper, which closes
/// the official launcher when the file goes stale.
///
/// It used to be written from a main-thread UI timer. While the game is in
/// front, MnM on Mac sits in the background where macOS can nap or throttle
/// it, and any long main-thread task also stalls the timer. A false expiry
/// closed the launcher and then the whole session, game included.
/// So the heartbeat runs on its own queue and holds an App Nap opt-out while
/// it runs. Idle system sleep is still allowed.
final class LauncherHeartbeat {
    private let file: URL
    private let interval: TimeInterval
    private let queue = DispatchQueue(label: "io.github.seathasky.mnmonmac.heartbeat", qos: .userInitiated)
    private var timer: DispatchSourceTimer?
    private var activity: NSObjectProtocol?

    init(directory: URL, interval: TimeInterval = 2) {
        file = directory.appendingPathComponent("heartbeat.txt")
        self.interval = interval
    }

    func start() {
        queue.sync {
            guard timer == nil else { return }
            activity = ProcessInfo.processInfo.beginActivity(
                options: [.userInitiatedAllowingIdleSystemSleep],
                reason: "Keep the Monsters & Memories launcher session alive")
            let source = DispatchSource.makeTimerSource(queue: queue)
            source.schedule(deadline: .now(), repeating: interval, leeway: .milliseconds(100))
            source.setEventHandler { [weak self] in self?.beat() }
            timer = source
            source.resume()
        }
    }

    func stop() {
        queue.sync {
            timer?.cancel()
            timer = nil
            if let activity { ProcessInfo.processInfo.endActivity(activity) }
            activity = nil
        }
    }

    private func beat() {
        do {
            try Data("running".utf8).write(to: file, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        } catch {
            // The session directory is removed when the launcher exits.
        }
    }

    deinit {
        // No queue.sync here: the last reference can drop on the heartbeat queue.
        timer?.cancel()
        if let activity { ProcessInfo.processInfo.endActivity(activity) }
    }
}
