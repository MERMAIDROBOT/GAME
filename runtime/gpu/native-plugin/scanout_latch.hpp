#pragma once
#include <cstdint>
namespace LocalGS {
struct ScanoutContent { unsigned base=0,pitch=0,format=0; unsigned long long serial=0;
 bool operator==(const ScanoutContent& x) const {return base==x.base&&pitch==x.pitch&&format==x.format&&serial==x.serial;}
};
struct ScanoutGeometry {unsigned width=0,height=0;bool field=false;unsigned left=0,top=0;
 bool operator==(const ScanoutGeometry& x)const{return width==x.width&&height==x.height&&field==x.field&&left==x.left&&top==x.top;}
};
class ScanoutLatch {
 bool valid=false;ScanoutContent content{};ScanoutGeometry geometry{};
 // Claude 2026-11-12: a display mode switch (game <-> movie) can show a buffer not yet drawn since the switch -
 // the previous screen's last picture read in the new layout, a zoom (Kelly Slater, before its movies and
 // menus). The last picture stays until the shown buffer is drawn into under the new layout, at most
 // kSwitchFields fields (a picture that is never redrawn still appears).
 static constexpr unsigned kSwitchFields=20;
 bool switching=false;ScanoutContent switchContent{};ScanoutGeometry switchGeometry{};unsigned switchFields=0;
public:
 void reset(){valid=false;switching=false;}
 bool hold(const ScanoutContent& c,const ScanoutGeometry& g){
  if(!valid||g==geometry){switching=false;return false;}
  if(c==content)return true;                       // the same image in another layout
  if(!switching||!(switchGeometry==g)){switching=true;switchGeometry=g;switchContent=c;switchFields=0;return true;}
  if(c==switchContent&&++switchFields<kSwitchFields)return true;    // not drawn into since the switch
  return false;
 }
 bool unchanged(const ScanoutContent& c,const ScanoutGeometry& g)const{return valid&&c==content&&g==geometry;}
 void accepted(const ScanoutContent& c,const ScanoutGeometry& g){content=c;geometry=g;valid=true;switching=false;}
};
// A rendered target narrower than the displayed width (from DBX) was drawn for another display mode: during a
// mode switch (game 512 -> movie 640) the old picture would be stretched to the new width, a brief zoom.
// The presenter keeps the previous picture until a target that covers the display exists.
inline bool coversDisplay(unsigned targetWidth,unsigned left,unsigned shownWidth,unsigned scale)
{return static_cast<unsigned long long>(targetWidth)>=(static_cast<unsigned long long>(left)+shownWidth)*scale;}
}
