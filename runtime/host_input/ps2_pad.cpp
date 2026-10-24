#include "runtime/ps2_pad.h"
#include "raylib.h"
#include <cstring>
#include <array>
#include <mutex>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <fstream>
#include <cstdio>

namespace
{
    constexpr uint8_t kPadAnalogMarker = 0x73;
    constexpr uint8_t kPadStickCenter = 0x80;

    constexpr uint16_t PAD_LEFT = 0x0080u;
    constexpr uint16_t PAD_DOWN = 0x0040u;
    constexpr uint16_t PAD_RIGHT = 0x0020u;
    constexpr uint16_t PAD_UP = 0x0010u;
    constexpr uint16_t PAD_START = 0x0008u;
    constexpr uint16_t PAD_R3 = 0x0004u;
    constexpr uint16_t PAD_L3 = 0x0002u;
    constexpr uint16_t PAD_SELECT = 0x0001u;
    constexpr uint16_t PAD_SQUARE = 0x8000u;
    constexpr uint16_t PAD_CROSS = 0x4000u;
    constexpr uint16_t PAD_CIRCLE = 0x2000u;
    constexpr uint16_t PAD_TRIANGLE = 0x1000u;
    constexpr uint16_t PAD_R1 = 0x0800u;
    constexpr uint16_t PAD_L1 = 0x0400u;
    constexpr uint16_t PAD_R2 = 0x0200u;
    constexpr uint16_t PAD_L2 = 0x0100u;
    // Window-thread only: retain guide events from the same queue as pad presses.
    bool controlsToggleRequested = false;
}

// Internal window-thread API; consume exactly once after polling host input.
bool ps2PortConsumeControlsToggle()
{
    const bool requested = controlsToggleRequested;
    controlsToggleRequested = false;
    return requested;
}

