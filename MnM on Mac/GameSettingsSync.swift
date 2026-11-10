import Foundation

/// Each graphics backend runs the game in its own Wine prefix, so each one has
/// its own copy of the game's settings.json. Switching backends brought back
/// whatever stale copy that prefix had: in one case background FPS 2 instead
/// of 30, which stalled zone loads at "Waiting for readyZone" whenever the
/// game lost focus. Before each launch, carry the newest valid settings into
/// the prefix that's about to run.
enum GameSettingsSync {
    static func settingsFile(in prefix: URL, user: String = NSUserName()) -> URL {
        prefix.appendingPathComponent("drive_c/users/\(user)/AppData/LocalLow/Niche Worlds Cult/Monsters and Memories/settings.json")
    }

    /// Copies the newest valid settings.json among `prefixes` into `target`.
    /// Returns the prefix it copied from, or nil if `target` was already current.
    @discardableResult
    static func sync(into target: URL, prefixes: [URL], user: String = NSUserName()) throws -> URL? {
        let fm = FileManager.default
        func modified(_ url: URL) -> Date? {
            (try? fm.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
        }
        func valid(_ url: URL) -> Data? {
            guard let data = try? Data(contentsOf: url), data.count < 1_000_000,
                  (try? JSONSerialization.jsonObject(with: data)) is [String: Any] else { return nil }
            return data
        }
        let candidates = prefixes.compactMap { p -> (URL, Date)? in
            let file = settingsFile(in: p, user: user)
            return modified(file).map { (p, $0) }
        }.sorted { $0.1 > $1.1 }
        guard let (newest, _) = candidates.first(where: { valid(settingsFile(in: $0.0, user: user)) != nil }),
              newest.standardizedFileURL != target.standardizedFileURL,
              let data = valid(settingsFile(in: newest, user: user)) else { return nil }
        let destination = settingsFile(in: target, user: user)
        if let existing = try? Data(contentsOf: destination), existing == data { return nil }
        try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: destination, options: .atomic)
        if let date = modified(settingsFile(in: newest, user: user)) {
            try? fm.setAttributes([.modificationDate: date], ofItemAtPath: destination.path)
        }
        return newest
    }
}
