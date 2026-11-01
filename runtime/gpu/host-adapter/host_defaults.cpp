// Claude 2026-10-20: offscreen-replay stand-ins for settings, window and texture pack (all off/default).
#include <cstdlib>
#include <functional>
#include <memory>
#include <string>
#include <vector>
#include "ps2host/run.h"
#include "ps2host/settings.h"
#include "ps2host/texture_pack.h"
namespace ps2gpu { class Gpu; class Display; }
namespace ps2host {
const std::string &SettingsPath() { static const std::string empty; return empty; }
std::string Setting(const std::string &, const std::string &fallback) { return fallback; }
bool SettingOn(const std::string &, bool fallback) { return fallback; }
u32 SupersamplingFactor() { const char *v = std::getenv("HW_REPLAY_SSAA"); return v ? u32(std::atoi(v)) : 1u; }
bool FramePacingDefault() { return false; }
ps2gpu::Display *WindowDisplay() { return nullptr; }
ps2gpu::Gpu *WindowGpu() { return nullptr; }
void SetPictureSource(std::function<bool(std::vector<u32> *, u32 *, u32 *)>) {}
namespace texture_pack {
void SetBlockCompression(bool) {}
std::string Name::Text() const { return std::string(); }
std::size_t Open(const std::string &) { return 0; }
std::size_t Count() { return 0; }
bool Enabled() { return false; }
void SetEnabled(bool) {}
std::uint64_t Generation() { return 0; }
Name NameOf(const gs::Memory &, std::uint64_t, std::uint64_t, std::uint64_t, const std::uint32_t *) { return Name{}; }
std::shared_ptr<const Image> Find(const Name &, bool *pending) { if (pending) *pending = false; return nullptr; }
bool Active() { return false; }
bool Wanted(std::uint32_t) { return false; }
}
}
