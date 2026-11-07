"""Generate standalone resource adapter from pinned Magpie source; no upstream edits."""
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MAGPIE = ROOT / "third_party/realtime/magpie"


def write_if_changed(path, content):
    if not path.is_file() or path.read_text(encoding='utf-8') != content:
        path.write_text(content, encoding='utf-8')


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError(f'Pinned source patch mismatch: {old[:100]}')
    return source.replace(old, new, 1)


def generate(output):
    output.mkdir(parents=True, exist_ok=True)
    shader = (ROOT / 'third_party/realtime/veyra/shaders/NrResidualComposite.hlsl').read_text(encoding='utf-8')
    shader = replace_once(
        shader,
        'float4 regions[4];}',
        'float4 regions[4];float residualInput;float residualMultiplier;'
        'float residualSaturation;float residualLightness;'
        'float shadowStructureMultiplier;float reflectionGlowMultiplier;float lumaRec2100;float outputTransfer;}',
    )
    shader = replace_once(shader, '(nrFinal[q].rgb-low)*weight', '(nrFinal[q].rgb-(residualInput>0.5?float3(0,0,0):low))*weight')
    # The realtime path normally receives a residual that has already gone
    # through its low-resolution controls.  The local media bridge receives
    # raw NR output instead, so keep the same fine-control formulas in this
    # shared shader.  Neutral values are exact identity and therefore do not
    # change the realtime path.
    fine_controls = r'''
float3 NrLumaWeights() {
    return lumaRec2100>0.5?float3(0.2627,0.6780,0.0593):float3(0.2126,0.7152,0.0722);
}
// Same ST2084/HLG normalization as video_export's CPU reference. Only used
// when a same-card GPU sink consumes the output, never by realtime/CPU paths.
float3 NrEncodeOutput(float3 radiance) {
    if(outputTransfer<0.5) return radiance;
    float3 x=max(radiance,0);
    float mode=outputTransfer>=4?outputTransfer-4:outputTransfer;
    precise float3 coded;
    if(mode<1.5) {
        precise float3 v=pow(x,2610.0/16384.0);
        precise float3 numerator=3424.0/4096.0+(2413.0/128.0)*v;
        precise float3 denominator=1.0+(2392.0/128.0)*v;
        coded=saturate(pow(numerator/max(denominator,1e-7),2523.0/32.0));
    } else {
        float a=0.17883277,b=1.0-4.0*a,c=0.5-a*log(4.0*a);
        float3 low=sqrt(3.0*x),high=a*log(max(12.0*x-b,1e-7))+c;
        coded=saturate(float3(x.r<=1.0/12.0?low.r:high.r,x.g<=1.0/12.0?low.g:high.g,x.b<=1.0/12.0?low.b:high.b));
    }
    // Typed RGBA16F UAV stores may truncate; CPU astype(float16) rounds to
    // nearest-even. Explicitly quantize before storing to keep that contract.
    if(outputTransfer>=4) {
        // f32tof16 is permitted to truncate too. Find the adjacent half values
        // explicitly and select the nearest, choosing even at an exact tie.
        uint3 lo=f32tof16(coded);
        lo-=uint3(f16tof32(lo)>coded);
        precise float3 midpoint=(f16tof32(lo)+f16tof32(lo+1))*0.5;
        bool3 up=(coded>midpoint)|((coded==midpoint)&((lo&1)!=0));
        return f16tof32(lo+uint3(up));
    }
    return coded;
}
float3 NrToLinear(float3 color) {
    return float3(
        color.r <= 0.04045 ? color.r / 12.92 : pow(max(color.r + 0.055, 0.0) / 1.055, 2.4),
        color.g <= 0.04045 ? color.g / 12.92 : pow(max(color.g + 0.055, 0.0) / 1.055, 2.4),
        color.b <= 0.04045 ? color.b / 12.92 : pow(max(color.b + 0.055, 0.0) / 1.055, 2.4));
}
float3 NrRgbToHsl(float3 rgb) {
    float hi=max(rgb.r,max(rgb.g,rgb.b)), lo=min(rgb.r,min(rgb.g,rgb.b));
    float d=hi-lo, safe=max(d,1e-6), light=(hi+lo)*0.5;
    float hue=hi==rgb.r?(rgb.g-rgb.b)/safe:hi==rgb.g?(rgb.b-rgb.r)/safe+2:(rgb.r-rgb.g)/safe+4;
    hue=frac(hue/6.0+1.0);
    float sat=d/max(1.0-abs(2.0*light-1.0),1e-6);
    return float3(d>1e-6?hue:0,saturate(d>1e-6?sat:0),light);
}
float NrHue(float p,float q,float t) {
    t=frac(t+1.0);
    return t<1.0/6.0?p+(q-p)*6.0*t:t<0.5?q:t<2.0/3.0?p+(q-p)*(2.0/3.0-t)*6.0:p;
}
float3 NrHslToRgb(float3 hsl) {
    float h=hsl.x,s=hsl.y,l=hsl.z,q=l<0.5?l*(1+s):l+s-l*s,p=2*l-q;
    return saturate(s<=1e-6?float3(l,l,l):float3(NrHue(p,q,h+1.0/3.0),NrHue(p,q,h),NrHue(p,q,h-1.0/3.0)));
}
float3 NrApplyFine(float3 original,float3 residual) {
    float3 adjusted=residual*residualMultiplier;
    if(signedHdr>0.5) {
        if(residualMultiplier==1 && residualSaturation==1 && residualLightness==1 &&
           shadowStructureMultiplier==1 && reflectionGlowMultiplier==1) return original+residual;
        // Match hdr_fine_controls: classify in linear light, then split the
        // directed residual into luminance/chroma. Never use SDR HSL/clipping.
        float dy=dot(adjusted,NrLumaWeights());
        adjusted*=dy<0?shadowStructureMultiplier:(dy>0?reflectionGlowMultiplier:1);
        float luma=dot(adjusted,NrLumaWeights());
        return original+luma*residualLightness+(adjusted-luma)*residualSaturation;
    }
    float3 result=original+adjusted;
    bool nonNeutral=abs(residualSaturation-1.0)>=1e-6 || abs(residualLightness-1.0)>=1e-6 ||
        abs(shadowStructureMultiplier-1.0)>=1e-6 || abs(reflectionGlowMultiplier-1.0)>=1e-6;
    if(nonNeutral) {
        float3 candidate=original+adjusted;
        if(signedHdr<=0.5) candidate=saturate(candidate);
        float3 classification=signedHdr>0.5?adjusted:NrToLinear(candidate)-NrToLinear(original);
        float dy=dot(classification,float3(0.2126,0.7152,0.0722));
        float directional=dy<0?shadowStructureMultiplier:(dy>0?reflectionGlowMultiplier:1);
        adjusted*=directional;
        result=original+adjusted;
        if(signedHdr<=0.5 && (abs(residualSaturation-1.0)>=1e-6 || abs(residualLightness-1.0)>=1e-6)) {
            candidate=original+adjusted;
            candidate=saturate(candidate);
            float3 oldHsl=NrRgbToHsl(original), newHsl=NrRgbToHsl(candidate);
            newHsl.y=saturate(oldHsl.y+(newHsl.y-oldHsl.y)*residualSaturation);
            newHsl.z=saturate(oldHsl.z+(newHsl.z-oldHsl.z)*residualLightness);
            result=NrHslToRgb(newHsl);
        }
    }
    return signedHdr>0.5?result:saturate(result);
}
'''
    # Both global controls and reconstruction use the same declared primaries.
    shader = shader.replace('float3(0.2126,0.7152,0.0722)', 'NrLumaWeights()')
    shader = replace_once(shader, '[numthreads(16,16,1)] void main', fine_controls + '\n[numthreads(16,16,1)] void main')
    # Full-resolution NR needs no reconstruction: avoid four texture taps and
    # the interpolation arithmetic when its pixels already match the base.
    begin = shader.index('    float2 pos=')
    end = shader.index('    float3 rawDelta=delta;', begin)
    reconstruction = shader[begin:end].replace('float3 delta=0;', 'delta=0;')
    shader = shader[:begin] + '''    float3 delta=0;
    if(w==nw&&h==nh) {
        int2 q=int2(id.xy);
        delta=nrFinal[q].rgb-(residualInput>0.5?float3(0,0,0):nrInput[q].rgb);
    } else {
''' + ''.join('    ' + line for line in reconstruction.splitlines(keepends=True)) + '    }\n' + shader[end:]
    shader = replace_once(shader, 'float3 rawDelta=delta;', 'delta=NrApplyFine(base.rgb,delta)-base.rgb;float3 rawDelta=delta;')
    shader = shader.replace('float unused;', 'float signedHdr;')
    shader = shader.replace('unused>0.5?result:max(0,result)', 'signedHdr>0.5?result:max(0,result)')
    shader = replace_once(shader, 'outputTex[id.xy]=base;', 'outputTex[id.xy]=float4(NrEncodeOutput(base.rgb),base.a);')
    shader = replace_once(shader, 'float4(signedHdr>0.5?result:max(0,result),base.a)',
                          'float4(NrEncodeOutput(signedHdr>0.5?result:max(0,result)),base.a)')
    write_if_changed(output / 'VeyraResidual.hlsl', shader)
    lines = ["// Generated from Magpie resw resources; GPL-3.0.", "#pragma once",
             "#include <winrt/base.h>", "#include <string_view>", "#include <utility>",
             "inline winrt::hstring StandaloneString(std::wstring_view name, bool fallbackToKey = true) {",
             "  static const std::pair<std::wstring_view, std::wstring_view> strings[] = {"]
    resources = ET.parse(MAGPIE / "src/Magpie/Resources.language-zh-Hans.resw")
    for entry in resources.findall("data"):
        key = json.dumps(entry.attrib["name"], ensure_ascii=False)
        value = json.dumps(entry.findtext("value") or "", ensure_ascii=False)
        lines.append(f"    {{ L{key}, L{value} }},")
        # MRT maps XAML property dots to slash-qualified resource lookups.
        # The standalone table must provide those aliases as well.
        name, separator, prop = entry.attrib['name'].rpartition('.')
        if separator and prop in ('Header', 'Content', 'Description', 'Text', 'ToolTipService.ToolTip'):
            alias = json.dumps(name + '/' + prop, ensure_ascii=False)
            lines.append(f"    {{ L{alias}, L{value} }},")
    lines.append('    { L"EffectParam_DLSSNR_DLSSNR_AI_Filter_enableResidualControls_Label", L"全分辨率残差控制" },')
    lines += ["  };", "  for (const auto& [key, value] : strings)",
              "    if (key == name) return winrt::hstring(value);",
              "  return fallbackToKey ? winrt::hstring(name) : winrt::hstring();", "}"]
    write_if_changed(output / "standalone_resources.h", "\n".join(lines))
    # Parameter localization has its own resource entry point, separate from
    # ScalingWindow and fonts. MRT can fail-fast before C++ catch handlers in an
    # unpackaged host. Use our resource table; unknown translations stay intact.
    localization = (MAGPIE / 'src/Magpie.Core/include/EffectParameterLocalization.h').read_text(encoding='utf-8')
    begin = localization.index('\t\t\tusing namespace winrt::Windows::ApplicationModel::Resources;')
    end = localization.index('\t\t\tconst std::wstring prefix', begin)
    localization = localization[:begin] + localization[end:]
    localization = replace_once(localization, 'const auto value = loader.GetString(key);',
                                'const auto value = StandaloneString(key, false);')
    localization = localization.replace('#pragma once', '#pragma once\n#include "standalone_resources.h"', 1)
    write_if_changed(output / 'standalone_parameter_localization.h', localization)
    rules = (MAGPIE / 'src/Magpie.Core/include/EffectParameterRules.h').read_text(encoding='utf-8')
    rules = replace_once(rules, 'return getValue("enableInputResolutionScaling", 0.0f) != 0.0f;',
        'return getValue("enableInputResolutionScaling", 0.0f) != 0.0f ||\n'
        '\t\t\t(parameter != "inputResolutionPercent" && getValue("enableResidualControls", 0.0f) != 0.0f);')
    write_if_changed(output / 'standalone_parameter_rules.h', rules)
    source = (MAGPIE / "src/Magpie.Core/ScalingWindow.cpp").read_text(encoding="utf-8")
    old = ('ScalingWindow::ScalingWindow() noexcept :\n'
           '\t_resourceLoader(winrt::ResourceLoader::GetForViewIndependentUse(CommonSharedConstants::APP_RESOURCE_MAP_ID)) {}')
    if source.count(old) != 1 or source.count("return _resourceLoader.GetString(resName);") != 1:
        raise RuntimeError("Pinned Magpie resource adapter no longer matches; review upstream change")
    source = source.replace(old, '#include "standalone_resources.h"\nScalingWindow::ScalingWindow() noexcept {}')
    source = source.replace("return _resourceLoader.GetString(resName);", "return StandaloneString(resName);")
    source = source.replace('#include "standalone_resources.h"', '#include "standalone_resources.h"\n#include "app_icon.h"', 1)
    source = replace_once(source,
        'LRESULT ScalingWindow::_MessageHandler(UINT msg, WPARAM wParam, LPARAM lParam) noexcept {',
        '''LRESULT ScalingWindow::_MessageHandler(UINT msg, WPARAM wParam, LPARAM lParam) noexcept {
    Realtime::HandleOutputIconMessage(Handle(), msg, wParam);''')
    source = replace_once(source,
        'options.IsHdrCompatibilityEnabled(options.hdrComponents.enabled && options.hdrComponents.outputHdr);',
        'options.IsHdrCompatibilityEnabled(options.hdrComponents.enabled ? options.hdrComponents.outputHdr : options.IsHdrCompatibilityEnabled());')
    source = replace_once(source, 'ScalingError ScalingWindow::_StartImpl(HWND hwndSrc) noexcept {', '''ScalingError ScalingWindow::_StartImpl(HWND hwndSrc) noexcept {
    // This host exposes an explicit all-HDR path in addition to upstream's
    // conversion components. Check capture and every selected output, never
    // silently discard the user's HDR request or present SDR as HDR success.
    if (_options.IsHdrCompatibilityEnabled() && !_options.hdrComponents.enabled) {
        if (_options.captureMethod != CaptureMethod::GraphicsCapture) return ScalingError::HdrCaptureMethodRequired;
        const auto sourceMonitor = MonitorFromWindow(hwndSrc, MONITOR_DEFAULTTONEAREST);
        if (!IsHdrMonitorActive(sourceMonitor)) return ScalingError::HdrCaptureRequired;
        RECT sourceRect{}; GetWindowRect(hwndSrc, &sourceRect);
        bool checked = _options.IsWindowedMode() || _options.multiMonitorUsage == MultiMonitorUsage::Closest;
        if (!checked) for (const auto& monitor : Win32Helper::GetDisplayMonitors()) {
            bool selected = _options.multiMonitorUsage == MultiMonitorUsage::All;
            if (_options.multiMonitorUsage == MultiMonitorUsage::Specific)
                selected = _wcsicmp(monitor.deviceId.c_str(), _options.preferredMonitorId.c_str()) == 0;
            if (_options.multiMonitorUsage == MultiMonitorUsage::Intersected)
                selected = Win32Helper::IsRectOverlap(monitor.rect, sourceRect);
            if (!selected) continue;
            checked = true;
            if (!IsHdrMonitorActive(monitor.handle)) return ScalingError::HdrDisplayRequired;
        }
        if (!checked) return ScalingError::DisplayLayoutFailed;
    }''')
    write_if_changed(output / "ScalingWindow.cpp", source)
    path_header = r'''#pragma once
#include <windows.h>
#include <filesystem>
#include <string>
inline std::filesystem::path StandalonePath(const wchar_t* variable, const wchar_t* fallback) {
    DWORD n = GetEnvironmentVariableW(variable, nullptr, 0);
    if (n > 0 && n < 32768) {
        std::wstring value(n, L'\0');
        DWORD copied = GetEnvironmentVariableW(variable, value.data(), n);
        if (copied && copied < n) {
            value.resize(copied);
            auto result = std::filesystem::path(value);
            if (result.is_absolute()) return result;
        }
    }
    wchar_t executable[32768]{};
    GetModuleFileNameW(nullptr, executable, 32768);
    return std::filesystem::path(executable).parent_path() / fallback;
}
inline std::filesystem::path StandaloneRuntimeDirectory() {
    return StandalonePath(L"DLSS5_REALTIME_RUNTIME_ROOT", L"..");
}
inline std::filesystem::path StandaloneEffectsDirectory() {
    return StandalonePath(L"DLSS5_REALTIME_EFFECTS_ROOT", L"effects");
}
inline std::filesystem::path StandaloneNrRuntimePath() {
    return StandalonePath(L"DLSS5_REALTIME_NR_PATH", L"../nvngx_dlssnr.dll");
}
'''
    write_if_changed(output / "standalone_paths.h", path_header)
    for name in ("EffectCompiler.cpp", "NgxD3D12Core.cpp", "DLSSNRFilter.cpp", "OverlayDrawer.cpp", "ParameterInputHost.cpp", "Renderer.cpp", "NativeEffectBackendFactory.cpp", "DLSSNRMultiPass.h", "DLSSSRUpscaler.cpp", "GraphicsCaptureFrameSource.cpp", "DesktopDuplicationFrameSource.cpp"):
        text = (MAGPIE / "src/Magpie.Core" / name).read_text(encoding="utf-8")
        text = text.replace('#include "EffectParameterRules.h"', '#include "standalone_parameter_rules.h"')
        text = text.replace('#include "pch.h"', '#include "pch.h"\n#include "standalone_paths.h"', 1)
        if name == "EffectCompiler.cpp":
            text = text.replace("CommonSharedConstants::EFFECTS_DIR", "StandaloneEffectsDirectory().c_str()")
        else:
            text = text.replace("Win32Helper::GetExePath().parent_path()", "StandaloneRuntimeDirectory()")
        if name == 'DLSSNRFilter.cpp':
            text = text.replace('// Resolution scaling and residual reconstruction are SDR RGBA8 features.',
                '// Downscaling is SDR-only; full-resolution residual controls also support signed FP16 HDR.')
            text = text.replace('// Preserve the active HDR protocol while validating live SDR parameters.',
                '// Preserve the active HDR protocol while validating live parameters.')
            # Full-resolution HDR gets its own signed-linear fine-control path.
            # Never run the SDR downsampling/HSL kernels on FP16 HDR textures.
            hdr_helper = (ROOT / 'native/realtime/hdr_residual.hlsli').read_text(encoding='utf-8')
            text = replace_once(text, 'float3 RGBToHSL(float3 color) {', hdr_helper + '\nfloat3 RGBToHSL(float3 color) {')
            text = replace_once(text, 'float3 ApplyResidualControls(float3 original, float3 residual) {',
                '''float3 ApplyResidualControls(float3 original, float3 residual) {
    if (Padding0 != 0) return ApplyHdrResidualControls(original, residual, ResidualMultiplier,
        ShadowStructureMultiplier, ReflectionGlowMultiplier, ResidualSaturation, ResidualLightness);''')
            text = replace_once(text,
                'impl->useResolutionScaling = !experimentalHdrPath && settings.enableInputResolutionScaling;',
                'impl->useResolutionScaling = settings.enableInputResolutionScaling;')
            text = replace_once(text, 'settings.inputResolutionPercent, 25u, 100u);',
                'experimentalHdrPath ? 100u : settings.inputResolutionPercent, 25u, 100u);')
            begin = text.index('static bool CompositeResidual(')
            text = text[:begin] + replace_once(text[begin:],
                '\t\t.targetHeight = impl.height,',
                '\t\t.targetHeight = impl.height,\n\t\t.padding0 = impl.experimentalHdrPath ? 1u : 0u,')
            # Explicit opt-in preserves dormant controls in old presets. Reuse
            # residual resources at 100% without enabling input downscaling.
            text = replace_once(text,
                'getParameter("enableInputResolutionScaling", 0.0f) >= 0.5f,',
                '(getParameter("enableInputResolutionScaling", 0.0f) >= 0.5f ||\n'
                '\t\t\t getParameter("enableResidualControls", 0.0f) >= 0.5f),')
            text = replace_once(text,
                'getParameter("inputResolutionPercent", 100.0f))), 25, 100)),',
                '(getParameter("enableInputResolutionScaling", 0.0f) >= 0.5f ?\n'
                '\t\t\t\t getParameter("inputResolutionPercent", 100.0f) : 100.0f))), 25, 100)),')
            text = replace_once(text,
                '(parameterName == "enableInputResolutionScaling" || parameterName == "inputResolutionPercent" ||',
                '(parameterName == "enableResidualControls" || parameterName == "enableInputResolutionScaling" || parameterName == "inputResolutionPercent" ||')
            begin = text.index('\tif (_settings.experimentalHdr.enabled &&')
            end = text.index('\tif (parameterName == "style"', begin)
            text = text[:begin] + ('\tif (_settings.experimentalHdr.enabled && parameterName == "inputResolutionPercent")\n'
                '\t\treturn EffectParameterApplyMode::Unavailable;\n') + text[end:]
            text = text.replace('applicationDirectory / L"nvngx_dlssnr.dll"', 'StandaloneNrRuntimePath()')
            text = text.replace('#include "pch.h"', '#include "pch.h"\n#include "quality.h"\n#include "telemetry.h"', 1)
            text = replace_once(text, 'struct DLSSNRFilter::Impl {\n', 'struct DLSSNRFilter::Impl {\n\tRealtime::ResidualComposite veyraComposite;\n')
            # Keep per-pass controls; edge-guided reconstruction reads the signed
            # low-resolution residual. Global protection follows temporal processing.
            begin = text.index('static bool CompositeResidual(')
            end = text.index('DLSSNRFilter::DLSSNRFilter() = default;', begin)
            prepare_end = text.index('\tID3D11ShaderResourceView* verticalResidual', begin)
            text = text[:prepare_end] + '''
    winrt::com_ptr<ID3D11Resource> original;
    impl.inputSrv11->GetResource(original.put());
    auto base = original.as<ID3D11Texture2D>();
    return impl.veyraComposite.Draw(impl.device11, impl.context11, base.get(),
        impl.sharedInput11.get(), impl.controlledResidual11.get(), output, impl.experimentalHdrPath, false, true);
}

''' + text[end:]
            text = text.replace('residualUpsample=catmull-rom-4+4', 'residualUpsample=veyra-bilateral-2x2')
            text = replace_once(text, 'const bool evaluateSucceeded = !sehCode && NGXSucceeded(result);',
                                'const bool evaluateSucceeded = !sehCode && NGXSucceeded(result);\n\tRealtime::NrEvaluated(evaluateSucceeded);')
            text = replace_once(text, 'impl.evaluateGpuTimings.Add(gpuMs);', 'impl.evaluateGpuTimings.Add(gpuMs);\n\t\tRealtime::NrGpu(gpuMs);')
        if name == 'NativeEffectBackendFactory.cpp':
            text = replace_once(text, 'return { multiPass, nullptr, ScalingError::NoError,', 'return { true, nullptr, ScalingError::ScalingFailedGeneral,')
            text = text.replace('multiPass ? "none-multipass-rejected" : "pass-through"', '"none-rejected"')
        if name == 'DLSSSRUpscaler.cpp':
            text = replace_once(text, 'NVSDK_NGX_Result result = NVSDK_NGX_D3D11_Init_with_ProjectID(', '''const std::wstring featurePath = StandaloneRuntimeDirectory().wstring();
    const wchar_t* featurePaths[]{featurePath.c_str()};
    NVSDK_NGX_FeatureCommonInfo featureInfo{};
    featureInfo.PathListInfo.Path = featurePaths;
    featureInfo.PathListInfo.Length = 1;
    NVSDK_NGX_Result result = NVSDK_NGX_D3D11_Init_with_ProjectID(''')
            text = replace_once(text, '\t\tL"logs",\n\t\t_device\n', '\t\tL"logs",\n\t\t_device, &featureInfo\n')
        if name == 'DLSSNRMultiPass.h':
            text = text.replace('#pragma once', '#pragma once\n#include "quality.h"', 1)
            text = replace_once(text, '\t\t_hdr = hdr;', '''\t\t_hdr = hdr;
        _resources = &resources;
        D3D11_TEXTURE2D_DESC qualityDesc{};
        output->GetDesc(&qualityDesc);
        qualityDesc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS;
        qualityDesc.MiscFlags = 0;
        _qualityInput = nullptr;
        if (FAILED(resources.GetD3DDevice()->CreateTexture2D(&qualityDesc, nullptr, _qualityInput.put()))) return false;
        output = _qualityInput.get();''')
            text = replace_once(text, 'bool Draw(const NativeEffectDrawContext& context) noexcept override {', '''bool Draw(const NativeEffectDrawContext& originalContext) noexcept override {
        auto context = originalContext;
        context.output = _qualityInput.get();''')
            text = replace_once(text, 'return !_filters.empty() && (!_temporal || _temporal->Draw(context));', '''if (_filters.empty() || (_temporal && !_temporal->Draw(context))) return false;
        const auto revision = Realtime::qualityRevision.load();
        const bool result = _quality.Draw(_resources->GetD3DDevice(), _resources->GetD3DDC(),
            originalContext.input, originalContext.input, _qualityInput.get(), originalContext.output, _hdr);
        if (result) Realtime::appliedQualityRevision = revision;
        return result;''')
            text = replace_once(text, 'if (_filters.size() > 1 && !_filters[i]->IsHealthy())', 'if (!_filters[i]->IsHealthy())')
            text = replace_once(text, 'result.Add(MotionVectorRequest::Amd(AmdOpticalFlowMode::Quality));',
                                'result.Add(MotionVectorRequest::Nvidia(NvidiaOpticalFlowQuality::Balanced));')
            text = replace_once(text, '\tNgxD3D12Core* _core = nullptr;', '''\tRealtime::ResidualComposite _quality;
    DeviceResources* _resources = nullptr;
    winrt::com_ptr<ID3D11Texture2D> _qualityInput;
    NgxD3D12Core* _core = nullptr;''')
        if name == 'OverlayDrawer.cpp':
            text = replace_once(text, '#include "EffectParameterLocalization.h"',
                                '#include "standalone_parameter_localization.h"\n#include "safety_state.h"\n#include "guidance.h"')
            begin = text.index('static const std::wstring& GetAppLanguage() noexcept {')
            end = text.index('static const std::wstring& GetSystemFontsFolder()', begin)
            text = text[:begin] + 'static const std::wstring& GetAppLanguage() noexcept {\n\tstatic const std::wstring language = L"zh-hans";\n\treturn language;\n}\n\n' + text[end:]
            text = replace_once(text, '\t_overlayOptions = &overlayOptions;',
                                '\t_overlayOptions = &overlayOptions;\n\tRealtime::BindGuidanceOutput(ScalingWindow::Get().Handle());')
            text = replace_once(text, 'bool result = _isToolbarVisible || _isProfilerVisible ||',
                                'bool result = !Realtime::stopAvailable.load() || _isToolbarVisible || _isProfilerVisible ||')
            text = replace_once(text, '''\tif (_isToolbarVisible && _DrawToolbar(fps, itemId)) {
\t\tneedRedraw = true;
\t}''', '''\tif (_isToolbarVisible && _DrawToolbar(fps, itemId)) {
\t\tneedRedraw = true;
\t}
    const float stopAlpha = !Realtime::stopAvailable.load() ? 1.0f :
        (_isToolbarVisible ? std::max(0.0f, _lastToolbarAlpha) : 0.0f);
    Realtime::drawnToolbarAlpha = _isToolbarVisible ? _lastToolbarAlpha : 0.0f;
    Realtime::drawnStopAlpha = stopAlpha;
    Realtime::controlMode = !_isEffectParametersVisible ? 0 : IsEditingParameters() ? 1 : 2;
    Realtime::controlWindow = _hwndParameterInput;
    // A draw-list label has no native/ImGui input region and never steals clicks.
    if (stopAlpha > 0.0f) {
    const char* stopHint = Realtime::StopHint();
    const float stopWrap = std::max(40.0f, ImGui::GetIO().DisplaySize.x - 30.0f * _dpiScale);
    const ImVec2 stopSize = ImGui::CalcTextSize(stopHint, nullptr, false, stopWrap);
    const ImVec2 stopPos(12.0f * _dpiScale,
        std::max(0.0f, ImGui::GetIO().DisplaySize.y - stopSize.y - 18.0f * _dpiScale));
    auto* stopDraw = ImGui::GetForegroundDrawList();
    stopDraw->AddRectFilled({ stopPos.x - 6, stopPos.y - 4 },
        { stopPos.x + stopSize.x + 6, stopPos.y + stopSize.y + 4 }, IM_COL32(20, 24, 30, int(210 * stopAlpha)), 4);
    stopDraw->AddText(nullptr, 0.0f, stopPos, IM_COL32(255, 255, 255, int(255 * stopAlpha)), stopHint, nullptr, stopWrap);
    }''')
            text = replace_once(text, 'if (_isToolbarPinned || _isToolbarItemActive) {',
                                'if (_isToolbarPinned || _isToolbarItemActive || IsEditingParameters()) {')
            text = replace_once(text, '\tstd::optional<ImVec4> windowRect = _imguiImpl.GetWindowRect(TOOLBAR_WINDOW_ID);', '''
    // Relative/hidden game cursors must not reveal or operate controls by accident.
    // Explicit parameter editing has its own foreground input host and cursor.
    if (ScalingWindow::Get().Options().Is3DGameMode()) return Realtime::IntroAlpha();
\tstd::optional<ImVec4> windowRect = _imguiImpl.GetWindowRect(TOOLBAR_WINDOW_ID);''')
            text = replace_once(text,
                'ImGui::BeginDisabled(!IsEditingParameters() && !ScalingWindow::Get().CursorManager().CursorHandle());',
                'ImGui::BeginDisabled(!IsEditingParameters() && (ScalingWindow::Get().Options().Is3DGameMode() || !ScalingWindow::Get().CursorManager().CursorHandle()));')
            text = replace_once(text, '''\tif (!windowRect) {
\t\treturn 0.0f;
\t}''', '''\tif (!windowRect) {
\t\treturn Realtime::IntroAlpha();
\t}''')
            text = replace_once(text, 'return (40.0f - std::clamp(dist - 10.0f, 0.0f, 40.0f)) / 40.0f;',
                                'return std::max(Realtime::IntroAlpha(), (40.0f - std::clamp(dist - 10.0f, 0.0f, 40.0f)) / 40.0f);')
        if name == 'ParameterInputHost.cpp':
            # A read-only panel must not release a game's relative cursor merely
            # because its hidden position happens to intersect the panel.
            text = replace_once(text,
                '_imguiImpl.ParameterPreview(_isEffectParametersVisible && _parameterPanelState == ParameterPanelState::Preview);',
                '_imguiImpl.ParameterPreview(_isEffectParametersVisible && _parameterPanelState == ParameterPanelState::Preview && !ScalingWindow::Get().Options().Is3DGameMode());')
            text = replace_once(text,
                'if (_parameterPanelState != ParameterPanelState::Preview || !_isEffectParametersVisible ||',
                'if (ScalingWindow::Get().Options().Is3DGameMode() || _parameterPanelState != ParameterPanelState::Preview || !_isEffectParametersVisible ||')
        if name == 'Renderer.cpp':
            text = text.replace('#include "pch.h"', '#include "pch.h"\n#include "telemetry.h"\n#include "quality.h"\n#include "guidance.h"', 1)
            text = replace_once(text, 'if (!options.Is3DGameMode()) {',
                                'if (!options.Is3DGameMode() || options.isParameterFocusSwitchingEnabled) {')
            text = replace_once(text, 'if (window.Options().Is3DGameMode()) {', '''if (window.Options().Is3DGameMode() &&
        !(window.Options().isParameterFocusSwitchingEnabled &&
          (action == OverlayAction::EffectParameters || _overlayDrawer.IsEditingParameters()))) {''')
            text = replace_once(text, 'if (scalingWindow.Options().Is3DGameMode()) {',
                                'if (scalingWindow.Options().Is3DGameMode() && !_overlayDrawer.IsEditingParameters()) {')
            text = replace_once(text, 'if (count > 1) {\n\t\t\t\t\t_FailColorPipeline(',
                                'if (count >= 1) {\n\t\t\t\t\t_FailColorPipeline(')
            text = replace_once(text, '\tbool reflexCleanupStopQueued = false;',
                                '\tbool reflexCleanupStopQueued = false;\n\tuint64_t qualityRevision = Realtime::qualityRevision.load();')
            text = replace_once(text, '\t\tif (!_pendingEffectParameterUpdates.empty()) {', '''
        const auto revision = Realtime::qualityRevision.load();
        if (qualityRevision != revision) {
            qualityRevision = revision;
            _forceNextRender = true;
            bool followsNr = false;
            for (size_t i = 0; i < _runtimeEffectOptions.size(); ++i) {
                if (followsNr) ++_effectInputRevisions[i];
                followsNr |= _runtimeEffectOptions[i].name == "DLSSNR\\\\DLSSNR_AI_Filter";
            }
        }
\t\tif (!_pendingEffectParameterUpdates.empty()) {''')
            text = replace_once(text, '\n}\n\nbool Renderer::QueueEffectParameterUpdate(', '''
    std::lock_guard lock(Realtime::parameterMutex);
    Realtime::parameterModes.clear();
    for (uint32_t i = 0; i < _effectParameterRuntimeInfos.size(); ++i)
        for (const auto& info : _effectParameterRuntimeInfos[i])
            Realtime::parameterModes.push_back({i, info.name, int(info.applyMode)});
}

bool Renderer::QueueEffectParameterUpdate(''')
            text = replace_once(text, 'FrameTrace::Mark(FrameTrace::Event::CaptureAccepted, _frameSource->CaptureTimestamp100ns(), sequence);',
                                'FrameTrace::Mark(FrameTrace::Event::CaptureAccepted, _frameSource->CaptureTimestamp100ns(), sequence);\n\t\tRealtime::Captured(_capturedFrameId);')
            text = replace_once(text, 'const bool submitted = _presenter->EndFrame(waitForGpu);',
                                '''const bool submitted = _presenter->EndFrame(waitForGpu);
    if (submitted && contentFrame) {
        Realtime::Submitted(_frontendCaptureFrameId,
            _frontendFrameMetadata.timestamp100ns, generatedFrame, _presenter->LastPresentedFrameCount());
        Realtime::GuidancePresented(ScalingWindow::Get().Handle());
    }''')
            # Timing is backend CPU submission work, explicitly not GPU completion.
            text = replace_once(text, 'traceTransaction.End();', '''traceTransaction.End();
    Realtime::Processed(std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - transactionStart).count());''')
            begin = text.index('void Renderer::_EnsureGpuPriority(bool force) noexcept {')
            end = text.index('ScalingError Renderer::Initialize', begin)
            text = text[:begin] + '''void Renderer::_EnsureGpuPriority(bool force) noexcept {
    const auto now = std::chrono::steady_clock::now();
    if (!force && now < _nextGpuPriorityCheck) return;
    _nextGpuPriorityCheck = now + std::chrono::seconds(1);
    const auto desired = static_cast<D3DKMT_SCHEDULINGPRIORITYCLASS>(Realtime::gpuPriority.load());
    D3DKMT_SCHEDULINGPRIORITYCLASS actual{};
    if (D3DKMTGetProcessSchedulingPriorityClass(GetCurrentProcess(), &actual) == STATUS_SUCCESS && actual == desired) return;
    const auto result = D3DKMTSetProcessSchedulingPriorityClass(GetCurrentProcess(), desired);
    if (result != STATUS_SUCCESS && !_gpuPriorityFailureLogged) {
        Logger::Get().NTError("Requested GPU priority unavailable; leaving OS scheduling unchanged", result);
        _gpuPriorityFailureLogged = true;
    }
}

''' + text[end:]
        if name == 'GraphicsCaptureFrameSource.cpp':
            text = text.replace('#include "pch.h"', '#include "pch.h"\n#include "telemetry.h"', 1)
            text = replace_once(text, 'FrameTrace::Mark(FrameTrace::Event::WgcDequeue, dequeued, _captureSessionGeneration);',
                                'FrameTrace::Mark(FrameTrace::Event::WgcDequeue, dequeued, _captureSessionGeneration);\n\t\t\tRealtime::CaptureDrained(dequeued);')
        if name == 'DesktopDuplicationFrameSource.cpp':
            text = replace_once(text, 'FrameSourceState DesktopDuplicationFrameSource::_Update() noexcept {', '''FrameSourceState DesktopDuplicationFrameSource::_Update() noexcept {
    static thread_local uint32_t waitCount = 0;''')
            text = replace_once(text, 'if (hr == DXGI_ERROR_WAIT_TIMEOUT) {', '''if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        if (++waitCount % 2000 == 0) Logger::Get().Info("Desktop Duplication still waiting for a desktop frame");''')
            text = replace_once(text, '\t_isFrameAcquired = true;', '''\t_isFrameAcquired = true;
    if (!_captureTimestamp100ns) Logger::Get().Info(fmt::format(
        "Desktop Duplication first frame: metadata={} accumulated={} presentTime={}",
        info.TotalMetadataBufferSize, info.AccumulatedFrames, info.LastPresentTime.QuadPart));''')
            # The first acquired image is valid even if a virtual/remote output
            # does not supply dirty-rect metadata. Never start from an undefined texture.
            text = replace_once(text, 'bool noUpdate = true;', 'bool noUpdate = _captureTimestamp100ns != 0 && info.TotalMetadataBufferSize != 0;')
            text = replace_once(text, '\td3dDC->CopySubresourceRegion(', '''
    LARGE_INTEGER frequency{}; QueryPerformanceFrequency(&frequency);
    _captureTimestamp100ns = info.LastPresentTime.QuadPart > 0
        ? int64_t(double(info.LastPresentTime.QuadPart) * 10000000.0 / frequency.QuadPart) : 1;
\td3dDC->CopySubresourceRegion(''')
        write_if_changed(output / name, text)


if __name__ == "__main__":
    generate(Path(sys.argv[1]).resolve())
