// Claude 2026-10-14 (WORKLOG 110-112): texture-pack identity tracking and fail-closed selection, candidate 4.
// Pure C++ (no Vulkan) so it can be regression-tested on the CPU without assets.
//
// Identity model:
//  - Every host->local upload is hashed (hash64 of the payload = TIM2/ATEX texel or palette bytes on disc) and recorded
//    with its layout (DBP, DBW, DPSM, DSAX/DSAY, RRW/RRH) and the exact VRAM blocks it covers (footprint supplied by
//    the caller from the GS swizzle; conservative page rects where exactness is not proven).
//  - Any later write to those blocks (host upload, local->local copy, render-target/Z write, host VRAM write) forgets the
//    record. Uploads into blocks the current render pass draws to are never recorded; the caller commits uploads only
//    after paraLLEl-GS has run its own hazard flush for the transfer.
//  - The CLUT buffer identity is captured when the GS actually loads it (TEX0 CLD), from the upload record at CBP, only
//    for CSM1 layouts whose CLUT read matches the TIM2 decoder (tests/clut_tests.cpp): T8 16x16, T4 8x2 or 16x2
//    (compound 32-entry TIM2 palettes; the GS reads entries 0-7 and 16-23). A 16x2 upload is accepted only when its
//    runtime hash, source CPSM and exact byte count are listed in GPT's compound-cluts.txt qualification sidecar
//    (and DBW=1, origin 0, CSM1, CSA 0); unlisted 32-entry palettes stay closed.
//  - Queries use the game's original TEX0 (TCC, CPSM, CSM, CSA), not paraLLEl-GS's normalised cache descriptor.
//  - A texture is replaced only when the texel record, the CLUT identity and GPT's identity sidecar
//    (texture psm, colour-source psm, safe-alpha) all agree and TEXA cannot change the alpha meaning.
//    Anything absent, unsupported or conflicting fails closed (the original texture is used).
#pragma once
#include <functional>
#include <algorithm>
#include <memory>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace ClaudeTexPack
{
enum : uint32_t
{
	PSM_CT32 = 0x00, PSM_CT24 = 0x01, PSM_CT16 = 0x02, PSM_CT16S = 0x0a, PSM_T8 = 0x13, PSM_T4 = 0x14
};

inline uint64_t hash64(const uint8_t *b, size_t n)
{
	uint64_t h = 0xcbf29ce484222325ull;
	size_t words = n / 8;
	for (size_t i = 0; i < words; i++)
	{
		uint64_t w;
		memcpy(&w, b + i * 8, 8);
		h ^= w;
		h *= 0x100000001b3ull;
		h ^= h >> 29;
	}
	for (size_t i = words * 8; i < n; i++)
		h = (h ^ b[i]) * 0x100000001b3ull;
	return h;
}

// The only TCC-dependent acceptance is a CT24-uploaded palette (sampled as CT32) whose alpha is ignored (TCC=0).
// For exactly those textures the caller keeps the real TCC in paraLLEl-GS's texture descriptor, so the render-pass
// memo, the page-tracker cache and the last-descriptor fast path all hold separate TCC=0 / TCC=1 entries.
inline bool tcc_segregated(bool active, bool palette_psm, uint32_t original_cpsm)
{
	return active && palette_psm && original_cpsm == PSM_CT24;
}

enum Reason : uint32_t
{
	R_REPLACED, R_NOT_LONG_TERM, R_SSAA, R_RECT, R_UNSUPPORTED_PSM, R_NO_UPLOAD, R_UPLOAD_LAYOUT, R_NOT_IN_INDEX,
	R_DIMS, R_CLUT_UNKNOWN, R_CLUT_CSA_CSM, R_CLUT_FORMAT, R_CLUT_MISMATCH, R_AMBIGUOUS, R_NO_IDENTITY,
	R_IDENTITY_CONFLICT, R_UNSAFE_ALPHA, R_FORMAT_MISMATCH, R_TEXA, R_LOAD_FAILED, R_COUNT
};

inline const char *reason_name(uint32_t r)
{
	static const char *n[R_COUNT] = {
		"replaced", "not_long_term", "ssaa", "rect", "unsupported_psm", "no_upload", "upload_layout", "not_in_index",
		"dims", "clut_unknown", "clut_csa_csm", "clut_format", "clut_mismatch", "ambiguous", "no_identity",
		"identity_conflict", "unsafe_alpha", "format_mismatch", "texa", "load_failed" };
	return r < R_COUNT ? n[r] : "?";
}

// Why a record disappeared / was never made (bounded evidence for no_upload).
enum Event : uint32_t
{
	E_FORGET_UPLOAD, E_FORGET_COPY, E_FORGET_RT, E_FORGET_HOSTVRAM, E_FORGET_FULLVRAM, E_UPLOAD_REJECTED_RP,
	E_UPLOAD_INCOMPLETE, E_UPLOAD_TOO_LARGE, E_UPLOAD_RECORDED, E_COUNT
};

inline const char *event_name(uint32_t e)
{
	static const char *n[E_COUNT] = { "forget_upload", "forget_copy", "forget_rt", "forget_hostvram", "forget_fullvram",
	                                  "upload_rejected_rp", "upload_incomplete", "upload_too_large", "upload_recorded" };
	return e < E_COUNT ? n[e] : "?";
}

// Why the CLUT buffer identity is unknown (bounded diagnostics for clut_unknown).
enum ClutWhy : uint32_t
{
	W_OK, W_NEVER_LOADED, W_NO_RECORD, W_LAYOUT, W_SHAPE, W_NOT_QUALIFIED, W_COMPOUND_DBW, W_FORWARDED, W_CSA_CSM,
	W_CPSM, W_CLOBBERED_BY_T8, W_CLOBBERED_BY_T4, W_CLOBBERED_OTHER, W_T8_PARTIAL_16X2, W_COUNT
};

inline const char *clut_why_name(uint32_t w)
{
	static const char *n[W_COUNT] = { "ok", "never_loaded", "no_record_at_cbp", "record_layout", "shape", "not_qualified",
	                                  "compound_dbw", "forwarded", "csa_csm", "cpsm", "clobbered_by_t8", "clobbered_by_t4",
	                                  "clobbered_other", "t8_partial_16x2" };
	return w < W_COUNT ? n[w] : "?";
}

struct Identity
{
	uint32_t tex_psm = 0, color_psm = 0;
	bool safe = false, conflict = false;
};

struct Entry
{
	uint64_t raw = 0, clut = 0;
	uint32_t w = 0, h = 0, hw = 0, hh = 0;
	std::string blob;
	bool has_id = false;
	Identity id;
};

struct PageSpan
{
	uint32_t base = 0, width = 1, height = 1, stride = 0, block_mask = UINT32_MAX;
};

// (page, block mask) pairs, one per touched page.
using Footprint = std::vector<std::pair<uint32_t, uint32_t>>;

struct Upload
{
	uint64_t hash = 0;
	uint32_t dbp = 0, dbw = 0, dpsm = 0, dsax = 0, dsay = 0, rrw = 0, rrh = 0;
};

struct Query
{
	uint32_t tbp0 = 0, tbw = 0, psm = 0, tw = 0, th = 0;
	// Original game TEX0 / TEXA (not the normalised cache descriptor).
	uint32_t tcc = 1, cpsm = 0, csm = 0, csa = 0;
	uint32_t ta0 = 0, ta1 = 0x80, aem = 0;
	// paraLLEl-GS's sanitised CLUT format (CT24 -> CT32) and whether the descriptor kept TCC (tcc_segregated()).
	uint32_t sampled_cpsm = 0;
	bool tcc_in_cache_key = false;
	uint32_t rect_x = 0, rect_y = 0, rect_w = 0, rect_h = 0, levels = 1, samples = 1;
	bool long_term = true;
};

struct Decision
{
	Reason reason = R_NO_UPLOAD;
	const Entry *entry = nullptr;
	uint64_t raw = 0, clut = 0;
	// clut_unknown details: why, the CBP of the last load into that slot, and the hash found there (if any).
	uint32_t clut_why = W_OK, clut_cbp = 0;
	uint64_t clut_seen = 0;
	// For clobbered_by_t8 / t8_partial_16x2: what the T8 load actually read at its CBP.
	bool load_has_record = false, load_qualified = false;
	uint32_t load_rrw = 0, load_rrh = 0, load_dpsm = 0, load_dbw = 0;
};

static inline bool valid_blob_name(const std::string &s)
{
	if (s.empty() || s.size() > 200 || s[0] == '.')
		return false;
	for (char c : s)
		if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.'))
			return false;
	auto ends = [&](const char *x) { size_t n = strlen(x); return s.size() > n && s.compare(s.size() - n, n, x) == 0; };
	return ends(".rgba") || ends(".png");
}

class Core
{
public:
	void reset(uint32_t num_pages)
	{
		pages_mask = 1;
		while (pages_mask < num_pages)
			pages_mask <<= 1;
		page_keys.assign(pages_mask, {});
		rp_mask.assign(pages_mask, 0);
		rp_pages.clear();
		num_pages_pow2 = pages_mask;
		pages_mask -= 1;
		records.clear();
		last_event.clear();
		active = false;
		t8 = t4 = {};
		std::fill(std::begin(counts), std::end(counts), 0);
		std::fill(std::begin(events), std::end(events), 0);
	}

	// Index (7 fields) + identity sidecar (7 fields). Both are required; returns number of usable entries.
	size_t load(const std::string &index_path, const std::string &identity_path, std::string *err)
	{
		std::unordered_map<std::string, Identity> ids;
		FILE *f = fopen(identity_path.c_str(), "r");
		if (!f)
		{
			if (err) *err = "missing identity sidecar";
			return 0;
		}
		unsigned long long raw, clut;
		unsigned w, h, a, b, c;
		while (fscanf(f, "%llx %llx %u %u %u %u %u", &raw, &clut, &w, &h, &a, &b, &c) == 7)
		{
			Identity id;
			id.tex_psm = a;
			id.color_psm = b;
			id.safe = c == 1;
			auto k = key(raw, clut, w, h);
			auto it = ids.find(k);
			if (it == ids.end())
				ids.emplace(k, id);
			else if (it->second.tex_psm != id.tex_psm || it->second.color_psm != id.color_psm || it->second.safe != id.safe)
				it->second.conflict = true;
		}
		fclose(f);

		f = fopen(index_path.c_str(), "r");
		if (!f)
		{
			if (err) *err = "missing index";
			return 0;
		}
		char blob[256];
		unsigned hw, hh;
		size_t n = 0;
		while (fscanf(f, "%llx %llx %u %u %u %u %255s", &raw, &clut, &w, &h, &hw, &hh, blob) == 7)
		{
			Entry e;
			e.raw = raw; e.clut = clut; e.w = w; e.h = h; e.hw = hw; e.hh = hh; e.blob = blob;
			if (!valid_blob_name(e.blob) || !w || !h || !hw || !hh || w > 1024 || h > 1024 || hw > 4096 || hh > 4096 ||
			    hw % w || hh % h || hw / w != hh / h || hw / w > 8)
			{
				bad_lines++;
				continue;
			}
			auto it = ids.find(key(raw, clut, w, h));
			if (it != ids.end())
			{
				e.has_id = true;
				e.id = it->second;
			}
			index[e.raw].push_back(std::move(e));
			n++;
		}
		fclose(f);
		active = n != 0;
		return n;
	}

	// Optional qualification sidecar compound-cluts.txt: runtime_hash source_cpsm exact_upload_bytes.
	// Conflicting rows for the same hash disable it.
	size_t load_compound_cluts(const std::string &path)
	{
		FILE *f = fopen(path.c_str(), "r");
		if (!f)
			return 0;
		unsigned long long h;
		unsigned cpsm, bytes;
		size_t n = 0;
		while (fscanf(f, "%llx %u %u", &h, &cpsm, &bytes) == 3)
			add_compound_clut(h, cpsm, bytes), n++;
		fclose(f);
		return n;
	}

	void add_compound_clut(uint64_t runtime_hash, uint32_t cpsm, uint32_t bytes)
	{
		auto it = compound.find(runtime_hash);
		if (it == compound.end())
			compound[runtime_hash] = { cpsm, bytes, false };
		else if (it->second.cpsm != cpsm || it->second.bytes != bytes)
			it->second.conflict = true;
	}

	// Tests add entries directly; production entries come from load(). Either way the core becomes active.
	void add_entry(Entry e)
	{
		index[e.raw].push_back(std::move(e));
		active = true;
	}

	// PS2_PORT_TEX_PACK_T8_COMPOUND: a PSMT8 CLUT load of an exactly qualified 16x2 compound upload makes the
	// T4 (CSA 0) view known; the T8 palette stays unknown. Off by default.
	void set_t8_compound(bool on) { t8_compound = on; }

	// Hooks are no-ops (no hashing or page tracking) unless a pack is loaded.
	bool is_active() const { return active; }

	Footprint footprint(const PageSpan &s) const
	{
		Footprint fp;
		if (page_keys.empty())
			return fp;
		for (uint32_t y = 0; y < s.height; y++)
			for (uint32_t x = 0; x < s.width; x++)
				merge(fp, (s.base + y * s.stride + x) & pages_mask, s.block_mask);
		return fp;
	}

	// Host->local upload, committed after paraLLEl-GS's hazard handling for this flush. The written blocks are always
	// forgotten; the upload is recorded only when complete (the final flush of a transfer, even after earlier partial
	// flushes) and not drawn over by the current pass.
	void on_host_upload(const Upload &u, const Footprint &fp, const uint8_t *data, size_t bytes, bool complete)
	{
		on_host_upload(u, std::make_shared<const Footprint>(fp), data, bytes, complete);
	}

	// The footprint is immutable and may be shared with the footprint cache; records keep a reference, no copy.
	void on_host_upload(const Upload &u, const std::shared_ptr<const Footprint> &fpp, const uint8_t *data, size_t bytes,
	                    bool complete)
	{
		if (!active || !fpp)
			return;
		const Footprint &fp = *fpp;
		forget(fp, E_FORGET_UPLOAD);
		if (!complete) { events[E_UPLOAD_INCOMPLETE]++; return; }
		if (!data || bytes == 0 || bytes > 4u * 1024u * 1024u) { events[E_UPLOAD_TOO_LARGE]++; return; }
		if (overlaps_rp(fp)) { events[E_UPLOAD_REJECTED_RP]++; note(u.dbp, E_UPLOAD_REJECTED_RP); return; }
		erase_record(u.dbp);
		Record r;
		r.u = u;
		r.u.hash = hash64(data, bytes);
		r.fp = fpp;
		for (auto &p : fp)
			page_keys[p.first & pages_mask].push_back(u.dbp);
		const uint64_t raw_hash = r.u.hash;
		records[u.dbp] = std::move(r);
		events[E_UPLOAD_RECORDED]++;
		// Claude 2026-10-19 (WORKLOG 127): an upload is the earliest point a replacement can be known to be
		// needed (draws come later); the plugin starts decoding its blobs here on worker threads.
		if (on_upload_hash) on_upload_hash(raw_hash);
	}
	std::function<void(uint64_t)> on_upload_hash;

	void on_pages_written(const Footprint &fp, bool render_target, Event cause)
	{
		if (!active)
			return;
		forget(fp, cause);
		if (render_target)
			for (auto &p : fp)
			{
				uint32_t page = p.first & pages_mask;
				if (!rp_mask[page])
					rp_pages.push_back(page);
				rp_mask[page] |= p.second;
			}
	}

	void on_render_pass_flushed()
	{
		for (uint32_t page : rp_pages)
			rp_mask[page] = 0;
		rp_pages.clear();
	}

	void on_bytes_written(size_t offset, size_t size, uint32_t page_size)
	{
		if (!active || !size)
			return;
		PageSpan s;
		s.base = uint32_t(offset / page_size);
		s.width = uint32_t((offset + size - 1) / page_size) - s.base + 1;
		const bool full = s.width >= num_pages_pow2;
		forget(footprint(s), full ? E_FORGET_FULLVRAM : E_FORGET_HOSTVRAM);
	}

	// The GS loads the CLUT buffer from CBP (TEX0 CLD). Capture which recognised palette is now in the buffer.
	// cpsm is the game's original TEX0.CPSM. forwarded = paraLLEl-GS rewrote the load to read another address.
	void on_clut_load(uint32_t psm, uint32_t cpsm, uint32_t csa, uint32_t csm, uint32_t cbp, bool forwarded = false)
	{
		if (!active)
			return;
		Clut c;
		c.cbp = cbp;
		auto it = records.find(cbp);
		// Diagnostics of what this load reads, captured before any slot is replaced or cleared.
		if (it != records.end())
		{
			const Upload &u = it->second.u;
			c.load_has_record = true;
			c.load_rrw = u.rrw;
			c.load_rrh = u.rrh;
			c.load_dpsm = u.dpsm;
			c.load_dbw = u.dbw;
			c.load_qualified = compound_qualified(u);
			c.seen = u.hash;
		}
		if (psm != PSM_T8 && psm != PSM_T4) c.why = W_SHAPE;
		else if (csa != 0 || csm != 0) c.why = W_CSA_CSM;
		else if (forwarded) c.why = W_FORWARDED;
		else if (it == records.end()) c.why = W_NO_RECORD;
		else if (cpsm != PSM_CT32 && cpsm != PSM_CT24 && cpsm != PSM_CT16 && cpsm != PSM_CT16S) c.why = W_CPSM;
		else
		{
			const Upload &u = it->second.u;
			c.seen = u.hash;
			// CSM1 read layouts proven against the TIM2 decoder (tests/clut_tests.cpp).
			const bool t8_ok = psm == PSM_T8 && u.rrw == 16 && u.rrh == 16;
			const bool t4_16 = psm == PSM_T4 && u.rrw == 8 && u.rrh == 2;
			const bool t4_32 = psm == PSM_T4 && u.rrw == 16 && u.rrh == 2;
			if (u.dsax != 0 || u.dsay != 0 || u.dpsm != cpsm) c.why = W_LAYOUT;
			else if (!(t8_ok || t4_16 || t4_32)) c.why = W_SHAPE;
			else if (t4_32)
			{
				// Compound 32-entry palette: only explicitly qualified runtime palettes.
				auto q = compound.find(u.hash);
				const uint32_t bytes = u.dpsm == PSM_CT24 ? 96u : u.dpsm == PSM_CT32 ? 128u : 0u;
				if (q == compound.end() || q->second.conflict || q->second.cpsm != u.dpsm || q->second.bytes != bytes || !bytes)
					c.why = W_NOT_QUALIFIED;
				else if (u.dbw != 1)
					c.why = W_COMPOUND_DBW;
				else
					c.why = W_OK;
			}
			else
				c.why = W_OK;
			if (c.why == W_OK)
			{
				c.valid = true;
				c.hash = u.hash;
				c.cpsm = cpsm;
			}
		}
		auto clobber = [&](Clut &slot, uint32_t why) {
			slot = {};
			slot.why = why;
			slot.cbp = cbp;
			slot.seen = c.seen;
			slot.load_has_record = c.load_has_record;
			slot.load_qualified = c.load_qualified;
			slot.load_rrw = c.load_rrw;
			slot.load_rrh = c.load_rrh;
			slot.load_dpsm = c.load_dpsm;
			slot.load_dbw = c.load_dbw;
		};
		if (psm == PSM_T8 && csa == 0)
		{
			// A T8 CSM1 load reads CLUT entries 0-15 from texels (0..7, 0..1) at CBP. When CBP holds an exactly
			// qualified 16x2 compound upload, those 16 colours are that palette's compound view (tests/clut_tests.cpp);
			// entries 16-255 come from unrelated VRAM, so the T8 palette stays unknown.
			const bool partial = t8_compound && csm == 0 && !forwarded && it != records.end() &&
			                     it->second.u.dsax == 0 && it->second.u.dsay == 0 && it->second.u.dpsm == cpsm &&
			                     it->second.u.dbw == 1 && compound_qualified(it->second.u);
			if (partial)
			{
				Clut t4c = c;
				t4c.valid = true;
				t4c.why = W_OK;
				t4c.hash = it->second.u.hash;
				t4c.cpsm = cpsm;
				t4 = t4c;
				clobber(t8, W_T8_PARTIAL_16X2);
			}
			else
			{
				t8 = c;
				clobber(t4, W_CLOBBERED_BY_T8);   // entries 0-15 now belong to the 256-colour palette
			}
		}
		else if (psm == PSM_T4 && csa == 0)
		{
			t4 = c;
			clobber(t8, W_CLOBBERED_BY_T4);   // the 256-colour palette lost entries 0-15
		}
		else
		{
			clobber(t8, W_CLOBBERED_OTHER);
			if (psm != PSM_T4)
				clobber(t4, W_CLOBBERED_OTHER);
		}
	}

	Decision decide(const Query &q)
	{
		Decision d = decide_inner(q);
		counts[d.reason]++;
		return d;
	}

	// Last event recorded for a DBP (bounded), for no_upload attribution.
	const char *last_event_for(uint32_t dbp) const
	{
		auto it = last_event.find(dbp);
		return it == last_event.end() ? "none" : event_name(it->second);
	}

	uint64_t counts[R_COUNT] = {};
	uint64_t events[E_COUNT] = {};
	size_t bad_lines = 0;

	// Claude 2026-10-19 (WORKLOG 127): read-only index view for the plugin's decode prefetch.
	const std::vector<Entry> *entries_for(uint64_t raw) const { auto it = index.find(raw); return it == index.end() ? nullptr : &it->second; }
private:
	struct Record
	{
		Upload u;
		std::shared_ptr<const Footprint> fp;
	};
	struct Clut
	{
		bool valid = false;
		uint64_t hash = 0;
		uint32_t cpsm = 0;
		uint32_t why = W_NEVER_LOADED, cbp = 0;
		uint64_t seen = 0;
		// What the most recent load into/over this slot read at its CBP (kept across clobbering).
		bool load_has_record = false, load_qualified = false;
		uint32_t load_rrw = 0, load_rrh = 0, load_dpsm = 0, load_dbw = 0;
	};
	bool t8_compound = false;

	bool compound_qualified(const Upload &u) const
	{
		auto q = compound.find(u.hash);
		const uint32_t bytes = u.dpsm == PSM_CT24 ? 96u : u.dpsm == PSM_CT32 ? 128u : 0u;
		return u.rrw == 16 && u.rrh == 2 && q != compound.end() && !q->second.conflict && q->second.cpsm == u.dpsm &&
		       q->second.bytes == bytes && bytes != 0;
	}

	uint32_t pages_mask = 0, num_pages_pow2 = 0;
	bool active = false;
	std::unordered_map<uint64_t, std::vector<Entry>> index;
	std::unordered_map<uint32_t, Record> records;
	std::vector<std::vector<uint32_t>> page_keys;
	std::vector<uint32_t> rp_mask;    // per page: union of blocks drawn by the current render pass
	std::vector<uint32_t> rp_pages;   // pages with a non-zero rp_mask (for O(touched) clearing)
	std::unordered_map<uint32_t, uint32_t> last_event;   // bounded to 4096 DBPs
	struct Compound { uint32_t cpsm, bytes; bool conflict; };
	std::unordered_map<uint64_t, Compound> compound;
	Clut t8, t4;

	static std::string key(unsigned long long raw, unsigned long long clut, unsigned w, unsigned h)
	{
		char b[64];
		snprintf(b, sizeof(b), "%016llx%016llx%u_%u", raw, clut, w, h);
		return b;
	}

	static void merge(Footprint &fp, uint32_t page, uint32_t mask)
	{
		for (auto &p : fp)
			if (p.first == page)
			{
				p.second |= mask;
				return;
			}
		fp.push_back({ page, mask });
	}

	void note(uint32_t dbp, Event e)
	{
		if (last_event.size() < 4096 || last_event.count(dbp))
			last_event[dbp] = e;
	}

	bool overlaps_rp(const Footprint &fp) const
	{
		for (auto &p : fp)
			if (rp_mask[p.first & pages_mask] & p.second)
				return true;
		return false;
	}

	void erase_record(uint32_t dbp)
	{
		auto it = records.find(dbp);
		if (it == records.end())
			return;
		for (auto &p : *it->second.fp)
		{
			auto &v = page_keys[p.first & pages_mask];
			v.erase(std::remove(v.begin(), v.end(), dbp), v.end());
		}
		records.erase(it);
	}

	void forget(const Footprint &fp, Event cause)
	{
		std::vector<uint32_t> doomed;
		for (auto &w : fp)
		{
			const uint32_t page = w.first & pages_mask;
			for (uint32_t dbp : page_keys[page])
			{
				auto it = records.find(dbp);
				if (it == records.end())
					continue;
				for (auto &p : *it->second.fp)
					if ((p.first & pages_mask) == page && (p.second & w.second))
						doomed.push_back(dbp);
			}
		}
		for (uint32_t dbp : doomed)
		{
			if (records.count(dbp))
			{
				events[cause]++;
				note(dbp, cause);
			}
			erase_record(dbp);
		}
	}

	Decision decide_inner(const Query &q) const
	{
		Decision d;
		const uint32_t w = 1u << q.tw, h = 1u << q.th;
		if (!q.long_term) { d.reason = R_NOT_LONG_TERM; return d; }
		if (q.samples != 1) { d.reason = R_SSAA; return d; }
		if (q.rect_x || q.rect_y || q.rect_w != w || q.rect_h != h) { d.reason = R_RECT; return d; }
		const bool palette = q.psm == PSM_T8 || q.psm == PSM_T4;
		if (!palette && q.psm != PSM_CT32 && q.psm != PSM_CT24 && q.psm != PSM_CT16 && q.psm != PSM_CT16S)
		{ d.reason = R_UNSUPPORTED_PSM; return d; }

		auto rec = records.find(q.tbp0);
		if (rec == records.end()) { d.reason = R_NO_UPLOAD; return d; }
		const Upload &u = rec->second.u;
		d.raw = u.hash;
		if (u.dsax || u.dsay || u.dpsm != q.psm || u.dbw != q.tbw || u.rrw != w || u.rrh != h)
		{ d.reason = R_UPLOAD_LAYOUT; return d; }

		auto ix = index.find(u.hash);
		if (ix == index.end()) { d.reason = R_NOT_IN_INDEX; return d; }

		uint64_t want_clut = 0;
		if (palette)
		{
			const Clut &c = q.psm == PSM_T8 ? t8 : t4;
			if (!c.valid)
			{
				d.reason = R_CLUT_UNKNOWN;
				d.clut_why = c.why;
				d.clut_cbp = c.cbp;
				d.clut_seen = c.seen;
				d.load_has_record = c.load_has_record;
				d.load_qualified = c.load_qualified;
				d.load_rrw = c.load_rrw;
				d.load_rrh = c.load_rrh;
				d.load_dpsm = c.load_dpsm;
				d.load_dbw = c.load_dbw;
				return d;
			}
			if (q.csa || q.csm) { d.reason = R_CLUT_CSA_CSM; return d; }
			if (c.cpsm != q.cpsm) { d.reason = R_CLUT_FORMAT; return d; }
			want_clut = c.hash;
		}
		d.clut = want_clut;

		const Entry *pick = nullptr;
		bool dims_seen = false, ambiguous = false;
		for (auto &e : ix->second)
		{
			if (e.w != w || e.h != h)
				continue;
			dims_seen = true;
			if (e.clut != want_clut)
				continue;
			if (!pick)
				pick = &e;
			else if (pick->blob != e.blob || pick->has_id != e.has_id ||
			         (pick->has_id && (pick->id.tex_psm != e.id.tex_psm || pick->id.color_psm != e.id.color_psm ||
			                           pick->id.safe != e.id.safe)))
				ambiguous = true;
		}
		if (!dims_seen) { d.reason = R_DIMS; return d; }
		if (!pick) { d.reason = palette ? R_CLUT_MISMATCH : R_NOT_IN_INDEX; return d; }
		if (ambiguous) { d.reason = R_AMBIGUOUS; return d; }

		if (!pick->has_id) { d.reason = R_NO_IDENTITY; return d; }
		if (pick->id.conflict) { d.reason = R_IDENTITY_CONFLICT; return d; }
		if (!pick->id.safe) { d.reason = R_UNSAFE_ALPHA; return d; }
		const uint32_t color = palette ? q.cpsm : q.psm;
		if (pick->id.tex_psm != q.psm || pick->id.color_psm != color) { d.reason = R_FORMAT_MISMATCH; return d; }

		// Alpha meaning. The cached image is shared by every draw with the same descriptor, so alpha rules must hold
		// for all of them:
		//  - CT32 data/CLUT carries its own alpha bytes (identity: no source alpha above 0x80).
		//  - CT24/CT16 direct and CT16 CLUT alpha comes from TEXA (part of the cache key); the pack encodes 24-bit as
		//    0x80 and 16-bit as STP ? 0x80 : 0, so only that TEXA state is equivalent. Never relaxed by TCC.
		//  - CT24 CLUT: paraLLEl-GS reads it as CT32; RGB is the uploaded palette, alpha is whatever byte was in VRAM.
		//    Accepted only when the game samples with TCC=0 (alpha unused) AND TCC is part of the cache key.
		if (palette && color == PSM_CT24)
		{
			if (q.sampled_cpsm != PSM_CT32 || q.tcc != 0 || !q.tcc_in_cache_key) { d.reason = R_CLUT_FORMAT; return d; }
		}
		else if (color == PSM_CT24)
		{
			if (q.aem != 0 || q.ta0 != 0x80) { d.reason = R_TEXA; return d; }
		}
		else if (color == PSM_CT16 || color == PSM_CT16S)
		{
			if (q.aem != 0 || q.ta0 != 0 || q.ta1 != 0x80) { d.reason = R_TEXA; return d; }
		}
		else if (palette && q.sampled_cpsm != q.cpsm)
		{
			d.reason = R_CLUT_FORMAT;
			return d;
		}

		d.reason = R_REPLACED;
		d.entry = pick;
		return d;
	}
};
}
