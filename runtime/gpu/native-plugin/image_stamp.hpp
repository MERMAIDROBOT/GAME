#pragma once
#include <memory>
namespace LocalGS {
// A held immutable image belongs to its first completed field. New capture
// boundaries alone do not require another window texture upload.
template<class Stamp> class ImageStamp {
 std::shared_ptr<const void> pixels; Stamp stamp{}; unsigned width=0,height=0;
public:
 Stamp select(std::shared_ptr<const void> image,unsigned w,unsigned h,Stamp incoming) {
  if(!image || image!=pixels || w!=width || h!=height || incoming.generation!=stamp.generation) {
   pixels=std::move(image);width=w;height=h;stamp=incoming;
  }
  return stamp;
 }
};
}
