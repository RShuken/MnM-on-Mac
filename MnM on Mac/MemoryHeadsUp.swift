import Foundation
import Darwin
import UserNotifications

/// The game needs about 8.7 GB (measured on both DXMT and D3DMetal), and on a
/// 16 GB Mac other apps routinely held another ~6 GB, which pushed macOS into
/// heavy swapping, stutter and slow zone loads. When the game starts, name the
/// biggest other apps once, so the player can decide what to quit.
enum MemoryHeadsUp {
    /// Groups processes by their outermost .app bundle (helpers count toward their app).
    static func group(_ processes: [(path: String, bytes: UInt64)], excluding: Set<String>, minimumBytes: UInt64) -> [(name: String, bytes: UInt64)] {
        var totals: [String: UInt64] = [:]
        for p in processes {
            guard let app = p.path.range(of: ".app/") else { continue }
            let name = (p.path[..<app.lowerBound] as Substring).split(separator: "/").last.map(String.init) ?? ""
            guard !name.isEmpty, !excluding.contains(name) else { continue }
            totals[name, default: 0] += p.bytes
        }
        return totals.filter { $0.value >= minimumBytes }.map { ($0.key, $0.value) }.sorted { $0.bytes > $1.bytes }
    }

    static func message(_ apps: [(name: String, bytes: UInt64)]) -> String? {
        let gb = { (b: UInt64) in String(format: "%.1f GB", Double(b) / 1_073_741_824) }
        let total = apps.reduce(UInt64(0)) { $0 + $1.bytes }
        guard total >= 2 * 1_073_741_824 else { return nil }
        let names = apps.prefix(4).map { "\($0.name) \(gb($0.bytes))" }.joined(separator: ", ")
        return "Other apps are using \(gb(total)) of memory (\(names)). Quitting the ones you don't need reduces stutter and speeds up zone loads."
    }

    /// Physical footprint (resident + compressed, as Activity Monitor shows) per process.
    static func currentProcesses() -> [(path: String, bytes: UInt64)] {
        var pids = [Int32](repeating: 0, count: 8192)
        let count = Int(proc_listallpids(&pids, Int32(pids.count * MemoryLayout<Int32>.size)))
        var result: [(String, UInt64)] = []
        var path = [CChar](repeating: 0, count: 4096)
        for pid in pids.prefix(max(0, count)) where pid > 0 {
            var info = rusage_info_v4()
            let ok = withUnsafeMutablePointer(to: &info) {
                $0.withMemoryRebound(to: rusage_info_t?.self, capacity: 1) { proc_pid_rusage(pid, RUSAGE_INFO_V4, $0) }
            }
            guard ok == 0, proc_pidpath(pid, &path, UInt32(path.count)) > 0 else { continue }
            result.append((String(cString: path), info.ri_phys_footprint))
        }
        return result
    }

    static func notifyIfNeeded() {
        DispatchQueue.global(qos: .utility).async {
            let apps = group(currentProcesses(), excluding: ["MnM on Mac", "MnM Play", "MnM Update"], minimumBytes: 300 * 1_048_576)
            guard let text = message(apps) else { return }
            let center = UNUserNotificationCenter.current()
            center.requestAuthorization(options: [.alert]) { granted, _ in
                guard granted else { return }
                let content = UNMutableNotificationContent()
                content.title = "Free up memory for Monsters & Memories"
                content.body = text
                center.add(UNNotificationRequest(identifier: "mnm-memory-headsup", content: content, trigger: nil))
            }
        }
    }
}
