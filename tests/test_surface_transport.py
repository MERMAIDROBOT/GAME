from pathlib import Path
import json,tempfile,unittest
import surface_transport as transport
from source_graph import stage
from clean_build import sha,inspect

WINDOW='''void start() { InitWindow(HOST_WINDOW_WIDTH, HOST_WINDOW_HEIGHT, title); }
void backend() {
        m_gs.setRasterBackend(std::move(backend));
}
static void UploadFrame(Texture2D &tex, PS2Runtime *rt) {
    static std::vector<uint8_t> s_uploadBuffer(DEFAULT_FB_SIZE, 0u);
    s_lastCompletedStamp=completedStamp;
    PresentationPacing::uploaded(completedStamp.tick,completedStamp.generation,completedStamp.ingressSequence);
}
void close() { CloseWindow(); }
'''
FRONTEND='''void copy() {
    const size_t packedRowBytes = static_cast<size_t>(outWidth) * 4u;
}
'''
class SurfaceTransport(unittest.TestCase):
 def test_staging_pins_actual_compiled_sources_headers_and_frameworks(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);src=root/'src';src.mkdir()
   (src/'window.cpp').write_text(WINDOW);(src/'frontend.cpp').write_text(FRONTEND)
   config=root/'stage.json';config.write_text(json.dumps(dict(schema=1,roots=dict(core=str(src)),units=[dict(id=n,source=str(src/(n+'.cpp'))) for n in ['window','frontend']],targets=[dict(id='game',kind='executable',units=['window','frontend'])])))
   original=root/'original';stage(config,original)
   before=sha(original/'recipe.json');out=root/'out'
   result=transport.stage(original/'recipe.json',original/'staging.json',out,window='sources/core/window.cpp',frontend='sources/core/frontend.cpp')
   self.assertEqual(before,sha(original/'recipe.json'));self.assertEqual(result['status'],'surface_transport_staged_not_built')
   checked=inspect(out/'recipe.json');recipe=checked['recipe']
   self.assertIn('IOSurface',recipe['targets'][0]['frameworks'])
   self.assertIn('sources/core/ps2port_surface/mac_surface_presenter.hpp',recipe['files'])
   ledger=json.loads((out/'staging.json').read_text());self.assertEqual(ledger['recipe_sha256'],checked['recipe_sha256']);self.assertFalse(ledger['gameplay_verified'])
   self.assertTrue(all('original' in row and 'original_sha256' in row for row in ledger['files']))
   with self.assertRaises(ValueError):transport.stage(original/'recipe.json',original/'staging.json',out,window='sources/core/window.cpp',frontend='sources/core/frontend.cpp')
   (out/'sources/core/ps2port_surface/surface_token.hpp').write_text('changed')
   with self.assertRaises(ValueError):inspect(out/'recipe.json')
 def test_unknown_or_duplicate_source_shapes_fail_closed(self):
  for source in ['', WINDOW+WINDOW]:
   with self.assertRaises(ValueError):transport.window_source(source)
  for source in ['', FRONTEND+FRONTEND]:
   with self.assertRaises(ValueError):transport.frontend_source(source)
