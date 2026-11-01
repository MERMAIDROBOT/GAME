// Asset-free contract of the actual factory/queue; mock only the hardware C API.
#include "runtime/gs/gs_backend.h"
#include <cassert>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>
static std::thread::id worker;
static unsigned writes=0,gifs=0;
static std::vector<unsigned char> memory(4*1024*1024);
static void owner() {
    if(worker==std::thread::id{})worker=std::this_thread::get_id();
    assert(worker==std::this_thread::get_id());
}
extern "C" {
int hwgs_start(unsigned,char*,size_t){owner();return 1;}
void hwgs_reset(const void*p,size_t n){owner();assert(n==memory.size());std::memcpy(memory.data(),p,n);}
void hwgs_write(unsigned,unsigned long long){owner();++writes;}
void hwgs_gif(unsigned,const void*,size_t n){owner();assert(n==16);++gifs;}
void hwgs_upload(unsigned long long,unsigned long long,unsigned long long,unsigned long long,const void*,size_t){owner();}
void hwgs_sync(){owner();}
void hwgs_snapshot(void*p){owner();std::memcpy(p,memory.data(),memory.size());}
void hwgs_privileged(unsigned,unsigned long long){owner();}
int hwgs_content_stamp(unsigned*,unsigned*,unsigned*,unsigned long long*){owner();return 0;}
int hwgs_frame(void*,size_t,unsigned*,unsigned*,unsigned*){owner();return 0;}
void hwgs_surface_release(std::uint64_t){}
int hwgs_frame_info(std::uint32_t*,std::uint32_t*,std::uint32_t*){owner();return 0;}
int hwgs_frame_surface(std::uint32_t,std::uint32_t,std::uint32_t,std::uint32_t,std::uint32_t,std::uint32_t,std::uint64_t*){owner();return 0;}
int hwgs_picture_cpu(void*,size_t,unsigned*,unsigned*){owner();return 0;}
size_t hwgs_read_fifo(void*p,size_t n){owner();std::memset(p,0x5a,n);return n;}
GSRasterBackend *PS2PortCreateRawGSBackend();
}
int main() {
    const auto caller=std::this_thread::get_id();
    std::unique_ptr<GSRasterBackend> backend(PS2PortCreateRawGSBackend());
    assert(backend->OwnsRawGS());assert(worker!=caller);
    std::vector<unsigned char> initial(4*1024*1024,0x11);initial.back()=0x22;
    backend->Initialize(initial.data(),initial.size());
    unsigned char packet[16]={};backend->RawGIF(packet,16);backend->RawRegister(0,1);
    std::vector<unsigned char> snapshot;backend->SnapshotVram(snapshot);
    assert(gifs==1&&writes==1);assert(snapshot==initial);
    bool rejected=false;try{backend->RawGIF(packet,15);}catch(const std::invalid_argument&){rejected=true;}assert(rejected);
    rejected=false;try{backend->Present({});}catch(const std::logic_error&){rejected=true;}assert(rejected);
    backend.reset();
}