static bool sampleHostPad(uint8_t *data, size_t size, uint16_t& freshPresses)
{
    if (!data || size < 32)
        return false;

    std::memset(data, 0, 32);
    data[0] = 0x01;
    data[1] = kPadAnalogMarker;
    data[2] = 0xFF;
    data[3] = 0xFF;
    data[4] = data[5] = data[6] = data[7] = kPadStickCenter;

    freshPresses=0;
    uint16_t btns = 0xFFFFu;
    constexpr int kGamepad = 0;
    const bool useGamepad = IsGamepadAvailable(kGamepad);
    static const bool trace = std::getenv("PS2_PORT_PAD_TRACE") != nullptr;
    static bool reportedMode = false;
    if (trace && !reportedMode) {
        std::cerr << "[port-lab host-pad-mode] gamepad_available=" << useGamepad << std::endl;
        reportedMode = true;
    }
    auto clearBit = [&btns](uint16_t mask)
    { btns &= ~mask; };

    if (useGamepad)
    {
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_FACE_UP))
            clearBit(PAD_UP);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_FACE_DOWN))
            clearBit(PAD_DOWN);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_FACE_LEFT))
            clearBit(PAD_LEFT);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_FACE_RIGHT))
            clearBit(PAD_RIGHT);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_FACE_DOWN))
            clearBit(PAD_CROSS);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_FACE_RIGHT))
            clearBit(PAD_CIRCLE);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_FACE_LEFT))
            clearBit(PAD_SQUARE);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_FACE_UP))
            clearBit(PAD_TRIANGLE);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_TRIGGER_1))
            clearBit(PAD_L1);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_TRIGGER_1))
            clearBit(PAD_R1);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_TRIGGER_2))
            clearBit(PAD_L2);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_TRIGGER_2))
            clearBit(PAD_R2);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_MIDDLE_RIGHT))
            clearBit(PAD_START);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_MIDDLE_LEFT))
            clearBit(PAD_SELECT);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_LEFT_THUMB))
            clearBit(PAD_L3);
        if (IsGamepadButtonDown(kGamepad, GAMEPAD_BUTTON_RIGHT_THUMB))
            clearBit(PAD_R3);

        float lx = GetGamepadAxisMovement(kGamepad, GAMEPAD_AXIS_LEFT_X);
        float ly = GetGamepadAxisMovement(kGamepad, GAMEPAD_AXIS_LEFT_Y);
        float rx = GetGamepadAxisMovement(kGamepad, GAMEPAD_AXIS_RIGHT_X);
        float ry = GetGamepadAxisMovement(kGamepad, GAMEPAD_AXIS_RIGHT_Y);
        data[6] = static_cast<uint8_t>(128 + lx * 127);
        data[7] = static_cast<uint8_t>(128 + ly * 127);
        data[4] = static_cast<uint8_t>(128 + rx * 127);
        data[5] = static_cast<uint8_t>(128 + ry * 127);
    }
    else
    {
        if (IsKeyDown(KEY_UP))
            clearBit(PAD_UP);
        if (IsKeyDown(KEY_DOWN))
            clearBit(PAD_DOWN);
        if (IsKeyDown(KEY_LEFT))
            clearBit(PAD_LEFT);
        if (IsKeyDown(KEY_RIGHT))
            clearBit(PAD_RIGHT);
        if (IsKeyDown(KEY_X) || IsKeyDown(KEY_SPACE) || IsMouseButtonDown(MOUSE_BUTTON_LEFT))
            clearBit(PAD_CROSS);
        if (IsKeyDown(KEY_C) || IsKeyDown(KEY_ESCAPE) || IsMouseButtonDown(MOUSE_BUTTON_RIGHT))
            clearBit(PAD_CIRCLE);
        if (IsKeyDown(KEY_Z) || IsKeyDown(KEY_KP_0))
            clearBit(PAD_SQUARE);
        if (IsKeyDown(KEY_V) || IsKeyDown(KEY_KP_1))
            clearBit(PAD_TRIANGLE);
        if (IsKeyDown(KEY_Q))
            clearBit(PAD_L1);
        if (IsKeyDown(KEY_E))
            clearBit(PAD_R1);
        if (IsKeyDown(KEY_LEFT_SHIFT))
            clearBit(PAD_L2);
        if (IsKeyDown(KEY_RIGHT_SHIFT))
            clearBit(PAD_R2);
        if (IsKeyDown(KEY_ENTER))
            clearBit(PAD_START);
        if (IsKeyDown(KEY_TAB))
            clearBit(PAD_SELECT);
    }
    // A down/up pair can occur between slow presentation polls. Raylib's
    // press queue retains that event even when IsKeyDown is already false.
    // Publish it through the existing guest-consumed button latch.
    auto queuedPress=[&](uint16_t mask){clearBit(mask);freshPresses|=mask;};
    for (int key = GetKeyPressed(); key != 0; key = GetKeyPressed()) {
        if (key == KEY_F1) controlsToggleRequested = !controlsToggleRequested;
        if (useGamepad) continue;
        switch (key) {
        case KEY_UP: queuedPress(PAD_UP); break;
        case KEY_DOWN: queuedPress(PAD_DOWN); break;
        case KEY_LEFT: queuedPress(PAD_LEFT); break;
        case KEY_RIGHT: queuedPress(PAD_RIGHT); break;
        case KEY_X: case KEY_SPACE: queuedPress(PAD_CROSS); break;
        case KEY_C: case KEY_ESCAPE: queuedPress(PAD_CIRCLE); break;
        case KEY_Z: case KEY_KP_0: queuedPress(PAD_SQUARE); break;
        case KEY_V: case KEY_KP_1: queuedPress(PAD_TRIANGLE); break;
        case KEY_Q: queuedPress(PAD_L1); break;
        case KEY_E: queuedPress(PAD_R1); break;
        case KEY_LEFT_SHIFT: queuedPress(PAD_L2); break;
        case KEY_RIGHT_SHIFT: queuedPress(PAD_R2); break;
        case KEY_ENTER: queuedPress(PAD_START); break;
        case KEY_TAB: queuedPress(PAD_SELECT); break;
        default: break;
        }
    }

    // WASD drives the left stick only. Publishing it as D-pad too can
    // trigger separate analog and digital navigation paths for one key.
    if (!useGamepad) {
        data[6] = static_cast<uint8_t>(128 + 127 * (int(IsKeyDown(KEY_D)) - int(IsKeyDown(KEY_A))));
        data[7] = static_cast<uint8_t>(128 + 127 * (int(IsKeyDown(KEY_S)) - int(IsKeyDown(KEY_W))));
        data[4] = static_cast<uint8_t>(128 + 127 * (int(IsKeyDown(KEY_L)) - int(IsKeyDown(KEY_J))));
        data[5] = static_cast<uint8_t>(128 + 127 * (int(IsKeyDown(KEY_K)) - int(IsKeyDown(KEY_I))));
    }
    data[2] = static_cast<uint8_t>(btns & 0xFF);
    data[3] = static_cast<uint8_t>(btns >> 8);
    return true;
}

