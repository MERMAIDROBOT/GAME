# Reusable source licensing and dependencies

The reusable engine source is prepared under GPL-3.0; the license text is in
LICENSE. Existing upstream notices remain in copied/derived components. This
does not license any user's retail game assets or generated game-specific code.

`tools/vu1_recomp.cpp` derives from the pinned PS2Recomp project and retains its
notice; its license is also preserved as tools/PS2Recomp-LICENSE. Dependency
URLs/revisions are recorded in toolchain.lock.json. Public dependencies are
downloaded separately rather than vendored into this source export.

The first-title local app additionally uses paraLLEl-GS, Granite, MoltenVK,
raylib/GLFW and FFmpeg. Its dependency notices are in the app bundle. Later
consolidation must preserve the notices of every runtime module actually adopted.
FSR1 EASU integration and Real-ESRGAN models must retain their own upstream
attribution/license terms when incorporated. Model files are not included here.

Only explicitly whitelisted reusable source enters the source ZIP or a future
GitHub publication. Disc assets, retail-derived generated C++, binaries, captures,
logs and private title manifests are excluded.


PMULTH investigation credit: the God of War ports by Waffle565 and KIexster
identified the eight-product/HI-LO defect. The guarded correction here is an
independent implementation, checked against PCSX2 MMI.cpp. The recognized legacy
emission is from the pinned GPL-3.0 PS2Recomp compiler already credited above.
No God of War functions, disc bytes or generated game sources are included.

Periodic texture-context handling carries the seam lessons from the first local
port: wrapped inference, consistent baseline/alpha borders, animation families
and avoiding internal model-tile joins. No retail texture or trained model is
included. External GPU renderer candidates remain reference-only and are not
licensed or bundled by this source export.
