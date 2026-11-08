// Interlaced presentation and host-frame strides (patches/presentation-fidelity.patch, patches/hd-frame-stride.patch).
#include "runtime/gs/gs_field_presentation.h"
#include "runtime/gs/gs_frame_layout.h"

#include <cstdio>

static int failures = 0;
static void expect(bool ok, const char *what)
{
    if (!ok) { std::fprintf(stderr, "FAIL: %s\n", what); ++failures; }
}

int main()
{
    // FFMD=0: the buffer holds both fields' lines; every output line shows its own line (no bob, no halving).
    for (unsigned y = 0; y < 448; ++y)
        if (GSFieldPresentation::sourceLine(y, 448, false) != y) { expect(false, "field mode shows every line"); break; }
    // FFMD=1: a half-height buffer, each line covering two output lines.
    expect(GSFieldPresentation::sourceLine(0, 448, true) == 0 && GSFieldPresentation::sourceLine(1, 448, true) == 0 &&
           GSFieldPresentation::sourceLine(447, 448, true) == 223, "frame mode line-doubles");
    expect(GSFieldPresentation::sourceLine(500, 448, false) == 447, "clamped to the buffer");

    // A GPU renderer's packed frame keeps its own width; the software renderer's padded 640x480 buffer keeps 640.
    expect(gsHostFrameStride(1024, 896, 1024u * 896u * 4u) == 1024u * 4u, "packed 2x frame");
    expect(gsHostFrameStride(1280, 896, 1280u * 896u * 4u) == 1280u * 4u, "packed 2x movie frame");
    expect(gsHostFrameStride(2560, 1792, 2560u * 1792u * 4u) == 2560u * 4u, "packed 4x movie frame");
    expect(gsHostFrameStride(512, 448, 640u * 480u * 4u) == 640u * 4u, "padded software frame");
    // The CPU backend's buffer is 640x512; a 512x448 interlaced title must not be rejected (was a magenta screen).
    expect(gsHostFrameStride(512, 448, 640u * 512u * 4u) == 640u * 4u, "padded CPU-backend 640x512 frame");
    expect(gsHostFrameStride(640, 513, 640u * 512u * 4u) == 0, "taller than the CPU buffer rejected");
    expect(gsHostFrameStride(1024, 896, 640u * 480u * 4u) == 0, "mismatched bytes rejected");
    expect(gsHostFrameStride(0, 448, 0) == 0 && gsHostFrameStride(5000, 10, 5000u * 10u * 4u) == 0, "bounds");

    if (!failures) std::puts("display path: ok");
    return failures ? 1 : 0;
}
