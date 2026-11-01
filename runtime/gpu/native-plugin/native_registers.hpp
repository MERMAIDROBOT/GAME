// Register layout compatibility only; no reference renderer/device is linked.
// SPDX-License-Identifier: LGPL-3.0-or-later
#pragma once
#include "gs_registers.hpp"
namespace ParallelGS {
struct PrivRegisterState
{
	union
	{
		struct
		{
			alignas(16) PMODEBits pmode;
			alignas(16) SMODE1Bits smode1;
			alignas(16) SMODE2Bits smode2;
			alignas(16) DummyBits srfsh;
			alignas(16) DummyBits synch1;
			alignas(16) DummyBits synch2;
			alignas(16) SYNCVBits syncv;
			alignas(16) DISPFBBits dispfb1;
			alignas(16) DISPLAYBits display1;
			alignas(16) DISPFBBits dispfb2;
			alignas(16) DISPLAYBits display2;
			alignas(16) EXTBUFBits extbuf;
			alignas(16) EXTDATABits extdata;
			alignas(16) EXTWRITEBits extwrite;
			alignas(16) BGCOLORBits bgcolor;
		};
		uint64_t qwords_lo[0x200];
	};

	union
	{
		struct
		{
			alignas(16) CSRBits csr;
			alignas(16) IMRBits imr;
			uint64_t pad0_[5];
			alignas(16) BUSDIRBits busdir;
			uint64_t pad1_[7];
			alignas(16) SIGLBLIDBits siglblid;
		};
		uint64_t qwords_hi[0x200];
	};
};

static_assert(sizeof(PrivRegisterState)==8192);
}
