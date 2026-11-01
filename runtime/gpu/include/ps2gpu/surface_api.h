#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
// C boundary shared by producer/plugin and window consumer. A successful wait
// returns a retained IOSurface on macOS. Release its returned serial exactly
// once after consumer GPU completion. No new frame/timeout/unsupported: null.
int hwgs_frame_info(uint32_t* width, uint32_t* height, uint32_t* scale);
// Success owns one publication lease. Release it when its payload is retired;
// a window wait acquires a separate lease for the consumer GPU copy.
int hwgs_frame_surface(uint32_t sx, uint32_t sy, uint32_t dw, uint32_t dh,
                       uint32_t ow, uint32_t oh, uint64_t* serial);
void* hwgs_surface_wait(uint64_t* serial, uint32_t* width, uint32_t* height, int timeout_ms);
void hwgs_surface_release(uint64_t serial);
#ifdef __cplusplus
}
#endif