namespace {
    std::mutex hostPadMutex;
    std::array<uint8_t,32> hostPadState = [] {
        std::array<uint8_t,32> state{};
        state[0] = 1; state[1] = 0x73;
        state[2] = state[3] = 255;
        state[4] = state[5] = state[6] = state[7] = 128;
        return state;
    }();
    struct PadEdge {std::array<uint8_t,32> state{};bool pressOnly=false;};
    std::array<PadEdge,16> padEdges{};
    size_t edgeHead=0, edgeCount=0;
    static void queueEdge(const uint8_t* state,bool pressOnly) {
        // Several keys can arrive before one guest poll. Merge an expanding
        // held chord so it reaches the game immediately, while every release
        // remains a barrier (a quick tap/repress must never disappear).
        if(pressOnly && edgeCount) {
            auto& previous=padEdges[(edgeHead+edgeCount-1)%padEdges.size()];
            const uint16_t a=previous.state[2] | (previous.state[3]<<8),b=state[2] | (state[3]<<8);
            if(previous.pressOnly && (b|a)==a) {std::memcpy(previous.state.data(),state,32);return;}
        }
        // Bound input memory if the game stalls for more than sixteen edges.
        // Retain the most recent states; the final release cannot be stranded.
        if(edgeCount==padEdges.size()) { edgeHead=(edgeHead+1)%padEdges.size(); --edgeCount; }
        auto& edge=padEdges[(edgeHead+edgeCount)%padEdges.size()];
        std::memcpy(edge.state.data(),state,32);edge.pressOnly=pressOnly;++edgeCount;
    }
    static void retainEdges(const uint8_t* state,uint16_t before,uint16_t after,uint16_t fresh) {
        // A queued GLFW PRESS while this button was already down proves a
        // real release/repress occurred between host polls (not autorepeat).
        const uint16_t repeat=fresh & uint16_t(~before) & uint16_t(~after);
        if(repeat) {
            std::array<uint8_t,32> release;std::memcpy(release.data(),state,32);
            const uint16_t mask=after|repeat;release[2]=mask;release[3]=mask>>8;queueEdge(release.data(),false);
        }
        if(before!=after || repeat) queueEdge(state,repeat || (uint16_t(after & ~before)==0));
    }
}

