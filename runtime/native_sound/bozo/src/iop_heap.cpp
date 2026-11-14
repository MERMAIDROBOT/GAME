// IopHeap from BozoEngine runtime/libs/sif.cpp (MIT, see ../LICENSE).
#include "bozo/libs/sif.h"
namespace bozo::libs
{
    void IopHeap::reset()
    {
        free_.clear();
        used_.clear();
        free_[kStart] = kEnd - kStart;
    }

    uint32_t IopHeap::allocate(uint32_t bytes)
    {
        const uint32_t n = std::max<uint32_t>(16u, (bytes + 15u) & ~15u);
        for (auto it = free_.begin(); it != free_.end(); ++it)
            if (it->second >= n)
            {
                const uint32_t a = it->first, left = it->second - n;
                free_.erase(it);
                if (left)
                    free_[a + n] = left;
                used_[a] = n;
                return a;
            }
        return 0;
    }

    bool IopHeap::release(uint32_t address)
    {
        auto u = used_.find(address);
        if (u == used_.end())
            return false;
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

    uint32_t IopHeap::largestFree() const
    {
        uint32_t largest = 0;
        for (const auto &[start, bytes] : free_)
            largest = std::max(largest, bytes);
        return largest;
    }

    uint32_t IopHeap::totalFree() const
    {
        uint32_t total = 0;
        for (const auto &[start, bytes] : free_)
            total += bytes;
        return total;
    }

    uint32_t IopHeap::blockAt(uint32_t address) const
    {
        auto it = used_.upper_bound(address);
        if (it == used_.begin())
            return 0;
        --it;
        return address < it->first + it->second ? it->first : 0;
    }
}
