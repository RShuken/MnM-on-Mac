/* Per-frame timing for the game process, for the performance log.
 *
 * Loaded into the game's Wine process (DYLD_INSERT_LIBRARIES) only when the
 * Performance Log is on and MNM_FRAME_STATS_PATH is set. DXMT and D3DMetal
 * both call -[CAMetalLayer nextDrawable] once per frame; timestamping it
 * costs nanoseconds and replaces 400 MB Instruments snapshots. Once a second
 * one line is appended: unix_ms,frames,avg_ms,max_ms,over50ms,over100ms.
 * Never touches Windows code, rendering, or what is presented.
 */
#import <objc/runtime.h>
#include <mach-o/dyld.h>
#include <dispatch/dispatch.h>
#include <mach/mach_time.h>
#include <os/lock.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/time.h>
#include <dlfcn.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

static id (*stats_original)(id, SEL);
static os_unfair_lock stats_lock = OS_UNFAIR_LOCK_INIT;
static int stats_fd = -1;
static double stats_tick_ns;
static uint64_t stats_last, stats_bucket_start;
static unsigned stats_frames, stats_over50, stats_over100;
static double stats_sum_ms, stats_max_ms;

static void stats_flush(uint64_t now) {
    if (stats_fd < 0 || !stats_frames) return;
    struct timeval tv; gettimeofday(&tv, NULL);
    char line[160];
    int n = snprintf(line, sizeof line, "%lld,%u,%.2f,%.2f,%u,%u\n",
                     (long long)tv.tv_sec * 1000 + tv.tv_usec / 1000, stats_frames,
                     stats_sum_ms / stats_frames, stats_max_ms, stats_over50, stats_over100);
    if (n > 0) (void)write(stats_fd, line, (size_t)n);
    stats_frames = stats_over50 = stats_over100 = 0; stats_sum_ms = stats_max_ms = 0;
    stats_bucket_start = now;
}

static id stats_next_drawable(id self, SEL cmd) {
    uint64_t now = mach_absolute_time();
    os_unfair_lock_lock(&stats_lock);
    if (stats_last) {
        double ms = (double)(now - stats_last) * stats_tick_ns / 1e6;
        if (ms < 5000) {   /* ignore pauses (minimized, debugger) */
            stats_frames++; stats_sum_ms += ms;
            if (ms > stats_max_ms) stats_max_ms = ms;
            if (ms > 50) stats_over50++;
            if (ms > 100) stats_over100++;
        }
    } else stats_bucket_start = now;
    stats_last = now;
    if ((double)(now - stats_bucket_start) * stats_tick_ns >= 1e9) stats_flush(now);
    os_unfair_lock_unlock(&stats_lock);
    return stats_original(self, cmd);
}

/* Wine's helper processes inherit this library too. It links no frameworks
 * and hooks only once the process itself loads QuartzCore (only the game's
 * renderer does), so other Wine processes pay essentially nothing. */
static int stats_hooked;
static void stats_try_hook(void) {
    if (stats_hooked) return;
    Class layer = objc_getClass("CAMetalLayer");
    Method m = layer ? class_getInstanceMethod(layer, sel_registerName("nextDrawable")) : NULL;
    if (!m) return;
    stats_hooked = 1;
    stats_fd = open(getenv("MNM_FRAME_STATS_PATH"), O_WRONLY | O_CREAT | O_APPEND | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (stats_fd < 0) return;
    mach_timebase_info_data_t tb; mach_timebase_info(&tb);
    stats_tick_ns = (double)tb.numer / tb.denom;
    stats_original = (id (*)(id, SEL))method_setImplementation(m, (IMP)stats_next_drawable);
}
static void stats_image(const struct mach_header *header, intptr_t slide) {
    const char *name = NULL; Dl_info info;
    if (stats_hooked || !dladdr(header, &info)) return;
    name = info.dli_fname;
    /* ObjC classes aren't registered inside the dyld callback: hook just after. */
    if (name && strstr(name, "/QuartzCore.framework/"))
        dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ stats_try_hook(); });
}
/* Write the final partial second when the process exits. */
__attribute__((destructor)) static void stats_finish(void) {
    if (!stats_hooked) return;
    os_unfair_lock_lock(&stats_lock);
    stats_flush(mach_absolute_time());
    os_unfair_lock_unlock(&stats_lock);
}
__attribute__((constructor)) static void stats_start(void) {
    const char *path = getenv("MNM_FRAME_STATS_PATH");
    if (!path || path[0] != '/') return;
    _dyld_register_func_for_add_image(stats_image);
}
