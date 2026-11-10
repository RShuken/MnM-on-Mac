import Foundation
import Darwin

/// Wine processes from MnM's own runtime whose wineserver is gone can't do anything,
/// but they stay resident: winedevice.exe services were seen surviving for hours,
/// some from old engine copies replaced during updates. Clean them up at startup.
enum WineOrphans {
    /// A process is orphaned when no wineserver from its engine folder is running.
    static func find(processes: [(pid: Int32, path: String)], runtimeRoot: String) -> [Int32] {
        let root = runtimeRoot.hasSuffix("/") ? runtimeRoot : runtimeRoot + "/"
        func engine(_ path: String) -> String? {
            guard path.hasPrefix(root), let bin = path.range(of: "/bin/", options: .backwards) else { return nil }
            return String(path[..<bin.lowerBound])
        }
        let ours = processes.compactMap { p in engine(p.path).map { (p.pid, $0, p.path.hasSuffix("/bin/wineserver")) } }
        let liveEngines = Set(ours.filter { $0.2 }.map { $0.1 })
        return ours.filter { !$0.2 && !liveEngines.contains($0.1) }.map { $0.0 }
    }

    /// All processes with their executable paths (proc_pidpath: Wine rewrites ps names).
    static func allProcesses() -> [(pid: Int32, path: String)] {
        var pids = [Int32](repeating: 0, count: 8192)
        let count = Int(proc_listallpids(&pids, Int32(pids.count * MemoryLayout<Int32>.size)))
        var result: [(Int32, String)] = []
        var buffer = [CChar](repeating: 0, count: 4096)
        for pid in pids.prefix(max(0, count)) where pid > 0 {
            if proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 { result.append((pid, String(cString: buffer))) }
        }
        return result
    }

    /// Terminate orphans, then force-quit any still running after a grace period.
    @discardableResult
    static func cleanUp(runtimeRoot: URL) -> [Int32] {
        let orphans = find(processes: allProcesses(), runtimeRoot: runtimeRoot.standardizedFileURL.path)
        for pid in orphans { kill(pid, SIGTERM) }
        if !orphans.isEmpty {
            DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 3) {
                for pid in orphans where kill(pid, 0) == 0 { kill(pid, SIGKILL) }
            }
        }
        return orphans
    }
}
