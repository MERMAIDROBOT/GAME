#include "surface_leases.h"
#include "ps2gpu/gs_renderer.h"
#include "ps2gpu/surface_api.h"
#include <cassert>
#include <type_traits>
static_assert(std::is_same_v<decltype(&ps2gpu::ZeroCopyWait),void* (*)(std::uint64_t*,std::uint32_t*,std::uint32_t*,int)>);
static_assert(std::is_same_v<decltype(&hwgs_surface_wait),void* (*)(uint64_t*,uint32_t*,uint32_t*,int)>);
int main() {
    struct Slot {std::uint64_t serial; std::shared_ptr<void> surface;};
    auto backing=std::make_shared<int>(1);
    Slot slots[]={{7,backing},{9,backing},{0,backing},{10,{}}};
    assert(ps2gpu::selectSurfaceSlot(slots,7)==&slots[0]);
    assert(ps2gpu::selectSurfaceSlot(slots,0)==&slots[1]);
    assert(!ps2gpu::selectSurfaceSlot(slots,8));
    slots[0].serial=11;
    assert(!ps2gpu::selectSurfaceSlot(slots,7)); // recycled token cannot name a new image
    assert(ps2gpu::selectSurfaceSlot(slots,0)==&slots[0]);
    ps2gpu::SurfaceLeases leases;
    unsigned destroyed=0;
    auto make=[&] {return std::shared_ptr<void>(new int(42),[&](void* p){++destroyed;delete static_cast<int*>(p);});};
    auto surface=make();const auto first=leases.nextSerial();
    assert(!leases.hold(0,surface) && !leases.hold(first+1,surface) && !leases.hold(first,{}));
    assert(leases.hold(first,surface) && leases.hold(first,surface));
    // Renderer reset drops its references; two outstanding window users keep
    // the backing storage alive until both GPU copies have completed.
    surface.reset();assert(destroyed==0 && leases.held(first));
    leases.release(first);assert(destroyed==0 && leases.held(first));
    leases.release(first);assert(destroyed==1 && !leases.held(first));
    leases.release(first);assert(destroyed==1);
    auto replacement=make();const auto second=leases.nextSerial();assert(second>first);
    assert(leases.hold(second,replacement));
    leases.release(first);assert(leases.held(second)); // stale release cannot affect new renderer generation
    auto impostor=std::shared_ptr<void>(replacement.get(),[](void*){});
    assert(!leases.hold(second,impostor)); // same address is insufficient; ownership must agree
    auto other=make();assert(!leases.hold(second,other));
    replacement.reset();leases.release(second);assert(destroyed==2);
    other.reset();assert(destroyed==3);
}
