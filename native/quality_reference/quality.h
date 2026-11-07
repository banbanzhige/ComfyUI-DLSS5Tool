// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <array>
#include <atomic>
#include <mutex>
#include <d3d11.h>
#include <winrt/base.h>

namespace Realtime {
// Matches Veyra's NrResidualComposite cbuffer. Rectangles are normalized to the
// preserved source, independent of NR's internal extent or output monitor DPI.
struct QualitySettings {
    float total = 1, darken = 1, brighten = 1, color = 1, luminance = 1;
    float protectionEnabled = 0, featherPixels = 12, signedHdr = 0;
    std::array<std::array<float, 4>, 4> regions{};
    // The residual controls are neutral for the realtime host (which already
    // applies them in its low-resolution pass), but are used by the local
    // media bridge where the shader receives the raw NR output directly.
    float residualInput = 0;
    float residualMultiplier = 1;
    float residualSaturation = 1;
    float residualLightness = 1;
    float shadowStructureMultiplier = 1;
    float reflectionGlowMultiplier = 1;
    float lumaRec2100 = 0; // Local PQ/HLG; realtime neutral/scRGB stays Rec.709.
    float outputTransfer = 0; // GPU sink only: 1 PQ, 2 HLG. CPU path stays linear.
};
static_assert(sizeof(QualitySettings) == 128);
inline std::mutex qualityMutex;
inline QualitySettings qualitySettings;
inline std::atomic<uint64_t> qualityRevision = 0, appliedQualityRevision = 0;
inline void SetQuality(QualitySettings settings) {
    std::lock_guard lock(qualityMutex); qualitySettings = settings; ++qualityRevision;
}
inline QualitySettings GetQuality() {
    std::lock_guard lock(qualityMutex); return qualitySettings;
}

// Owned and used by one NR backend thread. No CPU pixel transfer; resources
// are released only after the owning Magpie backend has joined.
class ResidualComposite {
public:
    bool Draw(ID3D11Device* device, ID3D11DeviceContext* context,
        ID3D11Texture2D* base, ID3D11Texture2D* nrInput,
        ID3D11Texture2D* nrOutput, ID3D11Texture2D* output, bool hdr,
        bool applyControls = true, bool residualInput = false,
        const QualitySettings* localSettings = nullptr) noexcept;
    void Reset() noexcept;
    // Extent/format changes invalidate views, not device-owned shader/timing.
    void ResetBindings() noexcept;
private:
    struct TimingSlot {
        winrt::com_ptr<ID3D11Query> disjoint;
        winrt::com_ptr<ID3D11Query> begin;
        winrt::com_ptr<ID3D11Query> end;
        bool pending = false;
    };
    bool EnsureTiming(ID3D11Device* device) noexcept;
    void PollTiming(ID3D11DeviceContext* context) noexcept;

    winrt::com_ptr<ID3D11ComputeShader> shader;
    winrt::com_ptr<ID3D11Buffer> constants;
    winrt::com_ptr<ID3D11Texture2D> result;
    winrt::com_ptr<ID3D11UnorderedAccessView> uav;
    winrt::com_ptr<ID3D11UnorderedAccessView> outputUav;
    ID3D11Texture2D* outputTexture = nullptr;
    std::array<winrt::com_ptr<ID3D11ShaderResourceView>, 3> views;
    std::array<ID3D11Texture2D*, 3> textures{};
    std::array<TimingSlot, 4> timing;
    size_t timingCursor = 0;
};
}
