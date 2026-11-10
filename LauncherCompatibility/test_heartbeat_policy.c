#include <assert.h>
#include <stdio.h>
#include "heartbeat_policy.h"
#define SECONDS(n) ((unsigned long long)(n)*10000000ULL)
int main(void) {
    unsigned long long base=SECONDS(1000000);
    /* A fresh heartbeat is never stale. */
    assert(!heartbeat_stale(base+SECONDS(1),base));
    /* The old 20 s limit closed the launcher, and the game with it, when the
     * Mac controller was briefly throttled while backgrounded during play. */
    assert(!heartbeat_stale(base+SECONDS(25),base));
    assert(!heartbeat_stale(base+SECONDS(59),base));
    /* A controller that is really gone is still detected. */
    assert(heartbeat_stale(base+SECONDS(61),base));
    /* A clock step backwards must not count as an expired controller. */
    assert(!heartbeat_stale(base,base+SECONDS(120)));
    puts("Heartbeat policy tests passed (fresh, throttled, gone, clock skew).");
}
