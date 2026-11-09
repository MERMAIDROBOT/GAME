// Native IOP sound bridge (see ps2_native_sound.h). BozoEngine's host (MIT, runtime/native_sound/bozo) runs the
// compiled driver; this file connects it to the runtime's RPC, SIF command, SIF DMA, clock and audio paths.
#include "ps2_native_sound.h"
#include "Stubs/SIF.h"
#include "bozo/native/compiled_libraries.h"
#include "ps2_runtime.h"
#include "runtime/ps2_audio.h"
#include <algorithm>
#include <array>
#include <cctype>
#include <charconv>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace
{
    std::vector<bozo::native::irx::Image> &registry()
    {
        static std::vector<bozo::native::irx::Image> images;
        return images;
    }

    // "cdrom0:\DATA\MODULES\SNDDRV.IRX;1" -> "SNDDRV.IRX"
    std::string moduleName(std::string_view path)
    {
        const auto slash = path.find_last_of("/\\:");
        std::string name(slash == std::string_view::npos ? path : path.substr(slash + 1));
        if (const auto version = name.find(';'); version != std::string::npos) name.resize(version);
        for (char &c : name) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        return name;
    }

    class RdramGuest final : public bozo::libs::Guest
    {
      public:
        explicit RdramGuest(uint8_t *rdram) : m_rdram(rdram) {}
        const uint8_t *in(uint32_t address, uint32_t bytes) const override { return at(address, bytes); }
        void write(uint32_t address, const uint8_t *data, uint32_t bytes) override
        {
            if (bytes) std::memcpy(at(address, bytes), data, bytes);
        }

      private:
        uint8_t *at(uint32_t address, uint32_t bytes) const
        {
            const uint32_t physical = address & 0x1FFFFFFFu;
            if (!m_rdram || physical >= PS2_RAM_SIZE || bytes > PS2_RAM_SIZE - physical)
                throw std::runtime_error("native sound guest access outside EE RAM");
            return m_rdram + physical;
        }
        uint8_t *m_rdram;
    };

    // Native IOP heap: well above the IOP model's module and allocation area, so routing by address is unambiguous.
    constexpr uint32_t kNativeHeapStart = 0x140000u;
    constexpr int32_t kNativeModuleIdBase = 0x1000;

    struct StaticDmaWindow
    {
        uint64_t imageHash = 0;
        uint32_t offset = 0;
        uint32_t bytes = 0;
    };

    // A local profile may admit a writable range verified against its original IRX.
    // The offset is relative to the linked image and pinned to its identity.
    std::optional<StaticDmaWindow> configuredDmaWindow()
    {
        const char *value = std::getenv("PS2_PORT_NATIVE_SOUND_DMA");
        if (!value) return std::nullopt;

        const std::string_view spec(value);
        const auto offsetEnd = spec.find(':', 17);
        if (spec.size() < 20 || spec[16] != ':' || offsetEnd == std::string_view::npos)
            return std::nullopt;

        const auto readHex = [](std::string_view digits, auto &out) {
            if (digits.empty()) return false;
            const auto [end, error] = std::from_chars(digits.data(), digits.data() + digits.size(), out, 16);
            return error == std::errc{} && end == digits.data() + digits.size();
        };
        StaticDmaWindow window;
        if (!readHex(spec.substr(0, 16), window.imageHash) ||
            !readHex(spec.substr(17, offsetEnd - 17), window.offset) ||
            !readHex(spec.substr(offsetEnd + 1), window.bytes) || !window.bytes)
            return std::nullopt;
        return window;
    }
}

bool ps2_native_sound_register_image(const bozo::native::irx::Image &image)
{
    registry().push_back(image);
    return true;
}

struct PS2NativeSound::Impl
{
    PS2Runtime &runtime;
    bozo::libs::Libraries libraries;
    std::unique_ptr<bozo::native::irx::CompiledLibraries> host;
    std::map<std::string, int32_t> started;
    uint64_t eeCycle = 0;
    int32_t nextModuleId = kNativeModuleIdBase;
    bool optIn = false;
    bool failed = false;
    std::optional<StaticDmaWindow> staticDma;
    std::optional<uint32_t> staticDmaAddress;

    void activateStaticDma(const bozo::native::irx::Image &image)
    {
        if (!staticDma || staticDma->imageHash != image.hash ||
            staticDma->offset > image.bytes.size() ||
            staticDma->bytes > image.bytes.size() - staticDma->offset)
            return;
        const uint64_t address = uint64_t(image.base) + staticDma->offset;
        if (address + staticDma->bytes > kNativeHeapStart)
            return;
        staticDmaAddress = static_cast<uint32_t>(address);
        std::cerr << "[native-sound] static DMA buffer enabled for " << image.name
                  << " (" << staticDma->bytes << " bytes)" << std::endl;
    }

    bool ownsStaticDma(uint32_t address, size_t bytes) const
    {
        return staticDmaAddress && address >= *staticDmaAddress &&
               uint64_t(address) + bytes <= uint64_t(*staticDmaAddress) + staticDma->bytes;
    }

    // A native host error is reported once; the driver then stops advancing (silence) instead of ending the game.
    void fail(const char *what)
    {
        if (!failed) std::cerr << "[native-sound] stopped: " << what << std::endl;
        failed = true;
    }
    std::vector<int16_t> pcm = std::vector<int16_t>(2 * 4096);

