#!/usr/bin/env python3 -I
"""mnm-perf: lightweight live performance monitor for Monsters & Memories on Mac.

  mnm_perf.py watch [--interval 2] [--fps-every 300] [--fps-seconds 3]
  mnm_perf.py fps [--seconds 3]        one FPS snapshot now (play in focus first)
  mnm_perf.py report [CSV]             summarize a session (default: latest)

The watch loop uses only cheap system commands (ps, ioreg, vm_stat, sysctl,
lsappinfo), stores nothing in memory except the previous counters, and appends
one CSV row per tick. FPS comes from short `xctrace` snapshots (Metal
Performance Overview) because macOS has no cheap per-app FPS counter. Each
snapshot briefly costs about 400 MB and some CPU in xctrace, so they are
sparse, flagged in the CSV, and the trace is deleted right after parsing.
"""
import argparse, collections, csv, datetime, os, re, shutil, signal, subprocess, sys, tempfile, time
import xml.etree.ElementTree as ET
# -I drops the script folder from sys.path; gpu_top.py lives next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gpu_top  # noqa: E402

SUPPORT = os.path.expanduser("~/Library/Application Support/MnM on Mac")
OUT_DIR = os.path.join(SUPPORT, "Logs", "perf")
FIELDS = ["time", "focused", "game_pid", "game_cpu", "top_thread_cpu", "threads", "game_rss_mb",
          "wineserver_cpu", "windowserver_cpu", "gpu_device", "gpu_renderer", "gpu_tiler", "game_gpu", "windowserver_gpu", "other_gpu", "other_gpu_top", "mem_free_pct",
          "swapins_s", "pageouts_s", "on_battery", "low_power", "cpu_speed_limit", "thermal_pressure", "loading", "net_iface", "vpn", "system_w", "compressed_gb", "swap_used_gb", "game_footprint_mb", "game_graphics_mb", "game_heap_mb",
          "fps", "skipped_fps", "gpu_usage_xc", "gpu_ms_per_frame", "cmd_buffers", "backend", "variant", "snapshot"]
STOP = False


def run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def game_pid():
    for line in run(["pgrep", "-f", r"mnm\.exe"]).split():
        if line.isdigit():
            return int(line)
    return None


def pids(pattern):
    return [int(p) for p in run(["pgrep", "-f", pattern]).split() if p.isdigit()]


def game_threads(pid):
    """Total CPU, busiest thread CPU, thread count, RSS MB from one `ps -M`."""
    # `ps -M` ignores -o: rows are "USER PID TT %CPU STAT ...", and thread rows
    # leave USER and TT blank. %CPU is the first decimal after the PID.
    threads = []
    for line in run(["ps", "-M", "-p", str(pid)]).splitlines()[1:]:
        m = re.match(r"^\S*\s+%d\s+(?:\S+\s+)?(\d+\.\d+)\s+[A-Z]" % pid, line)
        if m: threads.append(float(m.group(1)))
    total = run(["ps", "-o", "%cpu=,rss=", "-p", str(pid)]).split()
    game_cpu = float(total[0]) if total else 0.0
    rss = int(total[1]) / 1024 if len(total) > 1 else 0.0
    return game_cpu, (max(threads) if threads else 0.0), len(threads), rss


def cpu_of(pid_list):
    if not pid_list: return 0.0
    out = run(["ps", "-o", "%cpu=", "-p", ",".join(map(str, pid_list))])
    return sum(float(x) for x in out.split() if re.match(r"^[\d.]+$", x))


def gpu_stats():
    out = run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"])
    def grab(key):
        m = re.search(r'"%s"=(\d+)' % re.escape(key), out)
        return int(m.group(1)) if m else ""
    return grab("Device Utilization %"), grab("Renderer Utilization %"), grab("Tiler Utilization %")


def vm_counters():
    out = run(["vm_stat"])
    def grab(label):
        m = re.search(r"%s:\s+(\d+)" % label, out)
        return int(m.group(1)) if m else 0
    return grab("Swapins"), grab("Pageouts")


def focused_pid():
    front = run(["lsappinfo", "front"]).strip()
    if not front: return None
    m = re.search(r'\bpid\s*=\s*(\d+)', run(["lsappinfo", "info", "-only", "pid", front]))
    return int(m.group(1)) if m else None


def power_state():
    batt = run(["pmset", "-g", "batt"])
    on_battery = 1 if "Battery Power" in batt else 0
    low = 1 if re.search(r"lowpowermode\s+1", run(["pmset", "-g"])) else 0
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", run(["pmset", "-g", "therm"]))
    return on_battery, low, (int(m.group(1)) if m else 100)


