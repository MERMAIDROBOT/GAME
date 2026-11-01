// Test-only dependency plumbing. No instruction translation or formatting here.
// FpuTranslator only uses this object to report unsupported instructions.
#pragma once
#include <stdexcept>
#include <string>
namespace ps2recomp { struct Instruction; class CodeGenerator {
public: std::string emitUnhandledInstruction(const Instruction&,const std::string& why) { throw std::runtime_error(why); }
}; }
