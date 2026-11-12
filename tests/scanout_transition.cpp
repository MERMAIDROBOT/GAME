// coversDisplay: a target narrower than the displayed width is held, not stretched (mode-switch zoom).
#include "../runtime/gpu/native-plugin/scanout_latch.hpp"
#include <cstdio>
int main()
{
    using LocalGS::coversDisplay;
    int failures = 0;
    auto expect = [&](bool ok, const char *what) { if (!ok) { std::fprintf(stderr, "FAIL: %s\n", what); ++failures; } };
    expect(coversDisplay(1024, 0, 512, 2), "512-wide game target at 2x covers a 512 display");
    expect(coversDisplay(1280, 0, 640, 2), "640-wide movie target at 2x covers a 640 display");
    expect(!coversDisplay(1024, 0, 640, 2), "512-wide target shown on a 640 display is a mode switch");
    expect(!coversDisplay(1280, 8, 640, 2), "a display offset past the target edge is not covered");
    // A mode switch to a buffer not drawn into since: the last picture is held until it is, at most 20 fields.
    {
        LocalGS::ScanoutLatch latch;
        const LocalGS::ScanoutGeometry game{512, 448, false, 0, 0}, movie{640, 240, true, 0, 0};
        const LocalGS::ScanoutContent menu{0, 8, 0, 100}, stale{0x70, 10, 0, 7}, drawn{0x70, 10, 0, 8};
        latch.accepted(menu, game);
        expect(!latch.hold(menu, game), "no switch: nothing held");
        expect(latch.hold(stale, movie), "first field after the switch is held");
        expect(latch.hold(stale, movie), "a buffer not drawn into since the switch stays held");
        expect(!latch.hold(drawn, movie), "drawn into since the switch: shown");
        latch.accepted(drawn, movie);
        expect(latch.hold(drawn, game), "the same image in another layout is held, as before");
        LocalGS::ScanoutLatch still;
        still.accepted(menu, game);
        int shownAfter = 0;
        for (int field = 1; field <= 40 && !shownAfter; ++field) if (!still.hold(stale, movie)) shownAfter = field;
        expect(shownAfter == 21, "a picture never redrawn appears after 20 fields");
    }
    return failures ? 1 : 0;
}
