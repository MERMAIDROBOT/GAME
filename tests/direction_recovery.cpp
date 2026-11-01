#include "direction_recovery.hpp"
#include <stdexcept>
#include <iostream>
static void check(bool value) { if (!value) throw std::runtime_error("direction recovery contract failed"); }
int main() {
    using ps2port::DirectionRecovery;
    for (unsigned i=0;i<4;++i) {
        DirectionRecovery r; auto d=r.bits[i];
        check(r.apply(d,0)==d);check(r.apply(0,10)==0); // ordinary release
        r.reset();r.apply(d|0x4000,1000);r.apply(d,1010);
        check(r.apply(0,1060)==d);check(r.apply(0,1359)==d);check(r.apply(0,1360)==0);
        r.reset();r.apply(d|0x4000,2000);r.apply(d,2010);r.apply(0,2060);
        check(r.apply(r.opposites[i],2070)==r.opposites[i]);check(r.apply(0,2080)==0);
        r.reset();r.apply(d|0x4000,3000);r.apply(d,3010);r.apply(0,3060);
        check(r.apply(d,3070)==d);check(r.apply(0,3200)==0); // real repress cancels hold
        r.reset();r.apply(d|0x4000,4000);r.apply(d,4010);
        check(r.apply(0,4111)==0); // outside 100 ms link window
    }
    DirectionRecovery r;r.apply(0x4090,0);r.apply(0x90,10);
    check(r.apply(0x80,50)==0x90);check(r.apply(0,100)==0x90);
    check(r.apply(0,350)==0x80);check(r.apply(0,400)==0); // per-axis expiry
    r.apply(0x4080,1000);r.apply(0x80,1010);r.apply(0,1020);
    check(r.apply(0,900)==0); // clock rollback resets
    r.apply(0x4080,1100);r.apply(0x80,1110);r.apply(0,1120);r.reset();check(r.apply(0,1130)==0);
    check(r.apply(0x0100,1200)==0x0100); // L2 never becomes a face-button combo
    check(r.apply(0,-1)==0);
    std::cout << "PASS: four directions, release window, independent expiry, opposite/repress, reset and unchanged L2\n";
}