EXPERIMENT_VARS = ["D3DM_MTL4", "D3DM_MAX_FPS", "D3DM_LOD_BIAS", "DXMT_CONFIG"]


def experiment_variant():
    """Session env set with `launchctl setenv` (read by apps launched afterwards)."""
    parts = []
    for k in EXPERIMENT_VARS:
        v = run(["launchctl", "getenv", k]).strip()
        if v: parts.append(f"{k}={v}")
    return " ".join(parts) or "baseline"


THERMAL = {0: "nominal", 1: "moderate", 2: "heavy", 3: "trapping", 4: "sleeping"}


def thermal_pressure():
    m = re.search(r"(\d+)\s*$", run(["notifyutil", "-g", "com.apple.system.thermalpressurelevel"]).strip())
    return int(m.group(1)) if m else ""


PREFIX = {"DXMT": "Prefix", "D3DMetal": "Prefix-D3DMetal", "DXVK": "Prefix-DXVK"}
LOG_REL = "drive_c/users/{user}/AppData/LocalLow/Niche Worlds Cult/Monsters and Memories/Player.log"
EVENT_PATTERNS = [
    re.compile(r"^\[(Client)\]\[ZONE\] (.*)"),
    re.compile(r"^\[(Net|WorldConnection|GameController|NM|MTU)\] (.*)"),
    re.compile(r"^(Received) (SessionStart|CharactersList|CharacterData|EnterWorldResponse|EnterZone)\b(.*)"),
    re.compile(r"(?i)^(?!Warning!)(.*(exception|fatal|crash|disconnect|timed? ?out|out of memory|device (lost|removed)).*)"),
]


class PlayerLogTail:
    """Reads only newly appended lines of the game's Player.log (constant memory)."""
    def __init__(self):
        self.path = None; self.inode = None; self.offset = 0

    def new_lines(self, backend):
        path = os.path.join(SUPPORT, PREFIX.get(backend, "Prefix"), LOG_REL.format(user=os.environ.get("USER", "")))
        try:
            st = os.stat(path)
        except OSError:
            return []
        if path != self.path or st.st_ino != self.inode or st.st_size < self.offset:
            # New session log: start from the top so the whole load is captured.
            self.path, self.inode, self.offset = path, st.st_ino, 0
        if st.st_size == self.offset:
            return []
        with open(path, "rb") as f:
            f.seek(self.offset)
            data = f.read(min(st.st_size - self.offset, 4 * 1024 * 1024))
        cut = data.rfind(b"\n") + 1
        self.offset += cut
        return data[:cut].decode("utf-8", "replace").splitlines()


STACK_FRAME = re.compile(r"^[\w.`<>$\[\]]+:[\w.`<>$|]+\(")  # Unity stack frames: Namespace.Type:Method(args)


def interesting(line):
    if STACK_FRAME.match(line.strip()):
        return None
    for pat in EVENT_PATTERNS:
        m = pat.search(line)
        if m:
            return line.strip()[:240]
    return None


def game_settings(backend):
    """Key graphics/FPS settings from the game's settings.json, as one short string."""
    import json
    path = os.path.join(SUPPORT, PREFIX.get(backend, "Prefix"), os.path.dirname(LOG_REL.format(user=os.environ.get("USER", ""))), "settings.json")
    try:
        d = json.load(open(path))
    except Exception:
        return ""
    keys = ["GameSettingTargetFramerate", "GameSettingTargetBackgroundFramerate", "GameSettingQualityLevel",
            "GameSettingShadowsEnabled", "GameSettingDisplayResolution"]
    return " ".join(f"{k.replace('GameSetting', '')}={d[k]}" for k in keys if k in d)


