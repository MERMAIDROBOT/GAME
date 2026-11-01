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
public:
 void reset(){valid=false;}
 bool hold(const ScanoutContent& c,const ScanoutGeometry& g)const{return valid&&c==content&&!(g==geometry);}
 bool unchanged(const ScanoutContent& c,const ScanoutGeometry& g)const{return valid&&c==content&&g==geometry;}
 void accepted(const ScanoutContent& c,const ScanoutGeometry& g){content=c;geometry=g;valid=true;}
};
}
