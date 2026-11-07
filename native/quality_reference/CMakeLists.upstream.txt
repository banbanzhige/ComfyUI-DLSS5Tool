cmake_minimum_required(VERSION 3.24)
project(DLSS5Realtime LANGUAGES C CXX RC)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
add_compile_options(/EHsc /utf-8)
get_filename_component(ROOT "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
set(DEPS "${ROOT}/third_party/realtime")
set(MAGPIE "${DEPS}/magpie/src")
set(CORE "${MAGPIE}/Magpie.Core")
set(SDK "${ROOT}/third_party/NVIDIA-DLSS")
set(GENERATED "${CMAKE_BINARY_DIR}/generated")
file(MAKE_DIRECTORY "${GENERATED}/shaders")
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND "${Python3_EXECUTABLE}" -B "${ROOT}/scripts/prepare_realtime_build.py" "${GENERATED}"
  COMMAND_ERROR_IS_FATAL ANY)
file(GLOB CORE_SOURCES CONFIGURE_DEPENDS "${CORE}/*.cpp")
list(REMOVE_ITEM CORE_SOURCES "${CORE}/pch.cpp" "${CORE}/ScalingWindow.cpp"
  "${CORE}/EffectCompiler.cpp" "${CORE}/NgxD3D12Core.cpp" "${CORE}/DLSSNRFilter.cpp" "${CORE}/OverlayDrawer.cpp" "${CORE}/Renderer.cpp")
list(REMOVE_ITEM CORE_SOURCES "${CORE}/NativeEffectBackendFactory.cpp" "${CORE}/DLSSSRUpscaler.cpp" "${CORE}/GraphicsCaptureFrameSource.cpp" "${CORE}/DesktopDuplicationFrameSource.cpp")
list(REMOVE_ITEM CORE_SOURCES "${CORE}/ParameterInputHost.cpp")
set_source_files_properties("${GENERATED}/DLSSNRFilter.cpp" PROPERTIES COMPILE_DEFINITIONS MP_ENABLE_NATIVE_BACKEND_TIMING)
set_source_files_properties("${CORE}/SrcTracker.cpp" PROPERTIES COMPILE_OPTIONS "/FIpch.h;/FIWindowHelper.h")
file(GLOB SHARED_SOURCES "${MAGPIE}/Shared/*.cpp")
file(GLOB MUPARSER_SOURCES "${DEPS}/muparser/src/*.cpp")
list(FILTER MUPARSER_SOURCES EXCLUDE REGEX "muParserDLL.cpp$")
add_library(muparser STATIC ${MUPARSER_SOURCES})
target_include_directories(muparser PUBLIC "${DEPS}/muparser/include")
target_compile_definitions(muparser PUBLIC MUPARSER_STATIC)
add_library(imgui STATIC "${DEPS}/imgui/imgui.cpp" "${DEPS}/imgui/imgui_draw.cpp"
  "${DEPS}/imgui/imgui_tables.cpp" "${DEPS}/imgui/imgui_widgets.cpp")
target_include_directories(imgui PUBLIC "${DEPS}/imgui")
add_library(magpie_core STATIC ${CORE_SOURCES} ${SHARED_SOURCES} "${GENERATED}/ScalingWindow.cpp"
  "${GENERATED}/EffectCompiler.cpp" "${GENERATED}/NgxD3D12Core.cpp" "${GENERATED}/DLSSNRFilter.cpp" "${GENERATED}/OverlayDrawer.cpp" "${GENERATED}/Renderer.cpp" quality.cpp)
target_sources(magpie_core PRIVATE "${GENERATED}/NativeEffectBackendFactory.cpp" "${GENERATED}/DLSSSRUpscaler.cpp" "${GENERATED}/GraphicsCaptureFrameSource.cpp" "${GENERATED}/DesktopDuplicationFrameSource.cpp")
target_sources(magpie_core PRIVATE "${GENERATED}/ParameterInputHost.cpp")
target_include_directories(magpie_core PUBLIC "${CORE}" "${CORE}/include" "${MAGPIE}/Shared"
  "${GENERATED}" "${DEPS}/fmt/include" "${DEPS}/spdlog/include" "${DEPS}/phmap"
  "${DEPS}/yas/include" "${DEPS}/rapidhash" "${DEPS}/wil/include" "${DEPS}/nvapi"
  "${SDK}/include" "${DEPS}/nvof_headers" "${CMAKE_CURRENT_LIST_DIR}")
