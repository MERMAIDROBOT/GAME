#pragma once
// Direct jump targets that land inside a function the recompiler replaces with a runtime handler
// (a stub) or skips. Without symbols a function's extent runs to the next discovered start, so a
// library routine can absorb game code placed after it that is only reached by `j` (a tail call).
// The handler implements the routine alone; a jump into the rest of its extent must reach
// translated guest code, so these targets are returned as guest entry points.
#include <algorithm>
#include <cstdint>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace ps2recomp::stub_interior {

constexpr uint32_t kOpcodeJ = 0x02;
constexpr uint32_t kOpcodeJal = 0x03;

inline uint32_t absoluteJumpTarget(uint32_t address, uint32_t index) {
    return ((address + 4u) & 0xF0000000u) | (index << 2);
}

// Targets of J/JAL in translated functions that fall strictly inside a stubbed or skipped extent.
template <class FunctionT, class InstructionT>
std::unordered_set<uint32_t> entryTargets(const std::vector<FunctionT> &functions,
                                          const std::unordered_map<uint32_t, std::vector<InstructionT>> &decoded) {
    std::vector<std::pair<uint32_t, uint32_t>> replaced;
    for (const auto &function : functions) {
        if ((function.isStub || function.isSkipped) && function.end > function.start)
            replaced.emplace_back(function.start, function.end);
    }
    std::sort(replaced.begin(), replaced.end());

    std::unordered_set<uint32_t> targets;
    if (replaced.empty())
        return targets;
    for (const auto &function : functions) {
        if (!function.isRecompiled || function.isStub || function.isSkipped)
            continue;
        const auto decodedIt = decoded.find(function.start);
        if (decodedIt == decoded.end())
            continue;
        for (const auto &inst : decodedIt->second) {
            if (inst.opcode != kOpcodeJ && inst.opcode != kOpcodeJal)
                continue;
            const uint32_t target = absoluteJumpTarget(inst.address, inst.target);
            auto it = std::upper_bound(replaced.begin(), replaced.end(), std::make_pair(target, UINT32_MAX));
            if (it == replaced.begin())
                continue;
            --it;
            if (target > it->first && target < it->second && (target & 3u) == 0u)
                targets.insert(target);
        }
    }
    return targets;
}

}  // namespace ps2recomp::stub_interior
