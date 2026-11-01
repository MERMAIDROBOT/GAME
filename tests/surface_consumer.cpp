#include "surface_consumer.hpp"
#include "surface_token.hpp"
#include <cassert>
#include <vector>
int main() {
    using namespace ps2port;
    SurfaceConsumer consumer;
    std::vector<std::uint64_t> released;
    auto release = [&](std::uint64_t serial) { released.push_back(serial); };
    assert(consumer.retire(SurfaceCompletion::pending, release));
    bool rejected = false;
    try { consumer.acquired(0); } catch (const std::logic_error&) { rejected = true; }
    assert(rejected && !consumer.busy());
    consumer.acquired(7);
    for (int i = 0; i < 100; ++i) {
        assert(!consumer.retire(SurfaceCompletion::pending, release));
        assert(!consumer.retire(SurfaceCompletion::failed, release));
        assert(consumer.serial() == 7 && released.empty());
    }
    rejected = false;
    try { consumer.acquired(8); } catch (const std::logic_error&) { rejected = true; }
    assert(rejected && consumer.serial() == 7);
    // A successful context drain, rather than a forced timeout, permits reuse.
    assert(consumer.retire(SurfaceCompletion::complete, release));
    assert(!consumer.busy() && released == std::vector<std::uint64_t>{7});
    assert(consumer.retire(SurfaceCompletion::complete, release));
    assert(released.size() == 1);
    consumer.acquired(8);
    assert(consumer.retire(SurfaceCompletion::complete, release));
    assert(released == (std::vector<std::uint64_t>{7,8}));
    const auto bytes = encodeSurfaceToken({0x0102030405060708ULL, 2048, 1792});
    assert(bytes.size()==24 && bytes[8]==8 && bytes[15]==1);
    SurfaceToken parsed{};
    assert(decodeSurfaceToken(bytes, &parsed));
    assert(parsed.serial==0x0102030405060708ULL && parsed.width==2048 && parsed.height==1792);
    assert(!decodeSurfaceToken(bytes, nullptr));
    for (unsigned i=0; i<24; ++i) {
        auto truncated=bytes; truncated.resize(i);
        assert(!decodeSurfaceToken(truncated, &parsed));
    }
    auto bad=bytes; bad.push_back(0); assert(!decodeSurfaceToken(bad,&parsed));
    bad=bytes; bad[0]='X'; assert(!decodeSurfaceToken(bad,&parsed));
    bad=bytes; for (unsigned i=8;i<16;++i) bad[i]=0;
    assert(!decodeSurfaceToken(bad,&parsed));
    assert(parsed.serial==0x0102030405060708ULL); // failed parse leaves caller state intact
    for (const auto invalid : {SurfaceToken{1,0,1}, SurfaceToken{1,1,0}, SurfaceToken{1,16385,1}}) {
        rejected=false;
        try { encodeSurfaceToken(invalid); } catch(const std::invalid_argument&) {rejected=true;}
        assert(rejected);
    }
}
