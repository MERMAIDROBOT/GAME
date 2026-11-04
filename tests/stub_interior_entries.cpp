// Stub-interior entry rule on synthetic functions; no game, recompiler or retail code.
#include "stub_interior_entries.h"
#include <cstdio>

static int failures = 0;
#define CHECK(c) do { if (!(c)) { std::printf("FAIL line %d: %s\n", __LINE__, #c); ++failures; } } while (0)

struct Fn { uint32_t start, end; bool isRecompiled, isStub, isSkipped; };
struct Inst { uint32_t address, opcode, target; };

static Inst jump(uint32_t opcode, uint32_t at, uint32_t to) { return {at, opcode, (to & 0x0FFFFFFFu) >> 2}; }

int main() {
    using ps2recomp::stub_interior::entryTargets;
    // 0x1000..0x1100 is a stubbed library routine whose extent absorbed game code at 0x10c0.
    // 0x2000..0x2100 is skipped. 0x3000.. is translated code that jumps around.
    std::vector<Fn> functions = {{0x1000, 0x1100, false, true, false},
                                 {0x2000, 0x2100, false, false, true},
                                 {0x3000, 0x3100, true, false, false},
                                 {0x4000, 0x4100, true, false, false}};
    std::unordered_map<uint32_t, std::vector<Inst>> decoded;
    decoded[0x3000] = {jump(0x02, 0x3000, 0x10c0),   // tail call into the absorbed code: kept
                       jump(0x03, 0x3008, 0x1000),   // call to the stub itself: the handler serves it
                       jump(0x02, 0x3010, 0x2040),   // jump into a skipped extent: kept
                       jump(0x02, 0x3018, 0x4010),   // into translated code: not this rule's business
                       jump(0x02, 0x3020, 0x1100),   // the stub's end is the next function: not inside
                       {0x3028, 0x04, 0x10c0 >> 2}}; // a branch opcode is not a direct jump
    decoded[0x1000] = {jump(0x02, 0x1004, 0x1080)};  // code inside a stub is not translated: ignored
    const auto targets = entryTargets(functions, decoded);
    CHECK(targets.size() == 2);
    CHECK(targets.count(0x10c0) == 1);
    CHECK(targets.count(0x2040) == 1);

    // Region bits come from the instruction after the jump, as on the EE.
    std::vector<Fn> high = {{0x10001000, 0x10001100, false, true, false}, {0x10003000, 0x10003100, true, false, false}};
    std::unordered_map<uint32_t, std::vector<Inst>> highDecoded;
    highDecoded[0x10003000] = {jump(0x02, 0x10003000, 0x100010c0)};
    CHECK(entryTargets(high, highDecoded).count(0x100010c0) == 1);

    // Nothing stubbed: nothing to keep.
    functions[0].isStub = false;
    functions[1].isSkipped = false;
    CHECK(entryTargets(functions, decoded).empty());

    if (failures == 0)
        std::printf("stub interior entries ok\n");
    return failures == 0 ? 0 : 1;
}
