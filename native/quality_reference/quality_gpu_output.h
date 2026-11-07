// SPDX-License-Identifier: GPL-3.0-only
#pragma once
#include <d3d11_4.h>
#include <d3d12.h>
#include <winrt/base.h>
#include <cstdint>

// Shared texture/fence creation adapted from Magpie Experimental DLSSNRFilter
// CreateSharedTexture (2c017d4be8311fac8584e2ff6187b1f03383bde1, GPLv3).
// The exported buffer uses this project's existing D3D12/CUDA writer contract.
// One reusable slot: producer waits GPU completion; consumer ACK must precede
// the next dispatch. No CPU image mapping, no per-frame resource allocation.
class QualityGpuOutput final {
public:
    bool Prepare(ID3D11Device* device, const LUID& luid, UINT width, UINT height,
                 DXGI_FORMAT format) noexcept;
    bool Publish(ID3D11DeviceContext* context) noexcept;
    ID3D11Texture2D* Texture() const noexcept { return texture11.get(); }
    void Descriptor(uint64_t* values) const noexcept;
private:
    struct Handle {
        HANDLE value = nullptr;
        ~Handle() { reset(); }
        void reset(HANDLE next = nullptr) noexcept { if (value) CloseHandle(value); value = next; }
    };
    winrt::com_ptr<ID3D12Device> device12;
    winrt::com_ptr<ID3D12CommandQueue> queue;
    winrt::com_ptr<ID3D12CommandAllocator> allocator;
    winrt::com_ptr<ID3D12GraphicsCommandList> list;
    winrt::com_ptr<ID3D11Texture2D> texture11;
    winrt::com_ptr<ID3D12Resource> texture12, buffer;
    winrt::com_ptr<ID3D11Fence> fence11;
    winrt::com_ptr<ID3D12Fence> incoming, complete;
    Handle sharedBuffer, event;
    D3D12_PLACED_SUBRESOURCE_FOOTPRINT footprint{};
    uint64_t value = 0, allocation = 0, size = 0;
    UINT width_ = 0, height_ = 0;
    DXGI_FORMAT format_ = DXGI_FORMAT_UNKNOWN;
};
