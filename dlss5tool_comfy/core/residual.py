"""Extracted residual_composition.py; provenance is in the implementation record.

Source: banbanzhige/DLSS5Tool; fixed handoff source/sha256 manifest.
Retain LICENSE and licenses/DLSS5Tool-original-MIT.txt with this module.
Desktop settings and optional native-composition paths are excluded.
"""

import numpy as np


def _hsl(rgb):
    hi, lo = rgb.max(-1), rgb.min(-1)
    delta = hi-lo
    safe = np.maximum(delta, 1e-6)
    light = (hi+lo)*.5
    r, g, b = np.moveaxis(rgb, -1, 0)
    hue = np.where(hi == r, (g-b)/safe, np.where(hi == g, (b-r)/safe+2, (r-g)/safe+4))/6
    saturation = delta/np.maximum(1-np.abs(2*light-1), 1e-6)
    return np.stack((np.where(delta > 1e-6, hue % 1, 0),
                     np.where(delta > 1e-6, np.clip(saturation, 0, 1), 0), light), -1)


def _rgb(hsl):
    h, s, l = np.moveaxis(hsl, -1, 0)
    q = np.where(l < .5, l*(1+s), l+s-l*s)
    p = 2*l-q
    def channel(offset):
        t = (h+offset) % 1
        return np.where(t < 1/6, p+(q-p)*6*t,
                        np.where(t < .5, q, np.where(t < 2/3, p+(q-p)*(2/3-t)*6, p)))
    return np.clip(np.stack((channel(1/3), channel(0), channel(-1/3)), -1), 0, 1)


def fine_controls(base, output, r):
    if all(v == 1 for v in r.values()):
        return output
    delta = (output-base)*r['residualMultiplier']
    candidate = np.clip(base+delta, 0, 1)
    def linear(x):
        return np.where(x <= .04045, x/12.92, (np.maximum(x+.055, 0)/1.055)**2.4)
    dy = (linear(candidate)-linear(base)) @ np.array([.2126, .7152, .0722], np.float32)
    direction = np.where(dy < 0, r['shadowStructureMultiplier'],
                         np.where(dy > 0, r['reflectionGlowMultiplier'], 1))
    candidate = np.clip(base+delta*direction[..., None], 0, 1)
    if r['residualSaturation'] != 1 or r['residualLightness'] != 1:
        original, adjusted = _hsl(base), _hsl(candidate)
        adjusted[..., 1:] = np.clip(original[..., 1:] + (adjusted[..., 1:]-original[..., 1:]) *
                                   [r['residualSaturation'], r['residualLightness']], 0, 1)
        candidate = _rgb(adjusted)
    return np.where(np.any(delta != 0, axis=-1, keepdims=True), candidate, base)


def hdr_fine_controls(base, output, r, weights=(.2126, .7152, .0722)):
    if all(v == 1 for v in r.values()):
        return output
    delta = (output-base)*r['residualMultiplier']
    dy = delta @ np.asarray(weights, np.float32)
    direction = np.where(dy < 0, r['shadowStructureMultiplier'],
                         np.where(dy > 0, r['reflectionGlowMultiplier'], 1))
    delta *= direction[..., None]
    luma = (delta @ np.asarray(weights, np.float32))[..., None]
    return base + luma*r['residualLightness'] + (delta-luma)*r['residualSaturation']
