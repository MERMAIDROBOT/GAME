#include "completed_field.hpp"
#include <cassert>
#include <vector>
struct Image {std::vector<unsigned char> rgba;};
struct Capture {Image pixels;std::uint64_t ingressSequence=0,generation=0;};
struct Boundary {std::uint64_t tick=0;};
int main() {
    using Field=ps2port::CompletedField<Boundary,Image,Capture>;
    auto pending=std::make_shared<Field>();pending->ready=false;pending->boundary.tick=27;
    const unsigned char* original=nullptr;
    {std::promise<Capture> p;pending->pending=p.get_future().share();
     Capture c;c.pixels.rgba.assign(2048u*1792u*4u,77);original=c.pixels.rgba.data();
     c.ingressSequence=12;c.generation=3;p.set_value(std::move(c));}
    auto ready=ps2port::resolveField(std::shared_ptr<const Field>(pending));pending.reset();
    assert(ready->boundary.tick==27 && ready->ingressSequence==12 && ready->generation==3);
    for(int i=0;i<20;++i)assert(ready->image().rgba.data()==original && ready->image().rgba.back()==77);
    assert(ps2port::resolveField(ready)==ready);
    Field direct;direct.pixels.rgba={1,2,3,4};assert(direct.image().rgba[3]==4);
    auto invalid=std::make_shared<Field>();invalid->ready=false;
    try {ps2port::resolveField(std::shared_ptr<const Field>(invalid));assert(false);}catch(const std::logic_error&){}
}
