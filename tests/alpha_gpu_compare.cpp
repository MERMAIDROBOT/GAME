#include "ps2gpu/gs_renderer.h"
#include <cassert>
#include <cstdio>
#include <vector>
int main(){
 std::string error;auto gpu=ps2gpu::CreateGpu(&error);if(!gpu){std::fprintf(stderr,"%s\n",error.c_str());return 1;}
 auto r=ps2gpu::CreateGsRenderer(*gpu,&error);assert(r);r->SetScale(2);
 std::vector<std::uint32_t> tex(16,0x8000ffff);tex[0]=0x0000ffff;tex[7]=0x0000ffff;r->UploadTexture(77,tex.data(),4,4);
 unsigned long long hash=14695981039346656037ull;unsigned cases=0;
 for(unsigned textured=0;textured<2;++textured)for(unsigned tcc=0;tcc<2;++tcc)for(unsigned tfx=0;tfx<4;++tfx)for(unsigned test=0;test<8;++test)for(unsigned fail=0;fail<4;++fail){
  ps2gpu::GsDrawState s;s.width=s.height=64;s.fbw=1;s.fbp=16+cases;s.scissor_x1=s.scissor_y1=63;
  s.depth=true;s.depth_write=true;s.zbp=300+cases;s.ztst=2;s.alpha_test=true;s.atst=test;s.aref=test%2?1:127;s.afail=fail;
  s.textured=textured;s.tcc=tcc;s.tfx=static_cast<ps2gpu::TextureFunction>(tfx);s.texture=77;s.tw=s.th=2;s.gouraud=true;
  std::vector<std::uint32_t> base(64*64,0x80604020),depth(64*64,0);
  r->UploadTarget(s.fbp,1,0,base.data(),64,64);r->UploadDepth(s.zbp,64,64,depth.data());
  for(unsigned i=0;i<8;++i){
   auto a=static_cast<std::uint8_t>(i==6?0:128+i);
   auto color=static_cast<std::uint8_t>(50+i*20);float x=3+float(i*3);
   ps2gpu::GsVertex v[3]={{x,3,0x10000000u+i*0x1000000u,color,150,90,a,0,0,1},{60,12,0x10000000u+i*0x1000000u,color,150,90,a,1,0,1},{20,60,0x10000000u+i*0x1000000u,color,150,90,a,0,1,1}};
   r->DrawTriangles(s,v,3);
  }
  for(unsigned y=0;y<4;++y)for(unsigned x=0;x<4;++x) {
   float l=float(x*16),t=float(y*16),right=l+16,bottom=t+16;
   ps2gpu::GsVertex v[6]={{l,t,0x70000000,100,190,130,128,0,0,1},{right,t,0x70000000,100,190,130,128,1,0,1},{l,bottom,0x70000000,100,190,130,128,0,1,1},{right,t,0x70000000,180,120,90,128,1,0,1},{right,bottom,0x70000000,180,120,90,128,1,1,1},{l,bottom,0x70000000,180,120,90,128,0,1,1}};
   r->DrawTriangles(s,v,3);r->DrawTriangles(s,v+3,3);
  }
  std::vector<std::uint32_t> pixels;unsigned w=0,h=0;assert(r->ReadTarget(s.fbp,1,0,&pixels,&w,&h));assert(w&&h&&!pixels.empty());
  for(auto word:pixels)for(unsigned k=0;k<4;++k){hash^=(word>>(k*8))&255;hash*=1099511628211ull;}
  ++cases;
 }
 std::fprintf(stderr,"[alpha gpu counters] %s\n",ps2gpu::RendererCounters().c_str());
 std::printf("%u overlapping alpha/depth cases %016llx\n",cases,hash);
}
