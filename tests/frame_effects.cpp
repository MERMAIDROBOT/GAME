// Asset-free layout/FXAA-target tests, linked to mocks rather than a graphics device.
#include "../runtime/presentation/frame_effects.hpp"
#include <vector>
#include <string>
#include <stdexcept>
#include <cstdio>
struct Draw { Texture2D texture;Rectangle source,dest; };
static std::vector<Draw> draws;static float sourceClamp[4];static int shaders=0, shaderBegins=0;
static int logicalW=1600,logicalH=1000,physicalW=1600,physicalH=1000;
extern "C" {
unsigned int rlGetShaderIdDefault(){return 1;}
Shader LoadShaderFromMemory(const char*,const char*) { ++shaders;return Shader{42,nullptr}; }
int GetShaderLocation(Shader,const char *name){return std::string(name)=="srcRect"?7:3;}
void SetShaderValue(Shader,int loc,const void *data,int type){if(loc==7&&type==SHADER_UNIFORM_VEC4)for(int i=0;i<4;i++)sourceClamp[i]=static_cast<const float*>(data)[i];}
void TraceLog(int,const char*,...){}
int GetScreenWidth(){return logicalW;} int GetScreenHeight(){return logicalH;}
int GetRenderWidth(){return physicalW;} int GetRenderHeight(){return physicalH;}
void UnloadRenderTexture(RenderTexture2D){}
RenderTexture2D LoadRenderTexture(int w,int h){RenderTexture2D r{};r.id=20;r.texture.id=99;r.texture.width=w;r.texture.height=h;return r;}
void SetTextureFilter(Texture2D,int){}
void BeginTextureMode(RenderTexture2D){} void EndTextureMode(){}
void BeginShaderMode(Shader){++shaderBegins;} void EndShaderMode(){}
void DrawTexturePro(Texture2D t,Rectangle s,Rectangle d,Vector2,float,Color){draws.push_back({t,s,d});}
}
static void require(bool b,const char *s){if(!b)throw std::runtime_error(s);}
int main(int argc,char **argv){
 if(argc!=2)return 2;std::string mode=argv[1];
 Texture2D t{2,1024,896,1,7};Rectangle source{0,0,1024,896},dest{11,22,1400,970};
 if(mode!="identity")setenv("PS2_PORT_ENHANCE_WIDESCREEN","1",1);
 if(mode=="movie"){t.width=1280;source.width=1280;setenv("PS2_PORT_ENHANCE_MOVIE_FILL","off",1);}
 if(mode=="hidpi"){logicalW=1200;logicalH=800;physicalW=2400;physicalH=1600;t.width=1536;t.height=1344;source.width=1536;source.height=1344;setenv("PS2_PORT_ENHANCE_UPSCALE","3",1);}
 if(mode=="fxaa"){setenv("PS2_PORT_ENHANCE_FXAA","1",1);setenv("PS2_PORT_ENHANCE_UPSCALE","3",1);}
 ps2PortDrawFrame(t,source,dest,Vector2{0,0},0,WHITE);
 require(!draws.empty(),"Frame draw missing");auto result=draws.back();
 if(mode=="identity")require(draws.size()==1&&shaders==0&&result.dest.x==11&&result.dest.y==22&&result.dest.width==1400&&result.dest.height==970,"Disabled path changed draw/loaded shaders");
 else if(mode=="movie")require(result.dest.x==200&&result.dest.y==50&&result.dest.width==1200&&result.dest.height==900&&result.source.height==896,"Movie lost full 4:3 viewport");
 else if(mode=="hidpi")require(result.dest.width==1200&&result.dest.height==675&&shaderBegins==1,"Retina upscaler ignored physical drawable size");
 else require(result.dest.x==0&&result.dest.y==50&&result.dest.width==1600&&result.dest.height==900,"Game viewport is clipped or letterboxed twice");
 if(mode=="fxaa"){
  require(draws.size()==2&&draws.front().dest.width==1024&&draws.front().dest.height==896,"FXAA intermediate inherited the window rectangle");
  require(result.texture.id==99&&result.source.height==-896&&result.source.y==0,"FXAA target orientation wrong");
  require(sourceClamp[0]==0.5f&&sourceClamp[1]==0.5f&&sourceClamp[2]==1023.5f&&sourceClamp[3]==895.5f,"Flipped upscaler collapsed source rows/outside texture");
 }
 std::puts("PASS: composed frame boundary fixture");
}
