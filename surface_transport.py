"""Stage the recognized window/latch surface transport into a NEW pinned graph.

No game input or historical binary is embedded. Unknown source shapes fail closed.
The resulting graph still requires native compilation and gameplay qualification.
"""
from pathlib import Path
import json
from clean_build import inspect,sha,path_in
import graph_amend

HEADERS=('mac_surface_presenter.hpp','surface_consumer.hpp','surface_token.hpp')

def once(text,old,new):
 if text.count(old)!=1:raise ValueError('Unrecognized presentation source; expected exactly one anchor: '+old[:80])
 return text.replace(old,new,1)

def window_source(text):
 text='#include "ps2port_surface/mac_surface_presenter.hpp"\n'+text
 text=once(text,'static void UploadFrame(Texture2D &tex,', '#if defined(__APPLE__)\nstatic ps2port::MacSurfacePresenter surfacePresenter;\nstatic void* surfaceProducerHandle=nullptr;\n#endif\nstatic std::uint64_t presentationWindowEpoch=0;\n\nstatic void UploadFrame(Texture2D &tex,')
 marker='    s_lastCompletedStamp=completedStamp;\n    PresentationPacing::uploaded(completedStamp.tick,completedStamp.generation,completedStamp.ingressSequence);'
 # Successful delivery alone commits the dedup stamp. A pending fence/stale
 # token must be retried instead of marking an image as already uploaded.
 block='''#if defined(__APPLE__)
    ps2port::SurfaceToken token{};
    if (ps2port::decodeSurfaceToken(s_scratch,&token)) {
        if (token.width!=width || token.height!=height)
            throw std::runtime_error("Surface dimensions differ from latched field");
        if (!surfacePresenter.acquire(token)) {outWidth=s_lastWidth;outHeight=s_lastHeight;return;}
        if (tex.width!=int(width) || tex.height!=int(height)) {
            UnloadTexture(tex);Image blank=GenImageColor(width,height,BLACK);
            tex=LoadTextureFromImage(blank);UnloadImage(blank);SetTextureFilter(tex,TEXTURE_FILTER_BILINEAR);
        }
        if (!surfacePresenter.copyAcquired(tex.id,token)) {outWidth=s_lastWidth;outHeight=s_lastHeight;return;}
        s_lastCompletedStamp=completedStamp;
        PresentationPacing::uploaded(completedStamp.tick,completedStamp.generation,completedStamp.ingressSequence);
        outWidth=s_lastWidth=width;outHeight=s_lastHeight=height;s_hasUploadedFrame=true;
        return;
    }
#endif
'''
 text=once(text,marker,block+marker)
 text=once(text,'        m_gs.setRasterBackend(std::move(backend));', '''#if defined(__APPLE__)
        surfaceProducerHandle=plugin;
        if (const char* zero=std::getenv("PS2_PORT_HW_GS_ZERO_COPY"); zero && zero[0]=='1' && zero[1]==0)
            if (!surfacePresenter.configureProducer(plugin)) throw std::runtime_error("Surface producer API missing from loaded plugin");
#endif
        m_gs.setRasterBackend(std::move(backend));''')
 init='InitWindow(HOST_WINDOW_WIDTH, HOST_WINDOW_HEIGHT, title);'
 if text.count(init) not in (1,2):raise ValueError('Unrecognized window initialization branches')
 text=text.replace(init,init+''' ++presentationWindowEpoch;
#if defined(__APPLE__)
        if (surfaceProducerHandle) {
            const char* zero=std::getenv("PS2_PORT_HW_GS_ZERO_COPY");
            if (zero && zero[0]=='1' && zero[1]==0 && !surfacePresenter.configureProducer(surfaceProducerHandle))
                throw std::runtime_error("Cannot bind surface producer to recreated window");
        }
#endif
''')
 state='    static std::vector<uint8_t> s_uploadBuffer(DEFAULT_FB_SIZE, 0u);'
 text=once(text,state,state+'''
    static std::uint64_t uploadedWindowEpoch=~std::uint64_t(0);
    if (uploadedWindowEpoch!=presentationWindowEpoch) {
        uploadedWindowEpoch=presentationWindowEpoch;
        s_lastPresentationTick=std::numeric_limits<uint64_t>::max();
        s_hasLatchedInitialFrame=false;s_lastCompletedStamp={};
        s_lastDisplayFbp=s_lastSourceFbp=std::numeric_limits<uint32_t>::max();
        s_lastPreferred=false;s_lastWidth=s_lastHeight=0;s_hasUploadedFrame=false;
        s_scratch.clear();s_uploadBuffer.assign(DEFAULT_FB_SIZE,0);
    }
''')
 # Both error teardown and normal teardown release before context destruction.
 if 'CloseWindow();' not in text:raise ValueError('Window shutdown anchor missing')
 text=text.replace('CloseWindow();','''#if defined(__APPLE__)
        if (!surfacePresenter.shutdown()) throw std::runtime_error("Surface context shutdown failed");
#endif
        CloseWindow();''')
 return text

