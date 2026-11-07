"""Explicit sRGB / scRGB / BT.2100 contracts, evaluated in float32 or better.

PQ and HLG are display-linear for composition. HLG includes the 1000-nit
reference-display OOTF used by the target ComfyUI ImageColorSpace node.
"""

from dataclasses import fields
import math

import numpy as np

from .contracts import ColorInfo


PROFILES = {"srgb": 0, "scrgb": 1, "hdr10_pq": 2, "hdr10_hlg": 3}
COMFY_SPACES = {"sRGB": "srgb", "linear": "scrgb", "HDR PQ": "hdr10_pq", "HDR": "hdr10_hlg"}
M1, M2 = 2610 / 16384, 2523 / 32
C1, C2, C3 = 3424 / 4096, 2413 / 128, 2392 / 128
A, B, C = .17883277, .28466892, .55991072928
REC2020_TO_709 = np.array([
    [1.6604910021084338, -.5876411387885494, -.0728498633198846],
    [-.1245504745215905, 1.1328998971259600, -.0083494226043695],
    [-.0181507633549053, -.1005788980080076, 1.1187296613629125],
], dtype=np.float32)
REC709_TO_2020 = np.linalg.inv(REC2020_TO_709).astype(np.float32)


def validate_color(value):
    if value is None:
        return ColorInfo()
    if isinstance(value, dict):
        if set(value) - {f.name for f in fields(ColorInfo)} or not value.get("source"):
            raise ValueError("color_info needs an explicit profile and a trusted source")
        if "profile" not in value:
            raise ValueError("color_info.profile is required")
        value = ColorInfo(**value)
    if not isinstance(value, ColorInfo) or value.profile not in PROFILES:
        raise ValueError("Unsupported DLSS5_COLOR_INFO profile")
    if not isinstance(value.source, str) or not value.source.strip():
        raise ValueError("color_info.source must describe the colour information source")
    for name in ("white_nits", "peak_nits", "hlg_gamma"):
        number = getattr(value, name)
        if type(number) not in (float, int) or not math.isfinite(number) or number <= 0:
            raise ValueError(f"color_info.{name} must be finite and positive")
    if not .5 <= value.hlg_gamma <= 3 or max(value.peak_nits, value.white_nits) > 10000:
        raise ValueError("Unsupported HDR reference luminance or HLG display gamma")
    return value


def weights(info):
    return np.array((.2627, .6780, .0593) if info.profile in ("hdr10_pq", "hdr10_hlg")
                    else (.2126, .7152, .0722), dtype=np.float32)


def decode(rgb, info):
    """Convert stored RGB to linear light; HDR uses cd/m² in its own gamut."""
    rgb = np.asarray(rgb, dtype=np.float32)
    if info.profile == "srgb":
        return np.where(rgb <= .04045, rgb / 12.92,
                        (np.maximum(rgb + .055, 0) / 1.055) ** 2.4)
    if info.profile == "scrgb":
        return rgb * info.white_nits
    if info.profile == "hdr10_pq":
        # Evaluate around unity to keep the small denominator accurate.
        p = np.expm1(np.log(np.maximum(rgb.astype(np.float64), 1e-100)) / M2)
        linear = (np.maximum(p + 1 - C1, 0) / np.maximum((C2 - C3) - C3 * p, 1e-15)) ** (1 / M1)
        return (linear * 10000).astype(np.float32)
    rgb = np.maximum(rgb, 0)
    scene = np.where(rgb <= .5, rgb * rgb / 3,
                     (np.exp((np.maximum(rgb, .5) - C) / A) + B) / 12)
    luma = np.maximum(scene @ weights(info), 1e-12)[..., None]
    return scene * luma ** (info.hlg_gamma - 1) * info.peak_nits


def encode(linear, info):
    linear = np.asarray(linear, dtype=np.float32)
    if info.profile == "srgb":
        return np.where(linear <= .0031308, linear * 12.92,
                        1.055 * np.maximum(linear, 0) ** (1 / 2.4) - .055)
    if info.profile == "scrgb":
        return linear / info.white_nits
    rgb = np.maximum(linear, 0)
    if info.profile == "hdr10_pq":
        p = (rgb.astype(np.float64) / 10000) ** M1
        return (((C1 + C2 * p) / (1 + C3 * p)) ** M2).astype(np.float32)
    display = rgb / info.peak_nits
    luma = np.maximum(display @ weights(info), 1e-12)[..., None]
    scene = display * luma ** (1 / info.hlg_gamma - 1)
    return np.where(scene <= 1 / 12, np.sqrt(3 * scene),
                    A * np.log(np.maximum(12 * scene - B, 1e-12)) + C)


def sdr_preview(rgb, info):
    if info.profile == "srgb":
        return np.clip(rgb, 0, 1)
    light = decode(rgb, info)
    if info.profile in ("hdr10_pq", "hdr10_hlg"):
        light = light @ REC2020_TO_709.T
    light = np.maximum(light / info.white_nits, 0)
    luma = (light @ np.array((.2126, .7152, .0722), np.float32))[..., None]
    # A preview-only luminance shoulder. Formal HDR outputs never pass here.
    mapped = light / (1 + luma)
    return np.clip(encode(mapped, ColorInfo()), 0, 1)


def native_input(rgb, info):
    """Use PQ/Rec.2020 for HDR NR; direct linear input is clipped by the DLL.

    The native texture is still RGBA16F. No global exposure normalization or
    8-bit packing is used. Negative linear values survive through the source
    anchor and the signed linear rendering residual in native_output().
    """
    if not info.hdr:
        if np.min(rgb) < 0 or np.max(rgb) > 1:
            raise ValueError("sRGB IMAGE is outside 0–1; provide trusted HDR color_info or use a supported colour producer")
        return rgb
    linear = decode(rgb, info)
    if info.profile == "scrgb":
        linear = linear @ REC709_TO_2020.T
    if not np.isfinite(linear).all() or np.max(linear) > 10000:
        raise ValueError("HDR exceeds the native PQ transport range of 10000 cd/m²; no exposure rescale was applied")
    return encode(np.maximum(linear, 0), ColorInfo("hdr10_pq"))


def native_output(source, sent, rendered, info):
    if not info.hdr:
        return rendered
    pq = ColorInfo("hdr10_pq")
    # Compare against the texture the DLL actually received, not pre-pack
    # float32 values. This prevents half-precision quantization becoming an
    # unintended residual and preserves source values at a zero residual.
    received = sent.astype(np.float16).astype(np.float32)
    delta = decode(rendered, pq) - decode(received, pq)
    if info.profile == "scrgb":
        delta = delta @ REC2020_TO_709.T
    output = encode(decode(source, info) + delta, info)
    return np.where(np.all(delta == 0, axis=-1, keepdims=True), source, output).astype(np.float32)