target_compile_definitions(magpie_core PUBLIC UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX
  WINRT_LEAN_AND_MEAN WINRT_NO_MODULE_LOCK WIL_SUPPRESS_EXCEPTIONS WIL_USE_STL=1
  FMT_HEADER_ONLY SPDLOG_FMT_EXTERNAL SPDLOG_WCHAR_FILENAMES SPDLOG_NO_EXCEPTIONS
  MP_MAJOR_VERSION=0 MP_MINOR_VERSION=1 MP_PATCH_VERSION=0
  MP_ENABLE_DLSS_SR MP_ENABLE_DLSSNR MP_ENABLE_DLSS_FRAME_GENERATION MP_ENABLE_NVIDIA_OPTICAL_FLOW
  _SILENCE_CXX20_IS_ALWAYS_EQUAL_DEPRECATION_WARNING)
target_compile_options(magpie_core PUBLIC /utf-8 /bigobj /Zc:__cplusplus /permissive- /EHsc)
target_precompile_headers(magpie_core PRIVATE "${CORE}/pch.h")
target_link_libraries(magpie_core PUBLIC imgui muparser
  "${SDK}/lib/Windows_x86_64/x64/nvsdk_ngx_s.lib"
  d3d11 d3d12 dxgi d3dcompiler dxguid windowsapp runtimeobject
  user32 gdi32 dwmapi shell32 ole32 oleaut32 imagehlp comctl32 shlwapi
  magnification bcp47mrm shcore uxtheme dcomp winmm windowscodecs bcrypt version advapi32)
set(SDK_BIN "C:/Program Files (x86)/Windows Kits/10/bin/10.0.26100.0/x64" CACHE PATH "Windows SDK compiler tools")
add_custom_command(OUTPUT "${GENERATED}/shaders/VeyraResidualCS.h"
  COMMAND "${SDK_BIN}/fxc.exe" /nologo /O3 /T cs_5_0 /E main /Vn VeyraResidualCS
    /I "${DEPS}/veyra/shaders" /Fh "${GENERATED}/shaders/VeyraResidualCS.h" "${GENERATED}/VeyraResidual.hlsl"
  DEPENDS "${GENERATED}/VeyraResidual.hlsl" "${DEPS}/veyra/shaders/NrProtection.hlsli" VERBATIM)
target_sources(magpie_core PRIVATE "${GENERATED}/shaders/VeyraResidualCS.h")
foreach(SHADER DuplicateFrameCS ImGuiImplPS ImGuiImplVS MaskedCursorPS MonochromeCursorPS SimplePS SimpleVS)
  if(SHADER MATCHES "CS$")
    set(PROFILE cs_5_0)
  elseif(SHADER MATCHES "VS$")
    set(PROFILE vs_5_0)
  else()
    set(PROFILE ps_5_0)
  endif()
  add_custom_command(OUTPUT "${GENERATED}/shaders/${SHADER}.h"
    COMMAND "${SDK_BIN}/fxc.exe" /nologo /O3 /T ${PROFILE} /E main
      /Vn ${SHADER} /Fh "${GENERATED}/shaders/${SHADER}.h" "${CORE}/shaders/${SHADER}.hlsl"
    DEPENDS "${CORE}/shaders/${SHADER}.hlsl" VERBATIM)
  target_sources(magpie_core PRIVATE "${GENERATED}/shaders/${SHADER}.h")
endforeach()
if(NOT EXISTS "${ROOT}/assets/app.ico")
  message(FATAL_ERROR "Missing required realtime application icon")
