// Rules for runtime/guest_heap_layout.h (explicit SetupHeap ranges above the default heap end).
#include "runtime/guest_heap_layout.h"
#include <cstdio>

static int failures = 0;
static void expect(bool ok, const char *what) { if (!ok) { std::fprintf(stderr, "FAIL: %s\n", what); ++failures; } }

int main()
{
    using GuestHeapLayout::exactLimit;
    constexpr uint32_t ram = 0x02000000u;
    expect(exactLimit(0x01FF8000u, 0x4000u, 0x01FFC000u, ram) == 0x01FFC000u, "small heap under the title stack is exact");
    expect(exactLimit(0x01FF8000u, 0x8000u, 0x01FFC000u, ram) == 0x01FFC000u, "exact heap stops at the main stack");
    expect(exactLimit(0x01FF8000u, 0x8000u, 0u, ram) == ram, "without a placed stack the heap may reach the end of RAM");
    expect(exactLimit(0x00782554u, 0xFFFFFFFFu, 0u, ram) == 0u, "rest-of-RAM requests keep the generic path");
    expect(exactLimit(0x00180010u, 0x1000u, 0u, ram) == 0u, "heaps below the default end keep the generic path");
    expect(exactLimit(0x01FF8000u, 0x10000u, 0u, ram) == 0u, "a heap past the end of RAM is rejected");
    expect(exactLimit(0x01FF8000u, 0u, 0u, ram) == 0u, "size 0 keeps the generic path");
    return failures ? 1 : 0;
}
