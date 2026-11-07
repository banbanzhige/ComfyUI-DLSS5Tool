// SPDX-License-Identifier: GPL-3.0-only
// Small CPU-pointer bridge for the shared Veyra residual compute shader.
//
// The normal realtime host already owns D3D11 textures.  Local media goes
// through Python shared-memory frames instead, so this adapter deliberately
// keeps the public surface tiny: upload base/NR output, run the same shader,
// and read back one composed frame.  The Python caller keeps a context per
// worker thread and falls back to its reference implementation on any error.

#include "quality.h"
#include "quality_gpu_output.h"

#include <d3d11.h>
#include <dxgi1_4.h>
#include <winrt/base.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <new>
#include <memory>
#include <atomic>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")

namespace {
std::atomic<uint64_t> created_bridges{0}, destroyed_bridges{0}, retained_device_refs{0};

class Bridge final {
public:
    Bridge() noexcept { ++created_bridges; }
    ~Bridge() noexcept {
        // D3D11 defers destruction of objects referenced by the immediate
        // context. Explicitly unbind, release, and flush deferred destruction
        // before dropping the device, including rapid preview/session churn.
        if (context) context->ClearState();
        composite.Reset();
        gpu_output.reset();
        readback = nullptr; nr_texture = nullptr; base_texture = nullptr;
        if (context) context->Flush();
        context = nullptr;
        auto* last = device.detach();
        retained_device_refs = last ? last->Release() : 0;
        ++destroyed_bridges;
    }

    bool Initialize(const LUID* requested = nullptr) noexcept {
        // Same exact-LUID selection as host_v2/OpenAdapterByLuid. Never
        // substitute a different/default card for an explicit request.
        winrt::com_ptr<IDXGIAdapter> adapter;
        if (requested) {
            winrt::com_ptr<IDXGIFactory4> factory;
            if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory4), factory.put_void())) ||
                FAILED(factory->EnumAdapterByLuid(*requested, __uuidof(IDXGIAdapter), adapter.put_void())))
                return false;
        }
        const auto driver = adapter ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE;
        const D3D_FEATURE_LEVEL levels[] = {
            D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0,
        };
        HRESULT result = D3D11CreateDevice(
            adapter.get(), driver, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT,
            levels, ARRAYSIZE(levels), D3D11_SDK_VERSION,
            device.put(), nullptr, context.put());
        // Older Windows 10 drivers reject a feature-level list containing
        // 11_1 with E_INVALIDARG even though 11_0 is fully available.
        if (result == E_INVALIDARG) {
            device = nullptr;
            context = nullptr;
            const D3D_FEATURE_LEVEL fallback = D3D_FEATURE_LEVEL_11_0;
            result = D3D11CreateDevice(
                adapter.get(), driver, nullptr,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT,
                &fallback, 1, D3D11_SDK_VERSION,
                device.put(), nullptr, context.put());
        }
        if (FAILED(result)) return false;
        if (!device || !context) return false;
        winrt::com_ptr<IDXGIDevice> dxgi;
        winrt::com_ptr<IDXGIAdapter> actual;
        DXGI_ADAPTER_DESC desc{};
        if (FAILED(device->QueryInterface(__uuidof(IDXGIDevice), dxgi.put_void())) ||
            FAILED(dxgi->GetAdapter(actual.put())) || FAILED(actual->GetDesc(&desc))) return false;
        luid = desc.AdapterLuid;
        return !requested || (luid.HighPart == requested->HighPart && luid.LowPart == requested->LowPart);
    }

    LUID luid{};
    uint64_t cpu_readbacks = 0, gpu_frames = 0;

    bool PrepareGpu(int width, int height, int format_id, uint64_t* descriptor) noexcept {
        if (!descriptor || width <= 0 || height <= 0 || width > 16384 || height > 16384 ||
            format_id < 0 || format_id > 2) return false;
        if (!gpu_output) gpu_output.reset(new (std::nothrow) QualityGpuOutput());
        const auto format = format_id == 2 ? DXGI_FORMAT_R32G32B32A32_FLOAT :
            format_id == 1 ? DXGI_FORMAT_R16G16B16A16_FLOAT : DXGI_FORMAT_R8G8B8A8_UNORM;
        if (!gpu_output || !gpu_output->Prepare(device.get(), luid, width, height, format)) {
            gpu_output.reset(); return false;
        }
        gpu_output->Descriptor(descriptor);
        return true;
    }

    bool ComposeGpu(const void* base, const void* processed, int width, int height,
        int format_id, const float* values, int value_count) noexcept {
        if (!gpu_output || !Dispatch(base, processed, width, height, format_id, values, value_count,
                gpu_output->Texture()) || !gpu_output->Publish(context.get())) return false;
        ++gpu_frames;
        return true;
    }

    bool Compose(const void* base, const void* processed, void* output,
        int width, int height, int format_id, const float* values,
        int value_count) noexcept {
        if (!base || !processed || !output || !values || value_count < 32 ||
            width <= 0 || height <= 0 || width > 16384 || height > 16384 ||
            (format_id < 0 || format_id > 2)) return false;
        const DXGI_FORMAT format = format_id == 2 ? DXGI_FORMAT_R32G32B32A32_FLOAT
            : format_id == 1 ? DXGI_FORMAT_R16G16B16A16_FLOAT : DXGI_FORMAT_R8G8B8A8_UNORM;
        if (!EnsureTextures(static_cast<UINT>(width), static_cast<UINT>(height), format))
            return false;

        const size_t row_pitch = static_cast<size_t>(width) * (format_id == 2 ? 16u : format_id == 1 ? 8u : 4u);
        if (!EnsureReadback() || !Dispatch(base, processed, width, height, format_id, values, value_count, readback.get()))
            return false;

        D3D11_MAPPED_SUBRESOURCE mapped{};
        if (FAILED(context->Map(readback.get(), 0, D3D11_MAP_READ, 0, &mapped)))
            return false;
        auto* destination = static_cast<unsigned char*>(output);
        const auto* source = static_cast<const unsigned char*>(mapped.pData);
        for (UINT row = 0; row < static_cast<UINT>(height); ++row) {
            std::memcpy(destination + static_cast<size_t>(row) * row_pitch,
                source + static_cast<size_t>(row) * mapped.RowPitch, row_pitch);
        }
        context->Unmap(readback.get(), 0);
        ++cpu_readbacks;
        return true;
    }

