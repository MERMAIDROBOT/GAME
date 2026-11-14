// Qualification of the display geometry supported by the native scanout adapter.
#pragma once
#include "native_registers.hpp"
#include <array>
#include <cstring>
namespace LocalGS {
// PSMCT24 shares PSMCT32 storage (no alpha). DX is the CRT start position (NTSC titles use
// 600-700); DW 2560 is the same 640-pixel span written without the -1. Other geometry stays rejected.
inline bool qualifiedScanout(const std::array<uint64_t,15>& p) {
    ParallelGS::PrivRegisterState priv{};
    for(unsigned i=0;i<15;i++) std::memcpy(&priv.qwords_lo[i*2],&p[i],8);
    const bool interlaced=priv.smode2.INT;
    const bool crt1=(p[0]&3)==1;
    const auto& fb=crt1?priv.dispfb1:priv.dispfb2;
    const auto& display=crt1?priv.display1:priv.display2;
    const unsigned width=fb.FBW*64;
        const bool dual=(p[0]==32871 && p[1]==16640 && (p[2]==1 || p[2]==3) &&
            (p[7]==4096 || p[7]==4208 || p[7]==4320) && p[9]==(p[7]|(1ull<<43)) &&
            p[8]==7874698016924284ull && p[10]==7857105830879870ull && p[13]==0 && p[14]==0);

        const bool identicalMovie=(p[0]==32775 && p[1]==16640 && p[2]==3 &&
            (p[7]==5120 || p[7]==5190 || p[7]==5260) && p[7]==p[9] &&
            p[8]==0x1bf9ff0183227cull && p[8]==p[10] && p[13]==0 && p[14]==0);
        // Claude 2026-10-21: the 640x480 movie mode is also set with SMODE2 FFMD=0 (smode2=1) - seen at boot after the
        // Treyarch logo, three times today, each one fatal.
        const bool movie480=(p[0]==0x66 && p[1]==16640 && (p[2]==1 || p[2]==3) &&
            (p[9]==5120 || p[9]==5200) && p[10]==0x1df9ff0183227cull && p[13]==0 && p[14]==0);
    return !(!dual && !identicalMovie && !movie480 && (((p[0]&3)!=2 && !crt1) || (crt1 && (p[14]!=0 || !interlaced || priv.smode2.FFMD)) || priv.smode1.CMOD!=2 || priv.smode1.LC!=32 || (!interlaced && !priv.smode2.FFMD) ||
           (fb.PSM!=0 && fb.PSM!=1) || (width!=512 && width!=640) || fb.DBX || fb.DBY ||
           display.DX<600 || display.DX>700 || display.DY!=(interlaced?50:25) || (display.DW!=2559 && display.DW!=2560) ||
           display.DH!=(interlaced?447:223) || display.MAGH!=2560/width-1 || display.MAGV));
}
}