def frontend_source(text):
 text='#include "ps2port_surface/surface_token.hpp"\n'+text
 marker='    const size_t packedRowBytes = static_cast<size_t>(outWidth) * 4u;'
 return once(text,marker,'''    ps2port::SurfaceToken token{};
    if (ps2port::decodeSurfaceToken(m_hostPresentationFrame,&token)) {
        if (token.width!=outWidth || token.height!=outHeight) {
            outPixels.clear();outWidth=outHeight=0;return false;
        }
        outPixels=m_hostPresentationFrame;
        return true;
    }
'''+marker)

def stage(recipe,staging,destination,*,window=None,frontend=None):
 patcher_pin=sha(__file__)
 recipe=Path(recipe).resolve();staging=Path(staging).resolve();out=Path(destination).resolve()
 checked=inspect(recipe);root=checked['root'];r=checked['recipe']
 selected={unit['source'] for unit in r['units']}
 def choose(explicit,basename):
  if explicit is not None:return explicit
  matches=[name for name in selected if Path(name).name==basename]
  if len(matches)!=1:raise ValueError('Cannot uniquely identify compiled '+basename+'; provide an explicit pinned source selection')
  return matches[0]
 window=choose(window,'ps2_runtime.cpp');frontend=choose(frontend,'gs_frontend.cpp')
 if window==frontend or window not in selected or frontend not in selected:raise ValueError('Select distinct actual window/frontend compilation units')
 edits=[]
 for name,patch in [(window,window_source),(frontend,frontend_source)]:
  source=path_in(root,name);before=source.read_text()
  edits.append(dict(file=name,sha256=sha(source),before=before,after=patch(before),reason='Explicit macOS surface transport with GPU-completion ownership'))
 config=out.with_name(out.name+'-amendment.json')
 if config.exists() or out.exists():raise ValueError('Preserve existing surface graphs and amendments')
 header_root=Path(__file__).resolve().parent/'runtime/presentation'
 headers={name:(header_root/name).read_bytes() for name in HEADERS}
 additions={}
 for source in (window,frontend):
  for name,data in headers.items():
   target=str(Path(source).parent/'ps2port_surface'/name)
   if target in r['files']:raise ValueError('Surface headers already staged')
   additions[target]=data
 config.write_text(json.dumps(dict(schema=1,recipe=str(recipe),staging=str(staging),recipe_sha256=sha(recipe),staging_sha256=sha(staging),edits=edits),indent=2)+'\n')
 graph_amend.amend(config,out)
 r=json.loads((out/'recipe.json').read_text());ledger=json.loads((out/'staging.json').read_text())
 for name,data in additions.items():
  target=path_in(out,name);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  r['files'][name]=sha(target)
  ledger['files'].append(dict(staged=name,original=str(header_root/Path(name).name),original_sha256=sha(target),sha256=sha(target),role='surface_transport_header'))
 for target in r['targets']:
  if target['kind']=='executable' and 'IOSurface' not in target.setdefault('frameworks',[]):target['frameworks'].append('IOSurface')
 (out/'recipe.json').write_text(json.dumps(r,indent=2)+'\n');inspect(out/'recipe.json')
 if sha(__file__)!=patcher_pin or any((header_root/name).read_bytes()!=data for name,data in headers.items()):
  ledger.update(status='failed_surface_transport_inputs_changed',gameplay_verified=False)
  (out/'staging.json').write_text(json.dumps(ledger,indent=2)+'\n')
  raise ValueError('Surface transport patcher or headers changed during staging')
 ledger.update(recipe_sha256=sha(out/'recipe.json'),status='surface_transport_staged_not_built',gameplay_verified=False)
 ledger['surface_transport']=dict(window=window,frontend=frontend,headers={n:r['files'][n] for n in additions},patcher_sha256=patcher_pin,stale_token_policy='reject_exact_serial_and_retry',default_enabled=False)
 (out/'staging.json').write_text(json.dumps(ledger,indent=2)+'\n')
 return dict(status=ledger['status'],recipe=str(out/'recipe.json'),staging=str(out/'staging.json'))

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 for name in ('recipe','staging','destination'):parser.add_argument('--'+name,required=True)
 for name in ('window','frontend'):parser.add_argument('--'+name)
 args=parser.parse_args()
 print(json.dumps(stage(args.recipe,args.staging,args.destination,window=args.window,frontend=args.frontend),indent=2))
