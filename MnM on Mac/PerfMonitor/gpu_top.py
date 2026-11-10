#!/usr/bin/env python3 -I
"""gpu_top: per-process GPU busy %, from the GPU driver's accumulated GPU time.

  gpu_top.py [seconds]   (default 5)

Reads AGXDeviceUserClient "AppUsage" accumulatedGPUTime (nanoseconds) twice
and prints each process's share of wall time. Cheap: two ioreg calls, no sudo.
Values above 100% are possible (the GPU runs work from several queues at once).
"""
import collections, re, subprocess, sys, time


def gpu_times():
    out = subprocess.run(["ioreg", "-r", "-l", "-c", "AGXDeviceUserClient"],
                         capture_output=True, text=True, timeout=20).stdout
    totals, names = collections.defaultdict(int), {}
    # Each user client block lists AppUsage, then its IOUserClientCreator.
    for block in out.split("+-o AGXDeviceUserClient")[1:]:
        m = re.search(r'"IOUserClientCreator" = "pid (\d+), ([^"]*)"', block)
        if not m:
            continue
        pid = int(m.group(1)); names[pid] = m.group(2)
        totals[pid] += sum(int(x) for x in re.findall(r'"accumulatedGPUTime"=(\d+)', block))
    return totals, names


def sample(seconds=5.0):
    a, _ = gpu_times(); t0 = time.time()
    time.sleep(seconds)
    b, names = gpu_times(); dt = time.time() - t0
    usage = {pid: (b[pid] - a.get(pid, 0)) / (dt * 1e9) * 100 for pid in b}
    return sorted(((u, pid, names.get(pid, "?")) for pid, u in usage.items() if u > 0.05), reverse=True)


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    rows = sample(secs)
    print(f"GPU busy by process over {secs:.0f}s:")
    for u, pid, name in rows[:12]:
        print(f"  {u:6.1f}%  pid {pid:<6} {name}")
    print(f"  {sum(r[0] for r in rows):6.1f}%  total")
