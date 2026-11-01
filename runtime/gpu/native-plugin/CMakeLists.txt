cmake_minimum_required(VERSION 3.20)
project(ps2_native_plugin C CXX)
# Candidate path; this creates no game window and installs over no existing app.
# The caller supplies the matching source runtime, never an old runtime archive.
set(PS2_RUNTIME_SOURCE "" CACHE PATH "Matching PS2Recomp ps2xRuntime source directory")
if(NOT EXISTS "${PS2_RUNTIME_SOURCE}/src/lib/gs/ps2_gs_memory.cpp")
  message(FATAL_ERROR "PS2_RUNTIME_SOURCE must contain the matching runtime source")
endif()
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(PS2GPU_BUILD_DISPLAY OFF CACHE BOOL "Native runtime owns the window" FORCE)
get_filename_component(GPU_ROOT "${CMAKE_CURRENT_LIST_DIR}/.." ABSOLUTE)
if(NOT TARGET ps2gpu)
  add_subdirectory("${GPU_ROOT}" "${CMAKE_CURRENT_BINARY_DIR}/gpu-core")
endif()
add_library(ps2_native_host STATIC
  "${GPU_ROOT}/host-adapter/gpu_bridge.cpp"
  "${GPU_ROOT}/host-adapter/hwgs_capi.cpp"
  "${GPU_ROOT}/host-adapter/host_services.cpp"
  "${GPU_ROOT}/host-adapter/host_defaults.cpp"
  "${GPU_ROOT}/host-model/src/gs.cpp"
  "${GPU_ROOT}/host-model/src/gs_draw.cpp"
  "${GPU_ROOT}/host-model/src/gs_memory.cpp"
  "${GPU_ROOT}/host-model/src/gs_thread.cpp"
  "${GPU_ROOT}/host-model/src/profile.cpp")
target_compile_features(ps2_native_host PUBLIC cxx_std_17)
target_compile_definitions(ps2_native_host PRIVATE PS2HOST_VULKAN=1)
target_include_directories(ps2_native_host PRIVATE
  "${GPU_ROOT}/host-model/include" "${GPU_ROOT}/host-adapter"
  "${GPU_ROOT}/host-adapter/texpack" "${GPU_ROOT}/third_party")
target_link_libraries(ps2_native_host PRIVATE ps2gpu)
add_library(ps2_native_plugin SHARED plugin.cpp "${PS2_RUNTIME_SOURCE}/src/lib/gs/ps2_gs_memory.cpp")
target_compile_features(ps2_native_plugin PRIVATE cxx_std_20)
target_include_directories(ps2_native_plugin PRIVATE "${CMAKE_CURRENT_LIST_DIR}" "${PS2_RUNTIME_SOURCE}/include")
target_link_libraries(ps2_native_plugin PRIVATE ps2_native_host)
set_target_properties(ps2_native_plugin PROPERTIES
  CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON
  OUTPUT_NAME local-native-gs-plugin MACOSX_RPATH ON INSTALL_NAME_DIR "@rpath")