def stuck_bundle(pid, backend, last_step, since, tail_lines):
    """Capture what a stuck zone load is doing, while it is stuck."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(OUT_DIR, f"stuck-load-{stamp}.txt")
    with open(path, "w") as out:
        out.write(f"Stuck zone load: no progress for {since:.0f}s\nLast step: {last_step}\n")
        out.write(f"Backend: {backend}  focused pid: {focused_pid()}  game pid: {pid}\n")
        out.write(f"Settings: {game_settings(backend)}\n\n== Last game log lines\n")
        out.write("\n".join(tail_lines[-80:]) + "\n\n")
        out.write("== Threads (%CPU, state)\n" + run(["ps", "-M", "-p", str(pid)])[-6000:] + "\n")
        # Network: is the client still connected to the world/zone servers?
        out.write("== Network sockets\n" + run(["lsof", "-nP", "-a", "-i", "-p", str(pid)]) + "\n")
        snap = fps_snapshot(pid, 3)
        out.write(f"== Frames being presented (3 s)\n{snap}\n\n")
        out.write("== GPU by process\n" + "\n".join(f"{u:.1f}% {n}" for u, _, n in gpu_top.sample(3)[:8]) + "\n\n")
        out.write("== Thread sample (3 s)\n")
        smp = os.path.join(OUT_DIR, f"stuck-load-{stamp}-sample.txt")
        subprocess.run(["sample", str(pid), "3", "-mayDie", "-file", smp], capture_output=True, timeout=120)
        out.write(f"saved to {smp}\n")
    return path


def system_power_w():
    """Whole-Mac power draw in watts (SystemLoad from the battery controller; works on AC, no sudo)."""
    m = re.search(r'"SystemLoad"=(\d+)', run(["ioreg", "-rn", "AppleSmartBattery"]))
    return round(int(m.group(1)) / 1000, 1) if m else ""


def memory_state():
    """(compressed memory GB, swap used GB)."""
    vm = run(["vm_stat"])
    page = int(re.search(r"page size of (\d+)", vm).group(1)) if "page size of" in vm else 16384
    m = re.search(r"Pages occupied by compressor:\s+(\d+)", vm)
    comp = round(int(m.group(1)) * page / 2**30, 2) if m else ""
    sw = re.search(r"used = ([\d.]+)M", run(["sysctl", "-n", "vm.swapusage"]))
    return comp, (round(float(sw.group(1)) / 1024, 2) if sw else "")


FRIENDLY = {"BDLDaemon": "Bitdefender", "bdagentd": "Bitdefender", "krisp": "Krisp", "stable": "Warp"}
IGNORE_MEM = re.compile(r"^(wine|wineserver|kernel_task|WindowServer|mnm_perf|python3?|top|launchd|mds|mds_stores)$")


def memory_hogs(game_pid, min_mb=400):
    """Other apps holding more than min_mb (resident + compressed), largest first."""
    out = run(["top", "-l", "1", "-o", "mem", "-n", "25", "-stats", "pid,mem,command"], timeout=20)
    hogs = {}
    for line in out.splitlines():
        m = re.match(r"^\s*(\d+)\s+([\d.]+)([KMG])\+?-?\s+(.+?)\s*$", line)
        if not m or int(m.group(1)) == game_pid: continue
        mb = float(m.group(2)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(3)]
        name = re.sub(r"\s*(Helper|Renderer|He|\(.*\)).*$", "", m.group(4)).strip() or m.group(4)
        name = FRIENDLY.get(name, "Claude Code" if re.match(r"^\d+\.\d+\.\d+$", name) else name)
        if IGNORE_MEM.match(name): continue
        hogs[name] = hogs.get(name, 0) + mb
    return sorted(((n, mb) for n, mb in hogs.items() if mb >= min_mb), key=lambda x: -x[1])


def game_footprint(pid):
    """Game memory from `footprint`: total, graphics (all '(graphics)' rows) and Wine heap, in MB."""
    out = run(["footprint", "-p", str(pid)], timeout=30)
    def mb(v, unit):
        return float(v) * {"B": 1 / 2**20, "KB": 1 / 1024, "MB": 1, "GB": 1024}.get(unit, 0)
    total = re.search(r"Footprint:\s*([\d.]+)\s*(KB|MB|GB)", out)
    graphics = heap = 0.0
    for line in out.splitlines():
        m = re.match(r"^\s*([\d.]+)\s*(B|KB|MB|GB)\s", line)
        if not m: continue
        category = re.split(r"\s{2,}", line.strip())[-1]  # last column; values are space-padded
        if "(graphics)" in category: graphics += mb(m.group(1), m.group(2))
        elif category.startswith("WINE_RESERVE"): heap += mb(m.group(1), m.group(2))
    return (round(mb(*total.groups())) if total else "", round(graphics), round(heap))


def network_path():
    """(interface carrying default traffic, connected VPN names if that interface is a tunnel)."""
    m = re.search(r"interface:\s*(\S+)", run(["route", "-n", "get", "default"]))
    iface = m.group(1) if m else ""
    vpns = []
    if iface.startswith(("utun", "ipsec", "ppp", "tun", "wg")):
        for line in run(["scutil", "--nc", "list"]).splitlines():
            if "(Connected)" in line:
                n = re.search(r'"([^"]+)"', line)
                if n: vpns.append(n.group(1).strip())
    return iface, ", ".join(vpns) if vpns else ("unknown VPN" if iface.startswith(("utun", "ipsec", "ppp", "tun", "wg")) else "")


def graphics_backend():
    m = re.search(r"MnMGraphicsBackend\s*=\s*(\w+)", run(["defaults", "read", "io.github.seathasky.mnmonmac"]))
    return {"metal": "DXMT", "d3dmetal": "D3DMetal", "dxvk": "DXVK"}.get(m.group(1).lower(), m.group(1)) if m else ""


def fps_snapshot(pid, seconds=3):
    """Record Metal Performance Overview briefly; return averaged frame metrics for pid."""
    tmp = tempfile.mkdtemp(prefix="mnm-perf-")
    trace = os.path.join(tmp, "s.trace")
    try:
        subprocess.run(["nice", "-n", "10", "xcrun", "xctrace", "record",
                        "--instrument", "Metal Performance Overview", "--all-processes",
                        "--time-limit", f"{seconds}s", "--output", trace, "--no-prompt"],
                       capture_output=True, timeout=180)
        xml = subprocess.run(["xcrun", "xctrace", "export", "--input", trace, "--xpath",
                              '/trace-toc/run[@number="1"]/data/table[@schema="metal-perf-overview-layer-count-metric"]'],
                             capture_output=True, timeout=120).stdout
        if not xml: return None
        root = ET.fromstring(xml)
        ids, sums, counts = {}, {}, {}
        def resolve(e):
            ref = e.get("ref")
            if ref: return ids.get(ref, e)
            if e.get("id"): ids[e.get("id")] = e
            for child in e: resolve(child)
            return e
        for row in root.iter("row"):
            cells = {c.tag: c for c in (resolve(c) for c in row)}
            proc = cells.get("process")
            pid_el = proc.find("pid") if proc is not None else None
            if pid_el is None or pid_el.text != str(pid): continue
            idx, value = cells.get("uint32"), cells.get("fixed-decimal")
            if idx is None or value is None: continue
            k = int(idx.text); sums[k] = sums.get(k, 0.0) + float(value.text); counts[k] = counts.get(k, 0) + 1
        avg = {k: sums[k] / counts[k] for k in sums}
        # 0 FPS, 1 skipped frames, 2 direct-to-display, 3 command buffers/frame, 4 GPU usage %
        fps, gpu = avg.get(0, 0), avg.get(4, 0)
        # Game GPU share x frame time: how long the GPU works on each frame.
        gpu_ms = round(gpu / 100 * 1000 / fps, 1) if fps > 0 else ""
        return {"fps": round(fps, 1), "skipped_fps": round(avg.get(1, 0), 1),
                "cmd_buffers": round(avg.get(3, 0), 1), "gpu_usage_xc": round(gpu, 1),
                "gpu_ms_per_frame": gpu_ms}
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_session(args):
    """Log one game session to its own CSV + events file; returns when the game exits."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, datetime.datetime.now().strftime("perf-%Y%m%d-%H%M%S.csv"))
    exited = False
    print(f"mnm-perf: logging to {path} (Ctrl-C to stop)", flush=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: globals().__setitem__("STOP", True))
    last_swap, last_t = vm_counters(), time.time()
    backend = ""
    variant = ""
    last_pid_seen = None
    settings_pid = None
    net_pid, net_t, net_last = None, 0.0, ("", "")
    mem_pid, mem_t = None, 0.0
    fp_pid, fp_t = None, 0.0
    recent, last_step, last_step_t, stuck_reported = [], "", time.time(), False
    last_pid = None
    last_gpu = None
    gpu_clock = [time.time()]
    def now_gpu_dt():
        t = time.time(); dt = t - gpu_clock[0]; gpu_clock[0] = t; return dt
    power, power_t = power_state(), time.time()
    next_fps = time.time() + 20 if args.fps_every else float("inf")
    tail = PlayerLogTail()
    loading = False
    ev_path = path.replace(".csv", "-events.csv")
    evf = open(ev_path, "w", newline="")
    ev = csv.writer(evf); ev.writerow(["time", "focused", "game_pid", "game_cpu", "top_thread_cpu", "game_gpu", "loading", "event"])
    def event(text, row):
        ev.writerow([row["time"], row["focused"], row["game_pid"], row["game_cpu"], row["top_thread_cpu"],
                     row.get("game_gpu", ""), 1 if loading else 0, text]); evf.flush()
        if not args.quiet: print(f"  EVENT {row['time']} focus={row['focused']} {text}", flush=True)
    last_focus = None
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); f.flush()
        while not STOP:
            start = time.time()
            pid = game_pid()
            if pid != last_pid:
                backend, last_pid = graphics_backend(), pid
                variant = experiment_variant()
            row = dict.fromkeys(FIELDS, "")
            row["time"] = datetime.datetime.now().strftime("%H:%M:%S")
            if pid:
                row["game_pid"] = pid
                row["focused"] = 1 if focused_pid() == pid else 0
                c, top, n, rss = game_threads(pid)
                row.update(game_cpu=round(c, 1), top_thread_cpu=round(top, 1), threads=n, game_rss_mb=round(rss))
                row["wineserver_cpu"] = round(cpu_of(pids("wineserver")), 1)
                row["windowserver_cpu"] = round(cpu_of(pids("^/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer")), 1)
                row["backend"] = backend
                row["variant"] = variant
            row["gpu_device"], row["gpu_renderer"], row["gpu_tiler"] = gpu_stats()
            # Per-process GPU share from the driver's accumulated GPU time.
            gt, gnames = gpu_top.gpu_times()
            if last_gpu is not None:
                gdt = max(now_gpu_dt(), 0.001)
                use = {p: (gt[p] - last_gpu.get(p, 0)) / (gdt * 1e9) * 100 for p in gt}
                ws = sum(u for p, u in use.items() if gnames.get(p) == "WindowServer")
                g = use.get(pid, 0.0) if pid else 0.0
                others = sorted(((u, gnames.get(p, "?")) for p, u in use.items()
                                 if p != pid and gnames.get(p) != "WindowServer" and u > 0.05), reverse=True)
                row.update(game_gpu=round(g, 1), windowserver_gpu=round(ws, 1),
                           other_gpu=round(sum(u for u, _ in others), 1),
                           other_gpu_top=";".join(f"{n}:{u:.0f}" for u, n in others[:3]))
            if last_gpu is None: now_gpu_dt()  # start the GPU clock at the first reading
            last_gpu = gt
            m = re.search(r"(\d+)", run(["sysctl", "-n", "kern.memorystatus_level"]))
            row["mem_free_pct"] = int(m.group(1)) if m else ""
            swap, now = vm_counters(), time.time()
            dt = max(now - last_t, 0.001)
            row["swapins_s"] = round((swap[0] - last_swap[0]) / dt, 1)
            row["pageouts_s"] = round((swap[1] - last_swap[1]) / dt, 1)
            last_swap, last_t = swap, now
            if now - power_t >= 60: power, power_t = power_state(), now
            row["on_battery"], row["low_power"], row["cpu_speed_limit"] = power
            row["thermal_pressure"] = thermal_pressure()
            row["system_w"] = system_power_w()
            if pid and (pid != fp_pid or now - fp_t >= 300):
                row["game_footprint_mb"], row["game_graphics_mb"], row["game_heap_mb"] = game_footprint(pid)
                fp_pid, fp_t = pid, now
            if pid and (pid != mem_pid or now - mem_t >= 300):
                hogs, mem_pid, mem_t = memory_hogs(pid), pid, now
                if hogs:
                    total = sum(mb for _, mb in hogs) / 1024
                    event(f"MEMORY: other apps are holding {total:.1f} GB while the game runs: "
                          + ", ".join(f"{n} {mb/1024:.1f} GB" for n, mb in hogs[:6])
                          + ". Quitting the ones you don't need gives the game room and reduces swapping.", row)
            row["compressed_gb"], row["swap_used_gb"] = memory_state()
            if pid and (pid != net_pid or now - net_t >= 60):
                net, net_t = network_path(), now
                if net[1] and (pid != net_pid or net != net_last):
                    event(f"NETWORK WARNING: game traffic is going through a VPN ({net[1]}, {net[0]}). "
                          "VPNs can drop the game's UDP zone traffic and stall loading at 'Waiting for readyZone'.", row)
                elif net_last[1] and not net[1]:
                    event(f"NETWORK: VPN no longer carrying game traffic (now {net[0]})", row)
                net_pid, net_last = pid, net
            row["net_iface"], row["vpn"] = net_last
            if pid:
                for line in tail.new_lines(backend):
                    recent.append(line); del recent[:-200]
                    text = interesting(line)
                    if not text: continue
                    if "Start zoning process" in text: loading = True; stuck_reported = False
                    if loading and text.startswith("[Client][ZONE]"): last_step, last_step_t = text, now
                    event(text, row)
                    if "[Client][ZONE] Done loading" == text: loading = False
                if loading and not stuck_reported and now - last_step_t > args.stuck_after:
                    event(f"STUCK LOAD: no progress for {now - last_step_t:.0f}s at '{last_step}'", row)
                    bundle = stuck_bundle(pid, backend, last_step, now - last_step_t, recent)
                    event(f"STUCK LOAD diagnostics saved: {bundle}", row)
                    stuck_reported = True
                if pid != settings_pid:
                    event("SETTINGS " + game_settings(backend) + f" backend={backend} variant={variant}", row)
                    settings_pid = pid
                if row["focused"] != last_focus and last_focus is not None:
                    event("FOCUS " + ("gained" if row["focused"] == 1 else "lost"), row)
                last_focus = row["focused"]
            elif last_pid_seen:
                try:
                    import json
                    st = json.load(open(os.path.join(SUPPORT, "Logs", "game-status.json")))
                    msg = f"GAME EXIT state={st.get('state')} message={st.get('message')}"
                except Exception:
                    msg = "GAME EXIT"
                event(msg + (" (during zone load)" if loading else ""), row)
                loading = False; last_focus = None
                exited = True
            last_pid_seen = pid
            row["loading"] = 1 if loading else 0
            # FPS snapshots only while the game is in front; retry soon otherwise.
            if pid and now >= next_fps and row["focused"] != 1:
                next_fps = now + 15
            elif pid and now >= next_fps:
                snap = fps_snapshot(pid, args.fps_seconds) or fps_snapshot(pid, args.fps_seconds)
                if snap: row.update(snap)
                row["snapshot"] = 1
                next_fps = time.time() + args.fps_every
            w.writerow(row); f.flush()
            if not args.quiet:
                print(f"{row['time']} focus={row['focused']} cpu={row['game_cpu']} top={row['top_thread_cpu']} "
                      f"gpu={row['gpu_device']}% mem_free={row['mem_free_pct']}% swapin/s={row['swapins_s']}"
                      + (f" FPS={row['fps']} skipped={row['skipped_fps']}" if row['snapshot'] else ""), flush=True)
            if exited:
                break
            time.sleep(max(0.0, args.interval - (time.time() - start)))
    evf.close()
    return path


