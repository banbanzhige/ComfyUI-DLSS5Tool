// SPDX-License-Identifier: GPL-3.0-only
#include "quality_gpu_output.h"
#include <dxgi1_4.h>

bool QualityGpuOutput::Prepare(ID3D11Device* device, const LUID& luid,
    UINT width, UINT height, DXGI_FORMAT format) noexcept {
    if (buffer && width == width_ && height == height_ && format == format_) return true;
    // Prepare is called before publishing any frame; previous use was ACKed.
    buffer = nullptr; texture11 = nullptr; texture12 = nullptr;
    sharedBuffer.reset();
    if (!device12) {
        winrt::com_ptr<IDXGIFactory4> factory;
        winrt::com_ptr<IDXGIAdapter> adapter;
        if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory4), factory.put_void())) ||
            FAILED(factory->EnumAdapterByLuid(luid, __uuidof(IDXGIAdapter), adapter.put_void())) ||
            FAILED(D3D12CreateDevice(adapter.get(), D3D_FEATURE_LEVEL_11_0,
                __uuidof(ID3D12Device), device12.put_void()))) return false;
        D3D12_COMMAND_QUEUE_DESC q{};
        q.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
        if (FAILED(device12->CreateCommandQueue(&q, __uuidof(ID3D12CommandQueue), queue.put_void())) ||
            FAILED(device12->CreateCommandAllocator(q.Type, __uuidof(ID3D12CommandAllocator), allocator.put_void())) ||
            FAILED(device12->CreateCommandList(0, q.Type, allocator.get(), nullptr,
                __uuidof(ID3D12GraphicsCommandList), list.put_void())) || FAILED(list->Close())) return false;
        winrt::com_ptr<ID3D11Device5> d5;
        if (FAILED(device->QueryInterface(__uuidof(ID3D11Device5), d5.put_void())) ||
            FAILED(d5->CreateFence(0, D3D11_FENCE_FLAG_SHARED, __uuidof(ID3D11Fence), fence11.put_void()))) return false;
        Handle handle;
        if (FAILED(fence11->CreateSharedHandle(nullptr, GENERIC_ALL, nullptr, &handle.value)) ||
            FAILED(device12->OpenSharedHandle(handle.value, __uuidof(ID3D12Fence), incoming.put_void())) ||
            FAILED(device12->CreateFence(0, D3D12_FENCE_FLAG_NONE, __uuidof(ID3D12Fence), complete.put_void()))) return false;
        event.reset(CreateEventW(nullptr, FALSE, FALSE, nullptr));
        if (!event.value) return false;
    }
    D3D11_TEXTURE2D_DESC desc{};
    desc.Width = width; desc.Height = height; desc.MipLevels = desc.ArraySize = desc.SampleDesc.Count = 1;
    desc.Format = format; desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
    desc.MiscFlags = D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_NTHANDLE;
    if (FAILED(device->CreateTexture2D(&desc, nullptr, texture11.put()))) return false;
    winrt::com_ptr<IDXGIResource1> resource;
    Handle textureHandle;
    if (FAILED(texture11->QueryInterface(__uuidof(IDXGIResource1), resource.put_void())) ||
        FAILED(resource->CreateSharedHandle(nullptr, GENERIC_ALL, nullptr, &textureHandle.value)) ||
        FAILED(device12->OpenSharedHandle(textureHandle.value, __uuidof(ID3D12Resource), texture12.put_void()))) return false;
    const auto textureDesc = texture12->GetDesc();
    device12->GetCopyableFootprints(&textureDesc, 0, 1, 0, &footprint, nullptr, nullptr, nullptr);
    size = uint64_t(footprint.Footprint.RowPitch) * height;
    D3D12_RESOURCE_DESC b{};
    b.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER; b.Width = size;
    b.Height = b.DepthOrArraySize = b.MipLevels = b.SampleDesc.Count = 1;
    b.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
    D3D12_HEAP_PROPERTIES heap{};
    heap.Type = D3D12_HEAP_TYPE_DEFAULT; heap.CreationNodeMask = heap.VisibleNodeMask = 1;
    if (FAILED(device12->CreateCommittedResource(&heap, D3D12_HEAP_FLAG_SHARED, &b,
        D3D12_RESOURCE_STATE_COMMON, nullptr, __uuidof(ID3D12Resource), buffer.put_void())) ||
        FAILED(device12->CreateSharedHandle(buffer.get(), nullptr, GENERIC_ALL, nullptr, &sharedBuffer.value))) return false;
    allocation = device12->GetResourceAllocationInfo(0, 1, &b).SizeInBytes;
    width_ = width; height_ = height; format_ = format;
    return true;
}

bool QualityGpuOutput::Publish(ID3D11DeviceContext* context) noexcept {
    if (!buffer || !list || !complete || !incoming || !event.value) return false;
    winrt::com_ptr<ID3D11DeviceContext4> c4;
    if (FAILED(context->QueryInterface(__uuidof(ID3D11DeviceContext4), c4.put_void()))) return false;
    const uint64_t next = ++value;
    if (FAILED(c4->Signal(fence11.get(), next))) return false;
    context->Flush();
    if (FAILED(queue->Wait(incoming.get(), next)) || FAILED(allocator->Reset()) ||
        FAILED(list->Reset(allocator.get(), nullptr))) return false;
    auto barrier = [](ID3D12Resource* r, D3D12_RESOURCE_STATES before, D3D12_RESOURCE_STATES after) {
        D3D12_RESOURCE_BARRIER b{}; b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b.Transition = {r, D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES, before, after};
        return b;
    };
    D3D12_RESOURCE_BARRIER begin[]{barrier(texture12.get(), D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_SOURCE),
        barrier(buffer.get(), D3D12_RESOURCE_STATE_COMMON, D3D12_RESOURCE_STATE_COPY_DEST)};
    list->ResourceBarrier(2, begin);
    D3D12_TEXTURE_COPY_LOCATION src{}, dst{};
    src.pResource = texture12.get(); src.Type = D3D12_TEXTURE_COPY_TYPE_SUBRESOURCE_INDEX;
    dst.pResource = buffer.get(); dst.Type = D3D12_TEXTURE_COPY_TYPE_PLACED_FOOTPRINT; dst.PlacedFootprint = footprint;
    list->CopyTextureRegion(&dst, 0, 0, 0, &src, nullptr);
    D3D12_RESOURCE_BARRIER end[]{barrier(texture12.get(), D3D12_RESOURCE_STATE_COPY_SOURCE, D3D12_RESOURCE_STATE_COMMON),
        barrier(buffer.get(), D3D12_RESOURCE_STATE_COPY_DEST, D3D12_RESOURCE_STATE_COMMON)};
    list->ResourceBarrier(2, end);
    if (FAILED(list->Close())) return false;
    ID3D12CommandList* commands[]{list.get()}; queue->ExecuteCommandLists(1, commands);
    if (FAILED(queue->Signal(complete.get(), next)) ||
        FAILED(complete->SetEventOnCompletion(next, event.value))) return false;
    // Existing export imports a completed buffer and ACKs consumption. This is
    // one bounded completion wait, NOT a CPU pixel readback or a new queue.
    return WaitForSingleObject(event.value, 10000) == WAIT_OBJECT_0 &&
        SUCCEEDED(device12->GetDeviceRemovedReason());
}

void QualityGpuOutput::Descriptor(uint64_t* values) const noexcept {
    values[0] = reinterpret_cast<uint64_t>(sharedBuffer.value);
    values[1] = allocation; values[2] = size; values[3] = footprint.Footprint.RowPitch;
}
