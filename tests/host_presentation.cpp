// Synthetic blocking renderer, no retail content or translated functions.
#include "runtime/gs/gs_frontend.h"
#include "runtime/gs/gs_threaded_backend.h"
#include "runtime/ps2_memory.h"
#include <atomic>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <future>
using namespace std::chrono_literals;
struct Gate { std::mutex mutex; std::condition_variable cv; unsigned entered=0,allowed=0; bool fail=false; std::vector<int> rounding; };
class BlockedBackend:public GSRasterBackend {
 std::shared_ptr<Gate> g;
public:
 explicit BlockedBackend(std::shared_ptr<Gate> gate):g(std::move(gate)){}
 void Initialize(uint8_t*,uint32_t)override{} void Reset()override{}
 void Submit(const GSPrimitiveBatch&)override{} void LoadClut(const GSTex0Reg&,const GSTexClutReg&)override{}
 void BeginTransfer(const GSTransferCommand&)override{} void UploadImage(const uint8_t*,uint32_t)override{}
 void Flush()override{} void TextureFlush()override{} void Sync(GSSyncReason)override{}
 PresentationFrame Present(const GSPresentationRequest&)override {
  std::unique_lock<std::mutex> l(g->mutex);unsigned n=++g->entered;g->rounding.push_back(std::fegetround());g->cv.notify_all();
  g->cv.wait(l,[&]{return g->allowed>=n;});if(g->fail)throw std::runtime_error("synthetic presentation failure");
  PresentationFrame f;f.width=f.height=2;f.pixels.assign(640*480*4,uint8_t(n));return f;
 }
 bool ClearFramebuffer(const GSContext&,uint32_t)override{return true;} uint32_t ConsumeLocalToHostBytes(uint8_t*,uint32_t)override{return 0;}
 uint32_t ReadVram(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t)const override{return 0;}
 void WriteVram(uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t)override{}
 void SnapshotVram(std::vector<uint8_t>&)const override{} GSTransferSnapshot GetTransferSnapshot()const override{return {};}
};
static void waitEntered(std::shared_ptr<Gate> g,unsigned n){std::unique_lock<std::mutex>l(g->mutex);if(!g->cv.wait_for(l,2s,[&]{return g->entered>=n;}))throw std::runtime_error("presentation did not start");}
static void release(std::shared_ptr<Gate> g,unsigned n){{std::lock_guard<std::mutex>l(g->mutex);g->allowed=n;}g->cv.notify_all();}
static void check(bool good,const char*why){if(!good)throw std::runtime_error(why);}
static void testFieldBoundary(){
 GSRegisters regs{};auto gate=std::make_shared<Gate>();GS gs;
 gs.setRasterBackend(std::make_unique<BlockedBackend>(gate));gs.init(nullptr,0,&regs);
 const std::thread::id eeOwner=std::this_thread::get_id();
 std::array<uint64_t,15> before{},after{};for(unsigned i=0;i<15;i++){before[i]=0x100000000ull+i;after[i]=0x200000000ull+i;}
 static_assert(offsetof(GSRegisters,csr)==15*sizeof(uint64_t));
 std::memcpy(&regs,before.data(),sizeof(before));
 std::promise<GSFieldBoundary> first;std::promise<void> consuming,release;
 auto released=release.get_future().share();GSFieldBoundary second;unsigned calls=0;
 gs.setFieldBoundaryHandler([&](GSFieldBoundary field){
  check(std::this_thread::get_id()==eeOwner,"field capture moved off EE owner");
  if(++calls==1)first.set_value(std::move(field));else second=std::move(field);
 });
 auto consumer=std::async(std::launch::async,[&]{auto owned=first.get_future().get();consuming.set_value();released.wait();return owned;});
 gs.captureFieldBoundary(101);consuming.get_future().get();
 std::memcpy(&regs,after.data(),sizeof(after));gs.captureFieldBoundary(102);
 release.set_value();auto old=consumer.get();
 check(old.privileged==before&&old.tick==101&&old.phase==1,"delayed field adopted later guest display state");
 check(second.privileged==after&&second.tick==102&&second.phase==0,"next field metadata/parity wrong");
 gs.setFieldBoundaryHandler([](GSFieldBoundary){throw std::runtime_error("field failure");});
 bool failed=false;try{gs.captureFieldBoundary(103);}catch(const std::runtime_error&e){failed=std::string(e.what())=="field failure";}
 check(failed,"field callback failure was hidden");gs.setFieldBoundaryHandler({});gs.captureFieldBoundary(104);
 std::cout<<"PASS: EE-owned15-register field snapshots, delayed-consumer immutability, parity and failure propagation\n";
}
class RawSeamBackend final:public BlockedBackend {
public:
 unsigned gifs=0,registers=0,uploads=0,fields=0,presents=0,transfers=0;
 std::vector<uint8_t> lastPacket;GSFieldBoundary field;bool fail=false;
 RawSeamBackend():BlockedBackend(std::make_shared<Gate>()){}
 bool OwnsRawGS()const override{return true;}
 void RawGIF(const uint8_t*p,uint32_t n)override{if(fail)throw std::runtime_error("raw ingress failure");++gifs;lastPacket.assign(p,p+n);}
 void RawRegister(uint8_t a,uint64_t v)override{check(a==1&&v==0x1234,"direct ingress changed");++registers;}
 void RawUpload(uint64_t,uint64_t,uint64_t,uint64_t,const uint8_t*,uint32_t)override{++uploads;}
 void Submit(const GSPrimitiveBatch&)override{throw std::runtime_error("raw owner received decoded draw");}
 void LoadClut(const GSTex0Reg&,const GSTexClutReg&)override{throw std::runtime_error("raw owner received decoded CLUT");}
 void UploadImage(const uint8_t*,uint32_t)override{throw std::runtime_error("raw owner received decoded upload");}
 void BeginTransfer(const GSTransferCommand&)override{++transfers;}
 void CaptureField(const std::array<uint64_t,15>&p,uint64_t t,uint32_t phase)override{field={p,t,phase};++fields;}
 PresentationFrame Present(const GSPresentationRequest&)override{throw std::runtime_error("raw UI used mutable presentation request");}
 PresentationFrame PresentCompletedField()override{++presents;PresentationFrame f;f.stamp={37,2,99,1};f.width=f.height=2;f.pixels.assign(640*480*4,93);return f;}
};
static void testRawSeam(){
 bool rejectedWrapper=false;try{GSThreadedBackend bad(std::make_unique<RawSeamBackend>());}catch(const std::invalid_argument&){rejectedWrapper=true;}
 check(rejectedWrapper,"decoded threaded wrapper silently hid raw ingress owner");
 GS gs;auto raw=std::make_unique<RawSeamBackend>();auto*r=raw.get();gs.setRasterBackend(std::move(raw));
 // Null privileged state proves owned UI presentation does not require MMIO.
 gs.init(nullptr,0,nullptr);gs.latchHostPresentationFrame();
 std::vector<uint8_t>pixels;uint32_t w=0,h=0;GSPresentationStamp stamp;
 check(gs.copyLatchedHostPresentationFrame(pixels,w,h,nullptr,nullptr,nullptr,&stamp)&&stamp.tick==37&&stamp.generation==2&&stamp.ingressSequence==99&&stamp.phase==1&&w==2&&h==2&&pixels.front()==93&&r->presents==1,"raw completed frame missing");
 GSRegisters priv{};priv.smode1=27;gs.init(nullptr,0,&priv);gs.captureFieldBoundary(11);
 check(r->fields==1&&r->field.tick==11&&r->field.phase==1&&r->field.privileged[1]==27,"raw field boundary wrong");
 // Complete PACKED A+D point: one raw packet; internal register writes do not reenter.
 const uint64_t point[]={3ull|(1ull<<60),14,0,0,0x3f80000080808080ull,1,0,5};
 gs.processGIFPacket(reinterpret_cast<const uint8_t*>(point),sizeof(point));
 check(r->gifs==1&&r->registers==0,"generic ingress duplicated decoded register writes");
 check(gs.processNativePackedGIFPacket(reinterpret_cast<const uint8_t*>(point),sizeof(point)),"valid native packet rejected");
 check(r->gifs==2&&r->registers==0,"native packed ingress duplicated");
 check(!gs.processNativePackedGIFPacket(reinterpret_cast<const uint8_t*>(point),sizeof(point)-1)&&r->gifs==2,"invalid native packet forwarded");
 const uint64_t upload[]={4ull|(1ull<<60),14,1ull<<48,0x50,0,0x51,1ull|(1ull<<32),0x52,0,0x53,1ull|(2ull<<58),0,0x12345678,0};
 gs.processGIFPacket(reinterpret_cast<const uint8_t*>(upload),sizeof(upload));
 check(r->gifs==3&&r->uploads==0&&r->transfers>0,"native image shortcut duplicated raw upload or lost transfer state");
 uint8_t data[16]={};gs.uploadImageNative(1ull<<48,0,1ull|(1ull<<32),0,data,sizeof(data));
 check(r->uploads==1&&r->gifs==3,"public native upload duplicated or omitted");
 gs.writeRegister(1,0x1234);check(r->registers==1,"direct register ingress missing");
 r->fail=true;bool failed=false;try{gs.processGIFPacket(reinterpret_cast<const uint8_t*>(point),sizeof(point));}catch(const std::runtime_error&e){failed=std::string(e.what())=="raw ingress failure";}
 check(failed&&r->gifs==3,"raw ingress failure hidden");
 std::cout<<"PASS: raw generic/native/direct/upload exactly-once, decoded draw suppression, transfer metadata, immutable UI bypass and failures\n";
}
int main(){
 testRawSeam();
 testFieldBoundary();
 for(bool worker:{false,true}) {
  GSRegisters regs{};auto gate=std::make_shared<Gate>();auto gs=std::make_unique<GS>();
  std::unique_ptr<GSRasterBackend>b=std::make_unique<BlockedBackend>(gate);if(worker)b=std::make_unique<GSThreadedBackend>(std::move(b));gs->setRasterBackend(std::move(b));gs->init(nullptr,0,&regs);
  const int old=std::fegetround();std::fesetround(FE_DOWNWARD);gs->requestHostPresentationFrame();std::fesetround(old);waitEntered(gate,1);
  auto start=std::chrono::steady_clock::now();for(unsigned i=0;i<100;i++)gs->requestHostPresentationFrame();check(std::chrono::steady_clock::now()-start<200ms,"request blocked on slow rasterizer");
  release(gate,1);waitEntered(gate,2);std::vector<uint8_t>pixels;uint32_t w=0,h=0;check(gs->copyLatchedHostPresentationFrame(pixels,w,h)&&w==2&&h==2&&pixels.size()==16&&pixels.front()==1&&pixels.back()==1,"latest completed frame missing or incoherent");
  release(gate,2);std::this_thread::sleep_for(20ms);{std::lock_guard<std::mutex>l(gate->mutex);check(gate->entered==2,"coalesced burst created more than one follow-up");}
  gs->requestHostPresentationFrame();waitEntered(gate,3);
  std::atomic<bool>resetDone=false;std::thread reset([&]{gs->reset();resetDone=true;});std::this_thread::sleep_for(20ms);check(!resetDone,"reset did not join active presentation");release(gate,3);reset.join();check(!gs->copyLatchedHostPresentationFrame(pixels,w,h),"reset published stale frame");
  gs->requestHostPresentationFrame();waitEntered(gate,4);release(gate,4);std::this_thread::sleep_for(20ms);{std::lock_guard<std::mutex>l(gate->mutex);check(gate->entered==4,"reset retained stale pending requests");check(gate->rounding[0]==FE_DOWNWARD&&gate->rounding[1]==FE_DOWNWARD,"presentation FP environment changed");}
  gs->requestHostPresentationFrame();waitEntered(gate,5);std::atomic<bool>destroyed=false;std::thread destroy([&]{gs.reset();destroyed=true;});std::this_thread::sleep_for(20ms);check(!destroyed,"destructor did not join active presentation");release(gate,5);destroy.join();
 }
 {GSRegisters regs{};auto gate=std::make_shared<Gate>();gate->fail=true;gate->allowed=10;GS gs;gs.setRasterBackend(std::make_unique<BlockedBackend>(gate));gs.init(nullptr,0,&regs);gs.requestHostPresentationFrame();waitEntered(gate,1);bool caught=false;for(unsigned i=0;i<100&&!caught;i++){std::this_thread::sleep_for(2ms);try{gs.requestHostPresentationFrame();}catch(const std::runtime_error&e){caught=std::string(e.what())=="synthetic presentation failure";}}check(caught,"asynchronous failure not propagated");gs.reset();}
 std::cout<<"PASS: nonblocking coalesced presentation, ordered immutable frames, FP environment, reset/destruction joins and failure propagation\n";
}
