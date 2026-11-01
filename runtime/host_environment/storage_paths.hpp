#pragma once
#include <cstdlib>
#include <filesystem>
#include <stdexcept>
namespace ps2_port {
// Read-only game assets and writable saves may live on different volumes.
inline std::filesystem::path memory_card_root(const std::filesystem::path &disc_directory) {
    if (const char *value = std::getenv("PS2_PORT_SAVE_ROOT")) {
        const std::filesystem::path configured(value);
        if (!configured.is_absolute()) throw std::runtime_error("PS2_PORT_SAVE_ROOT must be an absolute directory");
        return configured.lexically_normal();
    }
    return disc_directory / "mc0";
}
}