def write_report(path):
    """Save the session report next to its CSV (perf-...-report.txt)."""
    import contextlib, io
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            report(argparse.Namespace(csv=path))
    except Exception as e:
        buf.write(f"\nreport failed: {e}\n")
    with open(path.replace(".csv", "-report.txt"), "w") as f:
        f.write(buf.getvalue())


def prune_logs(keep=40):
    """Keep the newest `keep` sessions (CSV, events, report) and their stuck-load bundles."""
    sessions = sorted(f for f in os.listdir(OUT_DIR) if re.match(r"^perf-\d{8}-\d{6}.*\.csv$", f) and not f.endswith("-events.csv"))
    for old in sessions[:-keep]:
        stem = old[:-4]
        for name in os.listdir(OUT_DIR):
            if name == old or name.startswith(stem + "-"):
                try: os.remove(os.path.join(OUT_DIR, name))
                except OSError: pass
    if len(sessions) > keep:
        oldest_kept = sessions[-keep][5:20]  # YYYYMMDD-HHMMSS
        for name in os.listdir(OUT_DIR):
            m = re.match(r"^stuck-load-(\d{8}-\d{6})", name)
            if m and m.group(1) < oldest_kept:
                try: os.remove(os.path.join(OUT_DIR, name))
                except OSError: pass


