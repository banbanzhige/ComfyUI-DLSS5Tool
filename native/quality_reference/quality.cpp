// SPDX-License-Identifier: GPL-3.0-only
#include "quality.h"
#include "shaders/VeyraResidualCS.h"
#include "telemetry.h"

namespace Realtime {
void ResidualComposite::ResetBindings() noexcept {
    result = nullptr;
    uav = nullptr;
    outputUav = nullptr;
    outputTexture = nullptr;
    for (auto& view : views) view = nullptr;
    textures = {};
}

void ResidualComposite::Reset() noexcept {
    ResetBindings();
    shader = nullptr;
    constants = nullptr;
    for (auto& slot : timing) {
        slot.disjoint = nullptr;
        slot.begin = nullptr;
        slot.end = nullptr;
        slot.pending = false;
    }
    timingCursor = 0;
}

bool ResidualComposite::EnsureTiming(ID3D11Device* device) noexcept {
    if (timing[0].begin) return true;
    if (!device) return false;
    const D3D11_QUERY_DESC timestamp{D3D11_QUERY_TIMESTAMP, 0};
    const D3D11_QUERY_DESC disjoint{D3D11_QUERY_TIMESTAMP_DISJOINT, 0};
    for (auto& slot : timing) {
        if (FAILED(device->CreateQuery(&disjoint, slot.disjoint.put())) ||
            FAILED(device->CreateQuery(&timestamp, slot.begin.put())) ||
            FAILED(device->CreateQuery(&timestamp, slot.end.put()))) {
            for (auto& failed : timing) {
                failed.disjoint = nullptr;
                failed.begin = nullptr;
                failed.end = nullptr;
                failed.pending = false;
            }
            return false;
        }
    }
    return true;
}

void ResidualComposite::PollTiming(ID3D11DeviceContext* context) noexcept {
    if (!context) return;
    for (auto& slot : timing) {
        if (!slot.pending) continue;
        D3D11_QUERY_DATA_TIMESTAMP_DISJOINT disjoint{};
        UINT64 begin = 0, end = 0;
        const auto ready = [&](ID3D11Query* query, void* data, UINT size) {
            return context->GetData(query, data, size, D3D11_ASYNC_GETDATA_DONOTFLUSH);
        };
        const HRESULT disjointResult = ready(slot.disjoint.get(), &disjoint, sizeof(disjoint));
        const HRESULT beginResult = ready(slot.begin.get(), &begin, sizeof(begin));
        const HRESULT endResult = ready(slot.end.get(), &end, sizeof(end));
        if (disjointResult == S_FALSE || beginResult == S_FALSE || endResult == S_FALSE) continue;
        slot.pending = false;
        if (FAILED(disjointResult) || FAILED(beginResult) || FAILED(endResult) ||
            disjoint.Disjoint || !disjoint.Frequency || end <= begin) continue;
        QualityGpu(double(end - begin) * 1000.0 / double(disjoint.Frequency));
    }
}

bool ResidualComposite::Draw(ID3D11Device* device, ID3D11DeviceContext* context,
    ID3D11Texture2D* base, ID3D11Texture2D* nrInput,
    ID3D11Texture2D* nrOutput, ID3D11Texture2D* output, bool hdr, bool applyControls, bool residualInput,
    const QualitySettings* localSettings) noexcept {
    if (!base || !nrInput || !nrOutput || !output) return false;
    if (!shader) {
        if (FAILED(device->CreateComputeShader(VeyraResidualCS, sizeof(VeyraResidualCS), nullptr, shader.put()))) return false;
        D3D11_BUFFER_DESC desc{};
        desc.ByteWidth = sizeof(QualitySettings); desc.Usage = D3D11_USAGE_DEFAULT;
        desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        if (FAILED(device->CreateBuffer(&desc, nullptr, constants.put()))) return false;
    }
    PollTiming(context);
    D3D11_TEXTURE2D_DESC desc{}, old{};
    output->GetDesc(&desc);
    const bool separateOutput = output != base && output != nrInput && output != nrOutput;
    bool directOutput = separateOutput && (desc.BindFlags & D3D11_BIND_UNORDERED_ACCESS);
    if (directOutput && (outputTexture != output || !outputUav)) {
        outputUav = nullptr;
        outputTexture = nullptr;
        if (SUCCEEDED(device->CreateUnorderedAccessView(output, nullptr, outputUav.put()))) {
            outputTexture = output;
        } else {
            directOutput = false;
        }
    }
    if (!directOutput) {
        outputUav = nullptr;
        outputTexture = nullptr;
        if (result) result->GetDesc(&old);
        if (!result || desc.Width != old.Width || desc.Height != old.Height || desc.Format != old.Format) {
            result = nullptr; uav = nullptr;
            desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
            desc.MiscFlags = 0; desc.CPUAccessFlags = 0; desc.Usage = D3D11_USAGE_DEFAULT;
            if (FAILED(device->CreateTexture2D(&desc, nullptr, result.put())) ||
                FAILED(device->CreateUnorderedAccessView(result.get(), nullptr, uav.put()))) return false;
        }
    }
    const std::array<ID3D11Texture2D*, 3> inputs{base, nrInput, nrOutput};
    for (size_t i = 0; i < 3; ++i) {
        if (inputs[i] != textures[i] || !views[i]) {
            views[i] = nullptr;
            if (FAILED(device->CreateShaderResourceView(inputs[i], nullptr, views[i].put()))) return false;
            textures[i] = inputs[i];
        }
    }
    auto settings = applyControls ? (localSettings ? *localSettings : GetQuality()) : QualitySettings{};
    settings.signedHdr = hdr ? 1.0f : 0.0f;
    settings.residualInput = residualInput ? 1.0f : 0.0f;
    context->UpdateSubresource(constants.get(), 0, nullptr, &settings, 0, 0);
    ID3D11ShaderResourceView* srvs[]{views[0].get(), views[1].get(), views[2].get()};
    auto* target = directOutput ? outputUav.get() : uav.get();
    auto* cb = constants.get();
    context->CSSetShader(shader.get(), nullptr, 0);
    context->CSSetShaderResources(0, 3, srvs);
    context->CSSetUnorderedAccessViews(0, 1, &target, nullptr);
    context->CSSetConstantBuffers(0, 1, &cb);
    const bool timingReady = EnsureTiming(device);
    TimingSlot* timingSlot = nullptr;
    if (timingReady) {
        auto& candidate = timing[timingCursor];
        if (!candidate.pending) {
            context->Begin(candidate.disjoint.get());
            context->End(candidate.begin.get());
            timingSlot = &candidate;
        }
    }
    context->Dispatch((desc.Width + 15) / 16, (desc.Height + 15) / 16, 1);
    if (timingSlot) {
        context->End(timingSlot->end.get());
        context->End(timingSlot->disjoint.get());
        timingSlot->pending = true;
        timingCursor = (timingCursor + 1) % timing.size();
    }
    ID3D11ShaderResourceView* empty[3]{};
    ID3D11UnorderedAccessView* emptyUav = nullptr;
    ID3D11Buffer* emptyBuffer = nullptr;
    context->CSSetShaderResources(0, 3, empty);
    context->CSSetUnorderedAccessViews(0, 1, &emptyUav, nullptr);
    context->CSSetConstantBuffers(0, 1, &emptyBuffer);
    context->CSSetShader(nullptr, nullptr, 0);
    if (!directOutput) context->CopyResource(output, result.get());
    return true;
}
}
