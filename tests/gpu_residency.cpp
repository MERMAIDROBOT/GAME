#include "ps2gpu/gs_renderer.h"
#include <unordered_set>
#include <atomic>
#include <cassert>
#include <cstdio>
using namespace ps2gpu;
struct Mock final:GsRenderer {
 mutable unsigned queries=0;std::unordered_set<std::uint64_t> resident;std::atomic<unsigned> draws{0};
 bool HasReplacement(std::uint64_t id)const override {++queries;return resident.count(id);}
 bool UploadReplacement(std::uint64_t id,std::uint32_t format,std::uint32_t,std::uint32_t,std::uint32_t,const std::uint8_t*,std::size_t)override {resident.erase(id);if(format==99)return false;resident.insert(id);return true;}
 void Kick() override {resident.erase(1);}
void UploadTexture(std::uint64_t key, const std::uint32_t* pixels, std::uint32_t width, std::uint32_t height) override {}
void UploadDepth(std::uint32_t zbp, std::uint32_t width, std::uint32_t height, const std::uint32_t* z) override {}
void UploadTarget(std::uint32_t fbp, std::uint32_t fbw, std::uint32_t psm, const std::uint32_t* pixels, std::uint32_t width, std::uint32_t height) override {}
void ReleaseTexture(std::uint64_t key) override {}
bool HasTarget(std::uint32_t fbp, std::uint32_t fbw, std::uint32_t psm) const override {return false;}
void DrawTriangles(const GsDrawState& state, const GsVertex* vertices, std::size_t count) override {++draws;}
void Flush() override {}
void MemoryWrite(std::uint32_t begin, std::uint32_t end, const std::uint8_t* bytes) override {}
void MemoryRead(std::uint32_t begin, std::uint32_t end, std::uint8_t* bytes) override {}
bool TargetToMemory(std::uint32_t fbp, std::uint32_t fbw, std::uint32_t psm, std::uint32_t cols, std::uint32_t rows, std::uint32_t first_row = 0) override {return false;}
void TargetFromMemory(std::uint32_t fbp, std::uint32_t fbw, std::uint32_t psm, std::uint32_t width, std::uint32_t height) override {}
bool DepthToMemory(std::uint32_t zbp, std::uint32_t width, std::uint32_t height, std::uint32_t fbw, std::uint32_t zpsm, std::uint32_t cols, std::uint32_t rows, std::uint32_t first_row = 0) override {return false;}
void DepthFromMemory(std::uint32_t zbp, std::uint32_t width, std::uint32_t height, std::uint32_t fbw, std::uint32_t zpsm) override {}
void TextureFromMemory(std::uint64_t key, const GsTextureDecode& decode) override {}
void ClutFromMemory(const GsClutLoad& load) override {}
void ClutRows(const std::uint32_t* clut, std::uint32_t rows) override {}
bool ReadDepth(std::uint32_t zbp, std::uint32_t width, std::uint32_t height, std::vector<std::uint32_t>* z) override {return false;}
bool ReadTexture(std::uint64_t key, std::vector<std::uint32_t>* pixels, std::uint32_t* width, std::uint32_t* height) override {return false;}
bool ReadTarget(std::uint32_t fbp, std::uint32_t fbw, std::uint32_t psm, std::vector<std::uint32_t>* pixels, std::uint32_t* width, std::uint32_t* height) override {return false;}
};
int main(){
 auto owned=std::make_unique<Mock>();auto* raw=owned.get();auto r=MakeThreadedGsRenderer(std::move(owned));std::uint8_t px[4]={};
 assert(r->UploadReplacement(1,0,1,1,1,px,4));GsDrawState st{};GsVertex vs[3]{};
 for(int i=0;i<1000;i++){r->DrawTriangles(st,vs,3);assert(r->HasReplacement(1));}
 r->MemoryRead(0,0,px);assert(raw->draws==1000);unsigned repeated=raw->queries;
 printf("Repeated residency queries reached inner renderer: %u (1000 draws preserved)\n",repeated);
 r->Kick();assert(!r->HasReplacement(1));assert(!r->HasReplacement(2));
 assert(r->UploadReplacement(2,0,1,1,1,px,4));assert(r->HasReplacement(2));
 assert(!r->UploadReplacement(2,99,1,1,1,px,4));assert(!r->HasReplacement(2));
 r->SetScale(2);assert(!r->HasReplacement(2));puts("PASS eviction boundary, upload success/failure invalidation, scale, draw ordering");
 return repeated==1?0:2;
}