// Called on the window thread. Guest reads consume a synchronized snapshot,
// retaining a quick press until the guest has had a chance to observe it.
static void publishPadState(const uint8_t *state,uint16_t freshPresses) {
    std::lock_guard<std::mutex> lock(hostPadMutex);
    const uint16_t before = hostPadState[2] | (hostPadState[3] << 8);
    uint16_t after = state[2] | (state[3] << 8);
    // Optional delayed headless input reproduces a press after the title screen.
    static const auto origin = std::chrono::steady_clock::now();
    static bool reported = false;
    if (std::getenv("PS2_PORT_HEADLESS")) {
        if (const char *delay = std::getenv("PS2_PORT_TEST_EXTRA_START_MS")) {
            const long elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now()-origin).count();
            const long at = std::strtol(delay,nullptr,10);
            if (at > 0 && elapsed >= at && elapsed < at+250) {
                after &= ~PAD_START;
                if (!reported) std::cerr << "[port-lab] delayed synthetic Start press" << std::endl;
                reported = true;
            }
        }
    }
    // Private local probes may provide milliseconds:button-mask pairs.
    // Pulses are headless only and never affect normal keyboard input.
    if (std::getenv("PS2_PORT_HEADLESS")) {
        if (const char *script = std::getenv("PS2_PORT_TEST_PAD_SCRIPT")) {
            const long elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now()-origin).count();
            const char *cursor = script;
            for (unsigned n = 0; n < 64u && *cursor; ++n) {
                char *end = nullptr;
                const long at = std::strtol(cursor, &end, 10);
                if (end == cursor || *end != ':') break;
                cursor = end + 1;
                const unsigned long mask = std::strtoul(cursor, &end, 0);
                if (end == cursor) break;
                if (at >= 0 && elapsed >= at && elapsed < at+250)
                    after &= ~static_cast<uint16_t>(mask);
                cursor = *end == ',' ? end + 1 : end;
            }
        }
    }
    // Headless local scenario commands: monotonically increasing sequence,
    // buttons, bounded hold duration, left-stick X/Y. Malformed/stale commands
    // are ignored. Normal keyboard input never reads this file.
    if (std::getenv("PS2_PORT_HEADLESS")) {
        if (const char *path = std::getenv("PS2_PORT_PAD_COMMAND")) {
            static uint64_t sequence = 0;
            static uint16_t mask = 0;
            static uint8_t lx = 128, ly = 128;
            static auto until = origin;
            std::ifstream input(path);
            uint64_t next = 0; unsigned buttons = 0, hold = 0, x = 128, y = 128;
            if ((input >> next >> buttons >> hold >> x >> y) && next > sequence &&
                buttons <= 65535u && hold <= 10000u && hold > 0u && x <= 255u && y <= 255u) {
                sequence = next; mask = static_cast<uint16_t>(buttons);
                lx = static_cast<uint8_t>(x); ly = static_cast<uint8_t>(y);
                until = std::chrono::steady_clock::now() + std::chrono::milliseconds(hold);
                // One stdio operation keeps the ACK intact when renderer threads
                // write diagnostics concurrently. A split ACK stalls scenario input.
                std::fprintf(stderr, "[port-lab pad-command] seq=%llu mask=%u hold_ms=%u lx=%u ly=%u\n",
                             static_cast<unsigned long long>(sequence),
                             static_cast<unsigned>(mask), hold, x, y);
            }
            if (std::chrono::steady_clock::now() < until) {
                after &= ~mask;
            } else { mask = 0; lx = ly = 128; }
            
            std::memcpy(hostPadState.data(), state, hostPadState.size());
            hostPadState[2] = after & 255u; hostPadState[3] = after >> 8;
            hostPadState[6] = lx; hostPadState[7] = ly;
            retainEdges(hostPadState.data(),before,after,freshPresses);
            return;
        }
    }
    if (std::getenv("PS2_PORT_PAD_TRACE") && before != after) {
        static unsigned changes = 0;
        if (changes++ < 256u) {
            const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
            std::cerr << "[port-lab host-pad] ns=" << ns << " mask=" << static_cast<uint16_t>(~after) << std::endl;
        }
    }
    
    std::memcpy(hostPadState.data(), state, hostPadState.size());
    hostPadState[2] = after & 255u; hostPadState[3] = after >> 8;
    retainEdges(hostPadState.data(),before,after,freshPresses);
}

void ps2PortPublishPadState(const uint8_t* state){publishPadState(state,0);}

void ps2PortPollHostInput() {
    uint8_t state[32];
    uint16_t freshPresses=0;
    if (sampleHostPad(state, sizeof(state),freshPresses)) publishPadState(state,freshPresses);
}

bool PSPadBackend::readState(int port, int slot, uint8_t *data, size_t size) {
    if (!data || size < 32u || port != 0 || slot != 0) return false;
    std::lock_guard<std::mutex> lock(hostPadMutex);
    std::memcpy(data, hostPadState.data(), hostPadState.size());
    if(edgeCount) {
        std::memcpy(data,padEdges[edgeHead].state.data(),32);
        edgeHead=(edgeHead+1)%padEdges.size();--edgeCount;
    }
    const uint16_t buttons = data[2] | (data[3]<<8);
    if (std::getenv("PS2_PORT_PAD_TRACE")) {
        static uint16_t previous = 0xffffu;
        static unsigned changes = 0;
        if (buttons != previous && changes++ < 256u) {
            const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
            std::cerr << "[port-lab guest-pad] ns=" << ns << " mask=" << static_cast<uint16_t>(~buttons)
                      << " queued=" << edgeCount << std::endl;
        }
        previous = buttons;
    }
    
    return true;
}
