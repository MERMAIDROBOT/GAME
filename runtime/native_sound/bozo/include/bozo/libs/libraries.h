// Adapter for BozoEngine's native IOP host inside the PS2 Port Lab runtime.
// Replaces BozoEngine's full EE library layer with the few members compiled_libraries.cpp uses:
// the SIF state, a clock, guest memory access and a print switch. Hooks are filled by the runtime bridge.
// Heap is copied from BozoEngine runtime/libs/libraries.cpp (MIT, see ../../../LICENSE).
#pragma once
#include "bozo/libs/sif.h"
#include <cstdint>
#include <functional>
#include <iterator>
#include <map>
#include <string>
#include <unordered_map>

namespace bozo::ee
{
    // The bulk-copy path (sifman 7/32) needs attached EE data; the bridge does not attach it, so dispatcher()
    // returns nullptr and such a call fails explicitly. These declarations only satisfy the compiler.
    struct EeMemoryView
    {
        uint8_t *hostPointer(uint32_t, uint32_t) { return nullptr; }
        uint8_t *ram() { return nullptr; }
    };
    struct EeRuntimeView
    {
        EeMemoryView memory;
    };
    class Dispatcher
    {
      public:
        EeRuntimeView &runtime() { return runtime_; }

      private:
        EeRuntimeView runtime_;
    };
}

namespace bozo::libs
{
    constexpr uint32_t kSectorBytes = 2048;

    // Guest (EE) memory as the native RPC servers read requests and write replies.
    class Guest
    {
      public:
        virtual ~Guest() = default;
        virtual const uint8_t *in(uint32_t address, uint32_t bytes) const = 0;
        virtual void write(uint32_t address, const uint8_t *data, uint32_t bytes) = 0;
    };

    // Native disc files are not enabled by the bridge (useNativeDiscFiles is never called).
    struct DiscImage
    {
        bool isOpen() const { return false; }
        bool open(const std::string &, std::string &error)
        {
            error = "native disc files are not enabled in this runtime";
            return false;
        }
        uint64_t sectors() const { return 0; }
        bool read(uint64_t, uint32_t, uint8_t *) { return false; }
        bool findFile(const std::string &, uint32_t &, uint32_t &) { return false; }
    };

    struct CdState
    {
        struct File { uint32_t sector = 0, bytes = 0, position = 0; };
        std::map<int32_t, File> files;
        int32_t nextFile = 3;
        DiscImage disc;
        std::string imagePath;
    };

    class Heap
    {
      public:
        void reset(uint32_t start, uint32_t end)
        {
            free_.clear();
            used_.clear();
            end_ = end;
            if (end > start) free_[start] = end - start;
        }
        bool ready() const { return end_ != 0; }
        uint32_t allocate(uint32_t bytes)
        {
            if (bytes > UINT32_MAX - 15u) return 0;
            uint32_t n = (bytes + 15u) & ~15u;
            if (n == 0) n = 16;
            for (auto it = free_.begin(); it != free_.end(); ++it)
                if (it->second >= n)
                {
                    const uint32_t a = it->first, left = it->second - n;
                    free_.erase(it);
                    if (left) free_[a + n] = left;
                    used_[a] = n;
                    return a;
                }
            return 0;
        }
        bool contains(uint32_t address, uint32_t bytes) const
        {
            for (const auto &[start, size] : used_)
                if (address >= start && uint64_t(address) - start <= size &&
                    bytes <= uint64_t(size) - (uint64_t(address) - start))
                    return true;
            return false;
        }
        bool release(uint32_t address)
        {
            auto u = used_.find(address);
            if (u == used_.end()) return false;
            uint32_t a = address, n = u->second;
            used_.erase(u);
            auto next = free_.lower_bound(a);
            if (next != free_.end() && next->first == a + n)
            {
                n += next->second;
                next = free_.erase(next);
            }
            if (next != free_.begin())
            {
                auto prev = std::prev(next);
                if (prev->first + prev->second == a)
                {
                    prev->second += n;
                    return true;
                }
            }
            free_[a] = n;
            return true;
        }
        size_t blocks() const { return used_.size(); }

      private:
        std::map<uint32_t, uint32_t> free_;
        std::unordered_map<uint32_t, uint32_t> used_;
        uint32_t end_ = 0;
    };

    class Libraries
    {
      public:
        SifState sif;
        CdState cd;
        bool printOutput = false;
        // The EE cycle of the call being served; the bridge installs it.
        std::function<uint64_t(Guest &)> clock;
        uint64_t now(Guest &guest) { return clock ? clock(guest) : 0; }
        ee::Dispatcher *dispatcher() { return nullptr; }
    };
}