def watch(args):
    """Always-on: sleep while the game isn't running; log each game session and report it."""
    os.makedirs(OUT_DIR, exist_ok=True)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: globals().__setitem__("STOP", True))
    print("mnm-perf: waiting for the game (checks every 10 s)", flush=True)
    waited = 0
    while not STOP:
        if not game_pid():
            # --once: started by MnM on Mac at game launch; give up if the game never appears.
            if args.once and waited >= 120:
                return
            time.sleep(2 if args.once else 10); waited += 2 if args.once else 10
            continue
        path = run_session(args)
        print(f"mnm-perf: session logged to {path}", flush=True)
        write_report(path)
        prune_logs(args.keep)
        if args.once:
            return


def latest_csv():
    files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".csv")) if os.path.isdir(OUT_DIR) else []
    return os.path.join(OUT_DIR, files[-1]) if files else None


def report(args):
    path = args.csv or latest_csv()
    if not path: sys.exit("No perf logs yet. Run: watch")
    rows = list(csv.DictReader(open(path)))
    def nums(rs, k):
        out = []
        for r in rs:
            try: out.append(float(r[k]))
            except (ValueError, KeyError): pass
        return out
    def stat(v):
        if not v: return "n/a"
        s = sorted(v)
        return f"avg {sum(v)/len(v):.1f}  min {s[0]:.1f}  p50 {s[len(s)//2]:.1f}  max {s[-1]:.1f}  (n={len(v)})"
    game = [r for r in rows if r.get("game_pid")]
    print(f"Session {os.path.basename(path)}: {len(rows)} samples, {len(game)} with the game running "
          f"({rows[0]['time'] if rows else ''} to {rows[-1]['time'] if rows else ''})\n")
    for label, sel in (("IN FOCUS", "1"), ("BACKGROUND", "0")):
        rs = [r for r in game if r["focused"] == sel and r.get("snapshot") != "1"]
        snaps = [r for r in game if r["focused"] == sel and r.get("snapshot") == "1"]
        print(f"== {label} ({len(rs)} samples, {len(snaps)} FPS snapshots)")
        print(f"  FPS presented   {stat(nums(snaps, 'fps'))}")
        print(f"  FPS skipped     {stat(nums(snaps, 'skipped_fps'))}")
        print(f"  game CPU %      {stat(nums(rs, 'game_cpu'))}")
        print(f"  busiest thread  {stat(nums(rs, 'top_thread_cpu'))}")
        print(f"  GPU ms/frame    {stat(nums(snaps, 'gpu_ms_per_frame'))}")
        print(f"  GPU device %    {stat(nums(rs, 'gpu_device'))}")
        print(f"  WindowServer %  {stat(nums(rs, 'windowserver_cpu'))}")
        print(f"  GPU: game %     {stat(nums(rs, 'game_gpu'))}")
        print(f"  GPU: WindowSrv  {stat(nums(rs, 'windowserver_gpu'))}")
        print(f"  GPU: other apps {stat(nums(rs, 'other_gpu'))}")
        print(f"  wineserver CPU  {stat(nums(rs, 'wineserver_cpu'))}")
        print(f"  mem free %      {stat(nums(rs, 'mem_free_pct'))}")
        print(f"  swap-ins/s      {stat(nums(rs, 'swapins_s'))}")
        print(f"  system watts    {stat(nums(rs, 'system_w'))}")
        print(f"  compressed GB   {stat(nums(rs, 'compressed_gb'))}")
        print(f"  swap used GB    {stat(nums(rs, 'swap_used_gb'))}\n")
    combos = sorted({(r.get("backend", ""), r.get("variant", "")) for r in game if r["focused"] == "1"})
    if combos:
        print("== By backend + variant (in focus)")
        for be, va in combos:
            sel = [r for r in game if (r.get("backend", ""), r.get("variant", "")) == (be, va) and r["focused"] == "1"]
            snaps = [r for r in sel if r.get("snapshot") == "1"]
            ticks = [r for r in sel if r.get("snapshot") != "1"]
            print(f"  {be or '?'} [{va or 'baseline'}]  samples={len(ticks)}")
            print(f"     FPS {stat(nums(snaps, 'fps'))}")
            print(f"     GPU ms/frame {stat(nums(snaps, 'gpu_ms_per_frame'))}")
            print(f"     game GPU % {stat(nums(ticks, 'game_gpu'))} | busiest thread {stat(nums(ticks, 'top_thread_cpu'))}")
            print(f"     game RSS MB {stat(nums(ticks, 'game_rss_mb'))} | swap-ins/s {stat(nums(ticks, 'swapins_s'))}")
        print()
    focus = [r for r in game if r["focused"] == "1" and r.get("snapshot") != "1"]
    fps = nums([r for r in game if r["focused"] == "1" and r.get("snapshot") == "1"], "fps")
    tp = nums(game, "thermal_pressure")
    if tp:
        c = collections.Counter(int(x) for x in tp)
        print("== Thermal pressure: " + ", ".join(f"{THERMAL.get(k, k)} {v*100//len(tp)}%" for k, v in sorted(c.items())) + "\n")
    evp = path.replace(".csv", "-events.csv")
    if os.path.exists(evp):
        evs = list(csv.DictReader(open(evp)))
        bad = [e for e in evs if e["event"].startswith(("GAME EXIT", "NETWORK", "MEMORY")) or (e["loading"] == "1" and e["event"].startswith("FOCUS"))
               or re.search(r"(?i)exception|fatal|crash|disconnect|timed? ?out|bad!", e["event"])]
        print(f"== Events: {len(evs)} logged, {len(bad)} notable")
        for e in bad[-15:]:
            print(f"  {e['time']} focus={e['focused']} loading={e['loading']} {e['event'][:150]}")
        print()
    # Long-session memory: growth of the game's heap and graphics memory per hour.
    for pidv in sorted({r["game_pid"] for r in game}):
        pts = [(r["time"], float(r["game_heap_mb"]), float(r["game_graphics_mb"]), float(r["game_footprint_mb"]))
               for r in game if r["game_pid"] == pidv and r.get("game_heap_mb") not in ("", None)]
        if len(pts) >= 3:
            h = lambda t: sum(int(x) * k for x, k in zip(t.split(":"), (3600, 60, 1)))
            hrs = (h(pts[-1][0]) - h(pts[0][0])) / 3600 or 1e-9
            print(f"== Game memory over time (pid {pidv}, {hrs*60:.0f} min): total {pts[0][3]:.0f} -> {pts[-1][3]:.0f} MB, "
                  f"heap {(pts[-1][1]-pts[0][1])/hrs:+.0f} MB/h, graphics {(pts[-1][2]-pts[0][2])/hrs:+.0f} MB/h\n")
            if hrs >= 0.5 and (pts[-1][1] - pts[0][1]) / hrs > 300:
                print("  - The game's Windows heap keeps growing (> 300 MB/h): long sessions may slowly get worse.\n")
    print("== Likely bottlenecks (in focus)")
    findings = []
    if fps and sum(fps) / len(fps) >= 29: findings.append("FPS is at the 30 cap: no bottleneck while in focus.")
    top, gpu = nums(focus, "top_thread_cpu"), nums(focus, "gpu_device")
    if top and sorted(top)[len(top)//2] >= 85:
        findings.append("CPU-bound on one thread (busiest thread at about a full core). The game's main/render thread "
                        "runs x86 code under Rosetta + Wine. Lower CPU-heavy settings (view distance, shadows, "
                        "player/NPC density) and close other CPU users.")
    if gpu and sorted(gpu)[len(gpu)//2] >= 85:
        findings.append("GPU-bound (GPU above 85%). Lower resolution or render scale, try MetalFX upscaling in the cog menu, "
                        "or reduce shadows and effects.")
    if top and gpu and sorted(top)[len(top)//2] < 85 and sorted(gpu)[len(gpu)//2] < 85 and fps and sum(fps)/len(fps) < 28:
        findings.append("Neither the CPU thread nor the GPU is saturated, yet FPS is under 30: suspect frame pacing or "
                        "sync stalls (Wine/wineserver, msync, DXMT present). Try toggling msync, or "
                        "DXMT_CONFIG=d3d11.preferredMaxFrameRate=30.")
    og, wg, gg = nums(focus, "other_gpu"), nums(focus, "windowserver_gpu"), nums(focus, "game_gpu")
    if gpu and sorted(gpu)[len(gpu)//2] >= 85 and (og or wg):
        tops = collections.Counter(n.split(":")[0] for r in focus for n in (r.get("other_gpu_top") or "").split(";") if n)
        findings.append(f"The GPU is shared: game {sum(gg)/max(len(gg),1):.0f}%, WindowServer {sum(wg)/max(len(wg),1):.0f}%, "
                        f"other apps {sum(og)/max(len(og),1):.0f}% (mostly {', '.join(n for n, _ in tops.most_common(3)) or 'n/a'}). "
                        "Closing those apps, disconnecting the external display, or using the default display scaling gives the game more GPU.")
    sw = nums(focus, "swapins_s")
    if sw and sum(sw) / len(sw) > 50:
        findings.append(f"Memory pressure: about {sum(sw)/len(sw):.0f} swap-ins/s. Close Chrome tabs and other apps while playing.")
    if tp and sum(1 for x in tp if x >= 2) > len(tp) * 0.2:
        findings.append("Heavy thermal pressure for a large part of the session: the fanless Air is throttling CPU/GPU clocks.")
    lim = nums(game, "cpu_speed_limit")
    if lim and min(lim) < 100:
        findings.append(f"Thermal throttling: CPU speed limited to {min(lim):.0f}%. Fanless Air: use a cooler surface, or plug in.")
    if any(r.get("on_battery") == "1" for r in game):
        findings.append("Played on battery for part of the session. Plug in for full performance.")
    if any(r.get("low_power") == "1" for r in game):
        findings.append("Low Power Mode was on. It caps CPU/GPU clocks.")
    bg = nums([r for r in game if r["focused"] == "0" and r.get("snapshot") == "1"], "fps")
    if bg and max(bg) > 6:
        findings.append(f"Background FPS reached {max(bg):.0f}: the out-of-focus limit (5 FPS) isn't being applied.")
    for line in findings or ["Not enough data yet. Play in focus with watch running for a few minutes."]:
        print("  - " + line)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch"); w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--fps-every", type=float, default=300, help="seconds between FPS snapshots, 0 = off")
    w.add_argument("--fps-seconds", type=int, default=3)
    w.add_argument("--stuck-after", type=float, default=60, help="seconds without zone-load progress before capturing diagnostics")
    w.add_argument("--keep", type=int, default=40, help="game sessions to keep in the log folder")
    w.add_argument("--once", action="store_true", help="log one game session, then exit (used by MnM on Mac)"); w.add_argument("--quiet", action="store_true")
    s = sub.add_parser("fps"); s.add_argument("--seconds", type=int, default=3)
    r = sub.add_parser("report"); r.add_argument("csv", nargs="?")
    a = p.parse_args()
    if a.cmd == "watch": watch(a)
    elif a.cmd == "fps":
        pid = game_pid()
        if not pid: sys.exit("The game isn't running.")
        print(fps_snapshot(pid, a.seconds) or "No frame data (is the game window visible?)")
    else: report(a)


if __name__ == "__main__":
    main()
