#pragma once
#include <string_view>

namespace ps2_port {
// These libsd functions serialize stateful RPC, callbacks and transfer data.
// The generic host stubs do not implement that protocol completely. Preserve
// their original bodies as native compiled C++ until a replacement is qualified.
inline constexpr bool requires_compiled_sdk_body(std::string_view name) {
    return name == "sceSdRemote" || name == "sceSdRemoteInit";
}
}
