# Local texture enhancement stage

## Local learned-detail preview backend

`local_sr.py` is the bounded Real-ESRGAN preview runner; `sr_batch.py` is the
approved classified batch backend described below. Kelly's completed private
AI pack and v56 GPU replacement loader are first-title implementations, not
universal runtime support. The earlier Lanczos pack only resamples existing
pixels; learned super-resolution infers plausible detail, without recovering
missing original artwork. New titles still require classification and visual
and runtime qualification.

Download the official [Real-ESRGAN Mac package](https://github.com/xinntao/Real-ESRGAN) separately. A private schema1 lock JSON beside the installation pins `model: "realesrgan-x4plus"`, an `executable` object and two `models` objects, each with a relative `file` and `sha256`. Models must be `realesrgan-x4plus.bin` and `realesrgan-x4plus.param` in the same folder. The runner checks all hashes before executing, uses argument arrays, denies network access with macOS sandbox-exec, limits input size/count and execution time, reserves1GiB disk space, retains failed reports/logs, and refuses to overwrite a run.

```sh
python3 local_sr.py --lock /absolute/private/tool/lock.json --output /absolute/private/new-preview /absolute/private/original.png
```

The selected policy uses **full Real-ESRGAN for ordinary surfaces** and **30% Real-ESRGAN +70% premultiplied Lanczos only for words, numbers or important fine details**. Use `--detail-sensitive` for an explicitly classified sensitive batch; unflagged CLI runs retain full AI. `--ai-weight` is an explicit comparison override. Matching source-derived alpha is required and retained byte for byte. The blend matches the channel-linear clock comparisons. It softens invented detail rather than correcting it. Model parameters, executable, implementation hash and blend weight are recorded in every run. No automatic word/fine-detail classifier has been implemented: unknown semantics must be reviewed before batching. Preserve the faithful baseline for effects visibly harmed by AI, such as the ring preview. This policy selection does not establish suitability for repeating textures or atlases, or install textures in the game.

Run GPU inference only when game sessions, builds, GPU diagnostics and benchmarks are stopped. The portable executable requires a usable Vulkan/MoltenVK device; command-line help alone does not establish inference compatibility.

This initial preview composites RGB over neutral gray before inference and resamples original alpha separately. Transparent glows, cutouts, repeating surfaces, atlases and text need dedicated qualification; do not bulk-apply this policy. It currently outputs preview PNGs and provenance, not runtime cache entries or game replacements. No textures or model binaries enter the public source package.

Run `python3 pipeline.py enhance-textures --manifest textures/catalog.json --name quality-01` after a title extractor has decoded its textures to private PNGs. Requires local Pillow (development qualification: 11.3.0). No remote image generation, model service, upload, network calls or external executable is used by this stage.

Implemented: batch decoded-PNG ingestion, boot executable revision/source fingerprint gates, decoded RGBA identity, deterministic premultiplied Lanczos resampling, opposite-edge padding for repeating world textures, optional nearest alpha for cutouts, complete world mip chains, and a verified local cache. Cache keys include pixels, dimensions, processing policy, implementation hash and Pillow version. Changes in palette-decoded colors or processing settings invalidate identity. Identical decoded content/policy reuses the cache. Corrupt cache entries fail rather than silently becoming replacement textures.

Classify each texture as `world`, `ui`, `text` or `unclassified`. World textures accept scale1/2/4 and wrap `repeat` or `clamp`, with alpha `smooth` or `cutout`. UI/text/unclassified stay at original resolution by default. Explicit `ui_filter: "nearest"` enables integer crisp scaling for classified UI/text with clamp addressing, retaining original colors and hard pixel boundaries. This helps pixel-style artwork; it does not reconstruct smooth vector lettering. UI/text get no generated mip chain. Font/glyph-aware rasterization and restrained smooth-font filtering remain separate work. Visual review is required, especially for cutout mipmaps, small lettering and atlases.

Private manifest schema:

```json
{
  "schema": 1,
  "title_id": "your-title",
  "executable_sha256": "SHA256_OF_OWNED_BOOT_EXECUTABLE",
  "textures": [
    {
      "id": "world-surface-001",
      "file": "textures/decoded/surface.png",
      "sha256": "SHA256_OF_DECODED_PNG_FILE",
      "role": "world",
      "scale": 2,
      "wrap": "repeat",
      "alpha": "smooth"
    }
  ]
}
```

Files and manifest must be inside the private game directory. Inputs are bounded to1024textures/8million sourcepixels per batch; maximum1million sourcepixels per texture and4million outputpixels. All manifests/inputs validate before outputs begin. Original disc/decoded files remain unchanged. Reports are unique under `texture-runs/NAME/report.json`; cached images live under `texture-cache/KEY/mip-NN.png`. Errors after processing starts retain a failed report. Interrupted cache writes remain rejected/incomplete; no automatic cleanup or overwrite.

A bounded `tim2.py` decoder/scanner now supports single-picture linear TIM2 RGB24, RGBA32 and 8-bit indexed textures with standard24/32-bit256-entry CSM1 palettes. Layout/palette/alpha handling was cross-checked against [ImageMagick’s TIM2 reader](https://github.com/ImageMagick/ImageMagick/blob/main/coders/tim2.c). This independently written subset rejects other image layouts; it is not a complete ST2 archive parser. Three main Kelly ST2 files yielded259 eligible decoded samples at least16x16 for a local random preview.

**Not implemented yet:** complete Kelly ST2 extraction/naming, automated role/atlas classification, GS texture replacement binding/cache invalidation, packaging and in-game visual/performance acceptance. `runtime_integration: "pending"` is deliberate. Processing these PNGs does not automatically change the game. Current renderer framebuffer-feedback candidate is a separate feature, not a replacement-loader implementation.

Original assets are not enriched with authentic new detail by resampling. A future pinned local upscaler can be a separate backend with model/license hashes, alpha/tiling controls, review and conformance gates. Keep texture enhancement separate from correctness, optional and reversible. Never replace game RAM/VRAM readback data with enhanced pixels; a host replacement sampler should preserve original GS data for guest operations.

Reusable source packaging includes only this code/documentation and asset-free tests. Retail textures, manifests, caches, game code and reports stay local and are excluded.


## Optional adaptive detail profile

Set `"detail": "adaptive"` on a world-texture entry for bounded source-space luminance sharpening before resampling. Requires local NumPy (qualified2.3.1); its version joins the cache key. Alpha-normalized Gaussian filtering ignores RGB hidden in transparent pixels. A noise threshold, +/-18 brightness correction bound and visible-neighbor color bounds limit ringing. Alpha values stay unchanged before ordinary resampling. Repeating textures use periodic padding. Default `detail: "none"` retains the original smoother pass. This changes contrast and cannot recover missing artwork. Glows carried in alpha may show little improvement by design. UI/font refinement remains separately classified work.

## Upscale, sharpen, and blend experiment

Set `"detail": "postblend"` to resample first, sharpen the enlarged RGB image, then blend it with the unsharpened result. Local visible-pixel luminance contrast weights the blend from0to0.65, reducing changes to flat regions. The sharpening radius scales with enlargement; visible-neighbor bounds limit ringing. Enlarged alpha stays byte-identical, and hidden transparent RGB is excluded from blend weighting. Mipmaps are generated from the combined result. The original `adaptive` source-space and `none` profiles remain available with distinct cache keys. This is an optional comparison candidate, not an automatically accepted visual improvement.

## Explicit first-pass batches of unclassified textures

The initial Kelly baseline uses4x first-pass resampling with `detail: "none"`; the later approved classified AI policy is described below. Adaptive/postblend experiments remain opt-in. Unknown texture semantics can be explicitly resampled using `role: "unclassified"` and `unclassified_filter: "lanczos"`. They remain flagged `classification_required`; this is not approval to bind them with guessed wrapping or UI/atlas behavior. Default unknown textures still preserve their original size. This explicit path creates a mip chain for review; the eventual loader must select appropriate material/UI policies.

`min_free_bytes` can reserve256MiB..8GiB on the private storage volume (default256MiB). Before each uncached texture, the processor checks free space against the reserve plus a conservative mip-output allowance. Low-space failures retain completed entries and a failed report rather than filling the drive. The first Kelly full supported batch reserves1GiB.

## Extended TIM2 coverage

The decoder now supports linear direct RGB16/24/32, 4-bit indexed textures (linear16-entry or CSM1 compound32-entry palettes), and 8-bit indexed textures with16/24/32-bit palettes. Standard file-alignment palette padding is validated. Up to7authored mip levels can be addressed explicitly with `decode_tim2(data, offset, level)`; byte ranges and nonzero dimensions are checked against the stored level-size table. The enhancement batch currently uses level0 and generates its own mip chain; authored lower mips remain in the untouched disc and their metadata is retained for later renderer-policy review.

Compound palette order, nibble order, RGB5551 channels and mip offsets were cross-checked with the publicly available [TIM2 sample reference](https://github.com/GirianSeed/tim2/tree/trunk/sample) and [tim2dump type definitions](https://github.com/polymood/tim2dump/blob/main/src/tim2_types.h). No SDK reference implementation is bundled or copied into the reusable source. CSM2 and nonzero initial palette banks remain rejected rather than guessed. Format/synthetic checks and coherent sample previews are not original-hardware validation.

The updated scan of the209first-title ST2 files recognizes5,189embedded TIM2 occurrences (2,132unique decoded level0images), with no remaining rejected TIM2 signatures in that inventory. This does not prove there are no textures in other containers, dynamically generated textures, or other palette variants used at runtime.

## Resumable classified super-resolution packs

`sr_batch.py --root PRIVATE_GAME --manifest PRIVATE_MANIFEST --lock PRIVATE_MODEL_LOCK --output PRIVATE_NEW_OR_RESUMED_PACK` processes up to4096 explicitly classified decoded textures in groups of at most16. The schema1 manifest contains `textures`, each with a unique64-hex `id`, private relative `source` and `baseline` PNG paths, their `source_sha256`/`baseline_sha256`, a `mode` (`full`, `detail-sensitive`, `preserve`) and a `reason`. Modes select1.0,0.30 or0.0 AI respectively. The baseline must be the matching4x source-derived-alpha Lanczos result. Sources are bounded to262,144pixels; original files remain unchanged.

The pack pins manifest, model lock and both implementation hashes. Resume validates every indexed output hash. A file lock prevents overlapping pack writers. Before each GPU group the runner rejects an active game, another upscaler or native Ninja build. Model execution denies network access, has a time limit, and monitors a1GiB free-space reserve. Scratch inputs/predictions are temporary; final PNGs, logs and progress survive interruption. Faithful entries reference existing baseline files rather than creating duplicates. The pack currently includes enhanced level0 only; runtime mip generation, atlas and wrapping contracts remain pending. Do not delete the baseline cache while a pack references it.

`tools/local_text_ocr.m` is a reusable Apple Vision local CPU text-recognition helper. Compile with Clang/Foundation/Vision/ImageIO/CoreGraphics, then provide a JSON array of `{ "id": "...", "file": "/absolute/private/input.png" }`. Its JSON-lines output records text/confidence/bounds or errors. Validate it on an asset-free known-text control before interpreting empty results: sandboxed Vision can silently return no observations. OCR is a classification aid, not proof of every word or important small detail. Title adapters must record heuristic thresholds, visual/manual overrides and conservative effect fallbacks. No retail files, OCR output, models or compiled helper enter the public source package.

ATEX animation containers are a separate discovery path from TIM2 signature
scanning. `ate.py` reads bounded split picture-header, image and palette ranges,
including shared data, and feeds a reconstructed read-only TIM2 view into the
qualified decoder. Enumerate each named frame and palette variant, preserve
sequence identity, and qualify temporal/alpha behaviour before enabling runtime
replacement. Water lighting/foam layers require a conservative effect policy;
independent AI inference on every frame is not automatically qualified. Raw
upload-hash membership measures extraction coverage, not on-screen replacement.
`scan_ate(data)` discovers qualified frames after invalid signatures and retains
separate named entries even when image and palette ranges are shared. Unsupported
entries are skipped; archive-level census must also record rejected signatures.
