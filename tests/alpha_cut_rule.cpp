// Claude 2026-10-27: alpha_cut_rule::Applies - the hard-alpha-cut only where the texture's alpha counts (TEX0.TCC).
#include "../runtime/gpu/host-adapter/alpha_cut_rule.hpp"
#include <cstdio>
int main() {
    const unsigned long long tcc1 = 1ull << 34, tcc0 = 0;
    int fail = 0, n = 0;
    auto check = [&](bool got, bool want, const char* what) { ++n; if (got != want) { ++fail; std::printf("FAIL %s\n", what); } };
    // The HUD wave panel: TCC 1, minified 2D piece at 3x -> cut.
    check(alpha_cut_rule::Applies(tcc1, true, true, 3, 4, true, 40, 20, 1.6f), true, "panel piece (TCC 1)");
    // The Florida photo shown minified, TCC 0 (alpha from the vertex) -> no cut.
    check(alpha_cut_rule::Applies(tcc0, true, true, 3, 4, true, 80, 80, 1.6f), false, "photo (TCC 0)");
    // The rest of the rule unchanged.
    check(alpha_cut_rule::Applies(tcc1, true, true, 1, 4, true, 40, 20, 1.6f), false, "scale 1");
    check(alpha_cut_rule::Applies(tcc1, true, true, 3, 4, false, 40, 20, 1.6f), false, "perspective");
    check(alpha_cut_rule::Applies(tcc1, true, true, 3, 4, true, 40, 20, 1.2f), false, "font-like ratio");
    check(alpha_cut_rule::Applies(tcc1, true, true, 3, 4, true, 6, 20, 1.6f), false, "under 8 pixels");
    check(alpha_cut_rule::Applies(tcc1, true, false, 3, 4, true, 40, 20, 1.6f), false, "not blended");
    std::printf(fail ? "test_alpha_cut_rule: %d of %d FAILED\n" : "test_alpha_cut_rule: %d/%d passed\n", fail ? fail : n, n);
    return fail != 0;
}
