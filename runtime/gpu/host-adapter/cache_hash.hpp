// Local, run-only texture-cache hash carried from the qualified adapter.
// This is not XXH3 and must not be used for external texture-pack identities.
#pragma once
#include <cstdint>
#include <cstring>
#include <cstddef>
static inline uint64_t RuntimeCacheHash64(const void *data, size_t len)
{
    const uint8_t *p = static_cast<const uint8_t *>(data);
    uint64_t h = 0x9E3779B97F4A7C15ull ^ (len * 0xC2B2AE3D27D4EB4Full);
    size_t i = 0;
    for (; i + 8 <= len; i += 8)
    {
        uint64_t k; std::memcpy(&k, p + i, 8);
        k *= 0x87C37B91114253D5ull; k = (k << 31) | (k >> 33); k *= 0x4CF5AD432745937Full;
        h ^= k; h = ((h << 27) | (h >> 37)) * 5 + 0x52DCE729;
    }
    uint64_t t = 0; if (len != i) std::memcpy(&t, p + i, len - i); h ^= t * 0x87C37B91114253D5ull;
    h ^= h >> 33; h *= 0xFF51AFD7ED558CCDull; h ^= h >> 33; h *= 0xC4CEB9FE1A85EC53ull; h ^= h >> 33;
    return h;
}
