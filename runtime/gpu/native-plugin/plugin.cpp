// Hardware-only native factory. The existing renderer owns its Vulkan device.
#include "raw_backend.hpp"
namespace {
struct Resources { LocalGS::QueuedOwner owner; };
}
extern "C" __attribute__((visibility("default"))) GSRasterBackend *PS2PortCreateRawGSBackend() {
    auto resources=std::make_shared<Resources>();
    return new LocalGS::RawBackend(resources->owner,resources);
}
