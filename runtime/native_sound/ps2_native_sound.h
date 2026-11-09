// Native IOP sound: a title's sound driver compiled ahead of time to C++ (BozoEngine tools/native_irx) runs on
// BozoEngine's native IOP host and libsd mixer instead of the IOP interpreter and SPU2 model.
// Opt-in per launch with PS2_PORT_NATIVE_SOUND=1, and only for modules whose compiled image was linked in.
#pragma once
#include "ps2x/iop/iop_types.h"
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string_view>

class PS2Runtime;
namespace bozo::native::irx
{
    struct Image;
}

// Called by generated code (static initializer) for each compiled IOP module image linked into the runner.
bool ps2_native_sound_register_image(const bozo::native::irx::Image &image);

class PS2NativeSound
{
  public:
    explicit PS2NativeSound(PS2Runtime &runtime);
    ~PS2NativeSound();
    // True when the opt-in is set and at least one compiled image is linked.
    bool enabled() const;
    // Module load by path: starts a compiled image natively, or accepts libsd (served natively). handled=false
    // leaves the module to the IOP subsystem.
    ps2x::iop::ModuleLoadResult loadModule(std::string_view path);
    bool serves(uint32_t sid) const;
    ps2x::iop::RpcResult handleRpc(uint8_t *rdram, const ps2x::iop::RpcRequest &request);
    // EE -> IOP SIF DMA into a native allocation; false when the address is not native.
    bool writeIopMemory(uint32_t address, const void *source, size_t size);
    // EE cycles elapsed: advances native IOP time and hands the mixed PCM to the audio backend.
    void advanceEeCycles(uint64_t eeCycles);
    // While native sound runs, the SPU2 model's output is not submitted.
    bool ownsAudio() const;

  private:
    struct Impl;
    std::unique_ptr<Impl> m_impl;
};
