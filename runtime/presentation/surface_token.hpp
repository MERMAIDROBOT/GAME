#pragma once
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

namespace ps2port {
struct SurfaceToken {
    std::uint64_t serial;
    std::uint32_t width, height;
};
// Fixed little-endian wire format, compatible with recovered macOS tokens.
// Never reinterpret a pixel buffer as a native structure (alignment/padding).
inline bool validSurfaceToken(const SurfaceToken& token) {
    return token.serial && token.width && token.height &&
           token.width <= 16384 && token.height <= 16384;
}
inline std::vector<std::uint8_t> encodeSurfaceToken(const SurfaceToken& token) {
    if (!validSurfaceToken(token)) throw std::invalid_argument("Invalid surface token");
    std::vector<std::uint8_t> bytes(24);
    std::memcpy(bytes.data(), "PS2ZCPY1", 8);
    for (unsigned i=0; i<8; ++i) bytes[8+i] = std::uint8_t(token.serial >> (8*i));
    for (unsigned i=0; i<4; ++i) {
        bytes[16+i] = std::uint8_t(token.width >> (8*i));
        bytes[20+i] = std::uint8_t(token.height >> (8*i));
    }
    return bytes;
}
inline bool decodeSurfaceToken(const std::vector<std::uint8_t>& bytes, SurfaceToken* output) {
    if (!output || bytes.size()!=24 || std::memcmp(bytes.data(), "PS2ZCPY1", 8)) return false;
    SurfaceToken token{};
    for (unsigned i=0; i<8; ++i) token.serial |= std::uint64_t(bytes[8+i]) << (8*i);
    for (unsigned i=0; i<4; ++i) {
        token.width |= std::uint32_t(bytes[16+i]) << (8*i);
        token.height |= std::uint32_t(bytes[20+i]) << (8*i);
    }
    if (!validSurfaceToken(token)) return false;
    *output = token;
    return true;
}
}