private:
    bool Dispatch(const void* base, const void* processed, int width, int height, int format_id,
        const float* values, int value_count, ID3D11Texture2D* output) noexcept {
        if (!base || !processed || !output || !values || value_count < 32 || width <= 0 || height <= 0 ||
            width > 16384 || height > 16384 || format_id < 0 || format_id > 2) return false;
        const auto format = format_id == 2 ? DXGI_FORMAT_R32G32B32A32_FLOAT :
            format_id == 1 ? DXGI_FORMAT_R16G16B16A16_FLOAT : DXGI_FORMAT_R8G8B8A8_UNORM;
        if (!EnsureTextures(width, height, format)) return false;
        const UINT pitch = width * (format_id == 2 ? 16u : format_id == 1 ? 8u : 4u);
        context->UpdateSubresource(base_texture.get(), 0, nullptr, base, pitch, 0);
        context->UpdateSubresource(nr_texture.get(), 0, nullptr, processed, pitch, 0);
        Realtime::QualitySettings settings{};
        std::memcpy(&settings, values, std::min<size_t>(sizeof(settings), size_t(value_count)*sizeof(float)));
        return composite.Draw(device.get(), context.get(), base_texture.get(), base_texture.get(),
            nr_texture.get(), output, settings.signedHdr > 0.5f, true, false, &settings);
    }

    bool EnsureReadback() noexcept {
        if (readback) return true;
        D3D11_TEXTURE2D_DESC staging{};
        base_texture->GetDesc(&staging);
        staging.Usage = D3D11_USAGE_STAGING; staging.BindFlags = 0;
        staging.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        return SUCCEEDED(device->CreateTexture2D(&staging, nullptr, readback.put()));
    }

    bool EnsureTextures(UINT width, UINT height, DXGI_FORMAT format) noexcept {
        if (base_texture && width_ == width && height_ == height && format_ == format)
            return true;
        // D3D11 destruction is deferred. Retire old bindings and flush those
        // releases before allocating another extent; preserve device resources.
        context->ClearState();
        composite.ResetBindings();
        base_texture = nullptr;
        nr_texture = nullptr;
        readback = nullptr;
        context->Flush();

        D3D11_TEXTURE2D_DESC input{};
        input.Width = width;
        input.Height = height;
        input.MipLevels = 1;
        input.ArraySize = 1;
        input.Format = format;
        input.SampleDesc.Count = 1;
        input.Usage = D3D11_USAGE_DEFAULT;
        input.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        if (FAILED(device->CreateTexture2D(&input, nullptr, base_texture.put())) ||
            FAILED(device->CreateTexture2D(&input, nullptr, nr_texture.put()))) {
            base_texture = nullptr;
            nr_texture = nullptr;
            return false;
        }

        width_ = width;
        height_ = height;
        format_ = format;
        return true;
    }

    winrt::com_ptr<ID3D11Device> device;
    winrt::com_ptr<ID3D11DeviceContext> context;
    winrt::com_ptr<ID3D11Texture2D> base_texture;
    winrt::com_ptr<ID3D11Texture2D> nr_texture;
    winrt::com_ptr<ID3D11Texture2D> readback;
    Realtime::ResidualComposite composite;
    std::unique_ptr<QualityGpuOutput> gpu_output;
    UINT width_ = 0;
    UINT height_ = 0;
    DXGI_FORMAT format_ = DXGI_FORMAT_UNKNOWN;
};

} // namespace

