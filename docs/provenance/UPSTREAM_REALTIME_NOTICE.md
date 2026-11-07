# Realtime source, modifications and distribution boundary

Current combined DLSS5Tool source is GPL-3.0-only; see the root LICENSE.
Existing MIT-licensed DLSS5Tool code retains its notice in
`licenses/DLSS5Tool-original-MIT.txt`. Earlier MIT grants are not revoked.

## Actual reuse

- Magpie / SAOG0721 Experimental contributors: commit
  `2c017d4be8311fac8584e2ff6187b1f03383bde1`, GPLv3.
  Build inputs are `src/Magpie.Core/*.cpp`, their headers, `src/Shared/*.cpp`,
  core shaders, `src/Effects/`, Chinese resw resources and `tests/DLSSNRTemporalTests.cpp`.
  Keep their embedded notices, including credits for independently licensed shaders.
- Likely7 / Veyra contributors: commit
  `6851c2775c978b999e263a9c427ff43461688aa0`, GPLv3.
  Directly compiled inputs: `shaders/NrResidualComposite.hlsl` and
  `shaders/NrProtection.hlsli`. Residual input support is a local shader adaptation.
  The reset/preset/transaction designs were also studied. Veyra's separate temporal
  shader is NOT stacked over Magpie's temporal backend. No RemotePlay, Chiaki,
  player, audio, decoder, capture-card or AGPL module is compiled here.
- NVIDIA OF API 5 declarations: individually MIT-licensed
  `nvOpticalFlowCommon.h` and `nvOpticalFlowD3D11.h`, mirrored by SlimRG/NvidiaOF5
  at `99e8d8eb8f269591e5e4bd5a7f3c248bbbc3a3b9`.
  Preserve the NVIDIA header copyright; the mirror's root Unlicense is NOT a
  replacement. No `nvofapi64.dll` is downloaded or redistributed; the installed
  display driver's System32 DLL is used.

Dependency commits and fetched ZIP SHA256 values are locked in `sources.lock.json`.
fmt (MIT), spdlog (MIT), imgui (MIT), nlohmann/json (MIT), parallel-hashmap
(Apache-2.0 plus bundled file notices), yas (Boost-1.0), muparser (BSD-2-Clause),
rapidhash (BSD-2-Clause), WIL (MIT and bundled notices), NVAPI (supplied NVIDIA
license) retain their original texts. Build staging collects these notices.
NVIDIA DLSS SDK comes from the repository's existing `third_party/NVIDIA-DLSS`,
under its own `LICENSE.txt`; SDK link-library and header hashes are in the build manifest.

## Local changes, no edits to downloaded source

`scripts/prepare_realtime_build.py` produces checked adapters under the single
task build directory. It fails if exact pinned patch sites no longer match.

| Original input | Adaptation |
| --- | --- |
| ScalingWindow.cpp / resources | Standalone resource lookup without packaged PRI |
| OverlayDrawer.cpp | Standalone Chinese language selection |
| EffectCompiler.cpp | Absolute trusted effect root |
| NgxD3D12Core.cpp / DLSSSRUpscaler.cpp | Trusted runtime root and NGX feature search |
| DLSSNRFilter.cpp | Trusted NR filename, edge-guided low-res residual, timing hooks |
| DLSSNRMultiPass.h | Final global residual/protection after temporal pass; fail closed; NVOF implicit motion |
| NativeEffectBackendFactory.cpp | Reject failed NR initialization instead of success-shaped passthrough |
| Renderer.cpp | Bounded metrics, actual parameter modes, static redraw, normal/below/above GPU scheduling |
| GraphicsCaptureFrameSource.cpp | Count discarded old WGC captures |
| DesktopDuplicationFrameSource.cpp | First-frame metadata handling, capture timestamps and diagnostic logging |
| SrcTracker.cpp | Forced include for standalone build's missing WindowHelper declaration |
| NrResidualComposite.hlsl | Accept controlled signed residual without double-applying global controls; local HDR luminance/chroma fine controls and explicit Rec.709/Rec.2100 weights |
| DLSSNRFilter.cpp CreateSharedTexture / shared fence pattern | Adapted in quality_gpu_output.cpp for the local bridge's single-slot D3D11 -> D3D12 shared output; reuse existing CUDA/NVENC import and ACK, not a new encoder |

Our C++ host, quality bridge, telemetry and tests are GPL-3.0-only.
The local GPU sink shader also implements the existing CPU PQ/HLG output
transfer formulas and explicit nearest-even half output quantization. Ordinary
CPU and neutral realtime composition do not enable that output conversion.
The Python control protocol contains settings/events, never per-frame pixel buffers.
Runtime DLLs are not patched, re-signed, downloaded or copied by this build.

## Rebuild

Windows 10 2004+ / Windows 11 x64; VS 2022 C++ tools and Windows SDK 10.0.26100.0.
Read `docs/development/REPOSITORY_HYGIENE.md`, then use the existing `.venv`:

```powershell
.venv/Scripts/python.exe -B scripts/fetch_realtime_sources.py magpie veyra fmt spdlog phmap yas imgui muparser rapidhash wil nvapi json nvof_headers
./scripts/build_realtime.ps1
```

The build uses existing NVIDIA SDK inputs, outputs `runtime/realtime`, and stages
notices plus a file-hashed `build-manifest.json`. The exact original trees remain
read-only. A corresponding-source distribution must include the pinned sources,
these adaptations, build scripts and notices, not just a link to this file.

## Publication gate

This is a local NVIDIA-enabled integration, NOT a cleared public binary release.
Magpie upstream expressly flags the GPL/NVIDIA combination as unresolved;
changing the top-level LICENSE or placing the host in another process does not
resolve it. `packaging_license_review_complete` remains false. Public packaging
must fail, not silently drop the realtime feature. Only an explicitly local
candidate may include the current runtime manifest and notices. No publish,
upload or push is authorized by the implementation request.

This inventory is engineering provenance, not legal advice or a vendor permission grant.
