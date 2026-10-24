#pragma once
#include <algorithm>
#include <cstdint>
#include <vector>
namespace RuntimeMips {
struct Chain { unsigned levels=1; std::vector<std::uint8_t> pixels; };
// The base remains byte-identical. Additional levels are made once per GPU
// residency upload, never during drawing or for render-to-texture images.
inline Chain make(unsigned width,unsigned height,const std::vector<std::uint8_t>& base) {
 Chain result;result.pixels=base;
 if(!width||!height||width>4096||height>4096||base.size()!=std::size_t(width)*height*4)return result;
 std::size_t source=0;
 while(result.levels<8&&(width>1||height>1)) {
  const unsigned w=std::max(1u,width/2),h=std::max(1u,height/2);
  const std::size_t output=result.pixels.size();result.pixels.resize(output+std::size_t(w)*h*4);
  for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++) {
   const unsigned x0=x*width/w,x1=(x+1)*width/w,y0=y*height/h,y1=(y+1)*height/h,n=(x1-x0)*(y1-y0);
   for(unsigned c=0;c<4;c++) {
    unsigned sum=0;for(unsigned yy=y0;yy<y1;yy++)for(unsigned xx=x0;xx<x1;xx++)sum+=result.pixels[source+(std::size_t(yy)*width+xx)*4+c];
    result.pixels[output+(std::size_t(y)*w+x)*4+c]=std::uint8_t((sum+n/2)/n);
   }
  }
  source=output;width=w;height=h;++result.levels;
 }
 return result;
}
}