// v2 adds exact adapter selection and linear HDR fine/global controls.
extern "C" __declspec(dllexport) int dlss5_quality_version() noexcept { return 3; }

extern "C" __declspec(dllexport) void dlss5_quality_lifetime(uint64_t* values) noexcept {
    if (!values) return;
    values[0]=created_bridges.load(); values[1]=destroyed_bridges.load();
    values[2]=retained_device_refs.load();
}

extern "C" __declspec(dllexport) int dlss5_quality_gpu_prepare(
    void* handle, int width, int height, int format_id, uint64_t* descriptor) noexcept {
    auto* bridge = static_cast<Bridge*>(handle);
    return bridge && bridge->PrepareGpu(width, height, format_id, descriptor) ? 1 : 0;
}

extern "C" __declspec(dllexport) int dlss5_quality_compose_gpu(void* handle,
    const void* base, const void* processed, int width, int height, int format_id,
    const float* values, int value_count) noexcept {
    auto* bridge = static_cast<Bridge*>(handle);
    return bridge && bridge->ComposeGpu(base, processed, width, height, format_id, values, value_count) ? 1 : 0;
}

extern "C" __declspec(dllexport) int dlss5_quality_stats(void* handle, uint64_t* readbacks, uint64_t* gpu_frames) noexcept {
    if (!handle || !readbacks || !gpu_frames) return 0;
    auto* bridge = static_cast<Bridge*>(handle);
    *readbacks = bridge->cpu_readbacks; *gpu_frames = bridge->gpu_frames;
    return 1;
}

extern "C" __declspec(dllexport) void* dlss5_quality_create() noexcept {
    auto* bridge = new (std::nothrow) Bridge();
    if (!bridge || !bridge->Initialize()) {
        delete bridge;
        return nullptr;
    }
    return bridge;
}

extern "C" __declspec(dllexport) void* dlss5_quality_create_on_adapter(
    int32_t luid_high, uint32_t luid_low) noexcept {
    const LUID requested{luid_low, luid_high};
    auto* bridge = new (std::nothrow) Bridge();
    if (!bridge || !bridge->Initialize(&requested)) {
        delete bridge;
        return nullptr;
    }
    return bridge;
}

extern "C" __declspec(dllexport) int dlss5_quality_adapter(
    void* handle, int32_t* high, uint32_t* low) noexcept {
    if (!handle || !high || !low) return 0;
    const auto* bridge = static_cast<Bridge*>(handle);
    *high = bridge->luid.HighPart;
    *low = bridge->luid.LowPart;
    return 1;
}

extern "C" __declspec(dllexport) void dlss5_quality_destroy(void* handle) noexcept {
    delete static_cast<Bridge*>(handle);
}

extern "C" __declspec(dllexport) int dlss5_quality_compose(
    void* handle, const void* base, const void* processed, void* output,
    int width, int height, int format_id, const float* values, int value_count) noexcept {
    auto* bridge = static_cast<Bridge*>(handle);
    return bridge && bridge->Compose(base, processed, output, width, height,
        format_id, values, value_count) ? 1 : 0;
}
