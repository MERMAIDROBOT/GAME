// Pixel formats a local -> host (TRXDIR = 1) transfer may read back through the guest FIFO, and their packed
// size: 32-bit colour and depth as 4 bytes, 24-bit colour and depth packed as 3 bytes (GS manual, transmission
// data formats). Both the raw backend's admission and hwgs_read_fifo's cursor use this one table.
#pragma once
#include <cstdint>
namespace LocalGS {
constexpr uint32_t kPsmCT32 = 0x00, kPsmCT24 = 0x01, kPsmZ32 = 0x30, kPsmZ24 = 0x31;
constexpr uint32_t guestFifoBytesPerPixel(uint32_t psm) {
    return (psm == kPsmCT32 || psm == kPsmZ32) ? 4u : (psm == kPsmCT24 || psm == kPsmZ24) ? 3u : 0u;
}
}
