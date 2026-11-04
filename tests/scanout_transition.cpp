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
    return failures ? 1 : 0;
}
