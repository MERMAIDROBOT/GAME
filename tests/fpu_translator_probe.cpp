// Asset-free driver for the actual pinned FpuTranslator implementation.
#include "ps2recomp/Translators/fpu_translator.h"
#include "ps2recomp/types.h"
#include "ps2recomp/code_generator.h"
#include <iostream>
#include <cstdlib>
int main(int argc,char**argv){
 ps2recomp::CodeGenerator plumbing;
 ps2recomp::FpuTranslator translator(plumbing);
 for(int i=1;i<argc;++i){uint32_t w=uint32_t(std::strtoul(argv[i],nullptr,16));
  if((w>>26)!=0x11 || ((w>>21)&31)!=16)return 2;
  ps2recomp::Instruction inst{};inst.raw=w;inst.opcode=w>>26;inst.rs=(w>>21)&31;
  inst.rt=(w>>16)&31;inst.rd=(w>>11)&31;inst.sa=(w>>6)&31;inst.function=w&63;
  std::cout<<std::hex<<w<<'\t'<<translator.translate(inst)<<'\n';
 }
}
