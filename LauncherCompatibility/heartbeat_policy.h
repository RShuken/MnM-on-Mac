/* How long the Windows helper waits for the Mac controller's heartbeat before
 * deciding it is gone. Times are FILETIME units (100 ns).
 *
 * 20 s was too short: macOS can throttle a backgrounded app (App Nap, a busy
 * main thread) while the game is in front, and closing the launcher on a
 * false expiry took the whole session down with it. 60 s matches the game
 * wait limit, and a controller that has really exited is still detected. */
#define MNM_CONTROLLER_HEARTBEAT_LIMIT 600000000ULL
static int heartbeat_stale(unsigned long long now,unsigned long long last) {
    return now>last && now-last>MNM_CONTROLLER_HEARTBEAT_LIMIT;
}
