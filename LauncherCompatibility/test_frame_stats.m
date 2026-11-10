/* Build + run (the dylib is injected like it is into the game):
 *   xcrun clang -dynamiclib -arch x86_64 -arch arm64 -lobjc \
 *     LauncherCompatibility/frame_stats.m -o /tmp/fs.dylib
 *   xcrun clang -fobjc-arc -framework Foundation -framework QuartzCore -framework Metal \
 *     LauncherCompatibility/test_frame_stats.m -o /tmp/tfs
 *   MNM_FRAME_STATS_PATH=/tmp/fs.log DYLD_INSERT_LIBRARIES=/tmp/fs.dylib /tmp/tfs /tmp/fs.log
 * (Build the test with -arch x86_64 and run it directly to check it under Rosetta;
 * don't wrap it in `arch`, which strips DYLD_* variables.)
 * A stand-in "game" asks a CAMetalLayer for 3 s of frames at ~30 FPS, with
 * two deliberate 120 ms hitches; the log must show them. (The final partial second
 * is written at process exit, so this in-process check allows for it.)
 */
#import <Foundation/Foundation.h>
#import <QuartzCore/CAMetalLayer.h>
#import <Metal/Metal.h>
#include <stdio.h>
#include <unistd.h>

int main(int argc, char **argv) {
    @autoreleasepool {
        CAMetalLayer *layer = [CAMetalLayer layer];
        layer.device = MTLCreateSystemDefaultDevice();
        layer.drawableSize = CGSizeMake(64, 64);
        usleep(300000);  /* the hook attaches just after QuartzCore loads; a game renders much later */
        for (int i = 0; i < 95; i++) {
            @autoreleasepool { (void)[layer nextDrawable]; }
            usleep(i == 40 || i == 70 ? 120000 : 33333);
        }
        for (int i = 0; i < 40; i++) { @autoreleasepool { (void)[layer nextDrawable]; } usleep(33333); }  /* closes the last bucket */
        FILE *f = fopen(argv[1], "r"); if (!f) { puts("FAIL: no stats file"); return 1; }
        char line[200]; int lines = 0, frames = 0, over100 = 0; double maxms = 0;
        while (fgets(line, sizeof line, f)) {
            long long t; int fr, o50, o100; double avg, mx;
            if (sscanf(line, "%lld,%d,%lf,%lf,%d,%d", &t, &fr, &avg, &mx, &o50, &o100) == 6) {
                lines++; frames += fr; over100 += o100; if (mx > maxms) maxms = mx;
            }
        }
        fclose(f);
        printf("lines=%d frames=%d max_ms=%.1f over100=%d\n", lines, frames, maxms, over100);
        if (lines < 3 || frames < 95 || over100 != 2 || maxms < 115) { puts("FAIL"); return 1; }
        puts("Frame stats tests passed (per-second lines, frame count, hitches, max frame time).");
    }
    return 0;
}