    explicit Impl(PS2Runtime &rt) : runtime(rt)
    {
        const char *flag = std::getenv("PS2_PORT_NATIVE_SOUND");
        optIn = flag && flag[0] == '1';
        if (optIn) staticDma = configuredDmaWindow();
    }

    uint8_t *rdram() { return runtime.memory().getRDRAM(); }

    bozo::native::irx::CompiledLibraries &ensureHost()
    {
        if (host) return *host;
        libraries.clock = [this](bozo::libs::Guest &) { return eeCycle; };
        libraries.sif.receiveCommand = [this](uint32_t command, std::span<const uint8_t> data, uint32_t destination,
                                              std::span<const uint8_t> extra) -> uint32_t
        {
            if (destination && !extra.empty()) RdramGuest(rdram()).write(destination, extra.data(), uint32_t(extra.size()));
            return ps2_stubs::dispatchSifCommand(rdram(), &runtime, command, data.data(), data.size()) ? 1u : 0u;
        };
        host = std::make_unique<bozo::native::irx::CompiledLibraries>(libraries, kNativeHeapStart);
        host->useReadyAudioCompletions();
        for (const auto &image : registry()) host->addImage(image);
        return *host;
    }
};

PS2NativeSound::PS2NativeSound(PS2Runtime &runtime) : m_impl(std::make_unique<Impl>(runtime)) {}
PS2NativeSound::~PS2NativeSound() = default;

bool PS2NativeSound::enabled() const { return m_impl->optIn && !registry().empty(); }

ps2x::iop::ModuleLoadResult PS2NativeSound::loadModule(std::string_view path)
{
    ps2x::iop::ModuleLoadResult result{};
    if (!enabled()) return result;
    const std::string name = moduleName(path);
    auto &p = *m_impl;
    if (auto found = p.started.find(name); found != p.started.end())
    {
        result.handled = true;
        result.moduleId = found->second;
        result.startResult = 0;
        return result;
    }
    const auto image = std::find_if(registry().begin(), registry().end(),
                                    [&](const auto &i) { return moduleName(i.name) == name; });
    const bool libsdServedNatively =
        name == "LIBSD.IRX" && std::any_of(registry().begin(), registry().end(), [](const auto &i) {
            return std::any_of(i.imports.begin(), i.imports.end(), [](const auto &imp) { return imp.library == "libsd"; });
        });
    if (image == registry().end() && !libsdServedNatively) return result;
    if (image != registry().end())
    {
        const int32_t started = p.ensureHost().startImage(image->name);
        p.activateStaticDma(*image);
        std::cerr << "[native-sound] started " << image->name << " natively (" << started << ")" << std::endl;
    }
    else
        std::cerr << "[native-sound] LIBSD.IRX served by the native libsd" << std::endl;
    result.handled = true;
    result.moduleId = p.nextModuleId++;
    result.startResult = 0;
    p.started.emplace(name, result.moduleId);
    return result;
}

bool PS2NativeSound::serves(uint32_t sid) const
{
    return m_impl->host && m_impl->libraries.sif.servers.count(sid) != 0;
}

ps2x::iop::RpcResult PS2NativeSound::handleRpc(uint8_t *rdram, const ps2x::iop::RpcRequest &request)
{
    ps2x::iop::RpcResult result{};
    auto &p = *m_impl;
    const auto server = p.libraries.sif.servers.find(request.sid);
    if (server == p.libraries.sif.servers.end()) return result;
    RdramGuest guest(rdram);
    try
    {
        server->second(p.libraries, guest,
                       {request.function, request.send.address, request.send.size, request.receive.address,
                        request.receive.size});
    }
    catch (const std::exception &error)
    {
        p.fail(error.what());
    }
    result.handled = true;
    result.resultAddress = request.receive.address;
    result.signalCompletion = true;
    result.signalNowaitCompletion = true;
    result.serverDispatchPolicy = ps2x::iop::ServerDispatchPolicy::Suppress;
    return result;
}

bool PS2NativeSound::writeIopMemory(uint32_t address, const void *source, size_t size)
{
    auto &p = *m_impl;
    if (!p.host || !size || size > kNativeHeapStart) return false;
    if (!p.ownsStaticDma(address, size))
    {
        try
        {
            (void)p.host->allocatedBuffer(address, uint32_t(size));
        }
        catch (const std::runtime_error &)
        {
            return false;
        }
    }
    p.host->writeBuffer(address, static_cast<const uint8_t *>(source), uint32_t(size));
    return true;
}

void PS2NativeSound::advanceEeCycles(uint64_t eeCycles)
{
    auto &p = *m_impl;
    p.eeCycle += eeCycles;
    if (!p.host) return;
    if (p.failed) return;
    try
    {
        auto *mixer = p.host->advanceTo(p.eeCycle);
        if (!mixer) return;
        while (true)
        {
            const size_t frames = mixer->take(p.pcm.data(), p.pcm.size() / 2);
            if (!frames) break;
            p.runtime.audioBackend().submitPcm(std::span<const int16_t>(p.pcm.data(), frames * 2));
        }
    }
    catch (const std::exception &error)
    {
        p.fail(error.what());
    }
}

bool PS2NativeSound::ownsAudio() const { return m_impl->host != nullptr && !m_impl->failed; }