endif()
configure_file(app_icon.rc.in "${GENERATED}/app_icon.rc" @ONLY)
add_executable(dlss5_realtime_host host.cpp guidance.cpp app.manifest "${GENERATED}/app_icon.rc")
target_link_libraries(dlss5_realtime_host PRIVATE magpie_core)
target_include_directories(dlss5_realtime_host PRIVATE "${DEPS}/json/single_include")
target_compile_options(dlss5_realtime_host PRIVATE "/FI${GENERATED}/standalone_paths.h")
set(REALTIME_OUTPUT_DIR "${ROOT}/runtime/realtime" CACHE PATH "Host output (candidate directory for validation)")
set_target_properties(dlss5_realtime_host PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${REALTIME_OUTPUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_DEBUG "${REALTIME_OUTPUT_DIR}")
enable_testing()
add_executable(realtime_hdr_residual_tests hdr_residual_tests.cpp)
target_compile_definitions(realtime_hdr_residual_tests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN
  HDR_TEST_SHADER=L"${CMAKE_CURRENT_LIST_DIR}/hdr_residual_test.hlsl")
target_link_libraries(realtime_hdr_residual_tests PRIVATE d3d11 d3dcompiler windowsapp)
add_test(NAME realtime_hdr_residual_warp COMMAND realtime_hdr_residual_tests)
add_test(NAME realtime_hdr_residual_gpu COMMAND realtime_hdr_residual_tests hardware)
add_executable(realtime_test_source EXCLUDE_FROM_ALL "${ROOT}/scripts/realtime_test_source.cpp")
target_include_directories(realtime_test_source PRIVATE "${DEPS}/json/single_include")
target_link_libraries(realtime_test_source PRIVATE d3d11 dxgi d3dcompiler user32 shcore)
add_executable(realtime_hotkeys_tests hotkeys_tests.cpp)
target_include_directories(realtime_hotkeys_tests PRIVATE "${DEPS}/json/single_include")
target_compile_definitions(realtime_hotkeys_tests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(realtime_hotkeys_tests PRIVATE /UNDEBUG)
add_test(NAME realtime_hotkeys COMMAND realtime_hotkeys_tests)
add_executable(realtime_app_icon_tests app_icon_tests.cpp "${GENERATED}/app_icon.rc")
target_compile_options(realtime_app_icon_tests PRIVATE /UNDEBUG)
target_link_libraries(realtime_app_icon_tests PRIVATE user32 gdi32)
add_test(NAME realtime_app_icon COMMAND realtime_app_icon_tests)
add_executable(realtime_guidance_tests guidance_tests.cpp)
target_compile_options(realtime_guidance_tests PRIVATE /UNDEBUG)
add_test(NAME realtime_guidance COMMAND realtime_guidance_tests)
add_executable(realtime_telemetry_tests telemetry_tests.cpp)
target_compile_definitions(realtime_telemetry_tests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_compile_options(realtime_telemetry_tests PRIVATE /UNDEBUG)
add_test(NAME realtime_telemetry COMMAND realtime_telemetry_tests)
add_executable(realtime_quality_tests quality_tests.cpp quality.cpp "${GENERATED}/shaders/VeyraResidualCS.h")
target_include_directories(realtime_quality_tests PRIVATE "${GENERATED}")
target_compile_definitions(realtime_quality_tests PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(realtime_quality_tests PRIVATE d3d11 windowsapp)
add_test(NAME realtime_quality_gpu COMMAND realtime_quality_tests)

# Local media uses the same ResidualComposite shader through CPU-pointer shared
# memory.  This is an optional bridge; Python falls back to NumPy when the DLL
# is absent or device creation/dispatch fails.
add_library(dlss5_quality SHARED quality_bridge.cpp quality_gpu_output.cpp quality.cpp "${GENERATED}/shaders/VeyraResidualCS.h")
target_include_directories(dlss5_quality PRIVATE "${GENERATED}" "${CMAKE_CURRENT_LIST_DIR}")
target_compile_definitions(dlss5_quality PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(dlss5_quality PRIVATE d3d11 d3d12 dxgi windowsapp)
set(QUALITY_OUTPUT_DIR "${ROOT}/runtime" CACHE PATH "Local composer candidate output")
set_target_properties(dlss5_quality PROPERTIES
  # The realtime manifest intentionally rejects DLLs under runtime/realtime;
  # keep this optional local-media bridge beside the existing host runtimes.
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${QUALITY_OUTPUT_DIR}"
  RUNTIME_OUTPUT_DIRECTORY_DEBUG "${QUALITY_OUTPUT_DIR}")
add_executable(realtime_temporal_tests "${DEPS}/magpie/tests/DLSSNRTemporalTests.cpp")
target_include_directories(realtime_temporal_tests PRIVATE "${CORE}" "${CORE}/include")
target_compile_options(realtime_temporal_tests PRIVATE /UNDEBUG)
target_link_libraries(realtime_temporal_tests PRIVATE d3d11 d3dcompiler)
add_test(NAME realtime_temporal_warp COMMAND realtime_temporal_tests)
