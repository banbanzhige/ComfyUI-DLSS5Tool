"""Single-pass appearance composition followed by mix and protection.

Global-control math and fine controls follow the fixed DLSS5Tool source.
HDR protection and output mixing use the declared source's linear light.
"""

import math

import numpy as np

from .color import decode, encode, weights
from .contracts import RESIDUAL_FIELDS, QUALITY_FIELDS
from .masks import validate_mask, feather
from .residual import fine_controls, hdr_fine_controls


def number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and between {low} and {high}")
    return float(value)


def validate_options(options):
    expected = {*RESIDUAL_FIELDS, *QUALITY_FIELDS, "output_mix", "mask_feather"}
    if set(options) - expected:
        raise ValueError("Unknown Adjust parameter")
    values = {name: number(options.get(name, 1.0), 1 if name == "residualMultiplier" else 0,
                           5 if name == "output_mix" else 2, name)
              for name in (*RESIDUAL_FIELDS, *QUALITY_FIELDS, "output_mix")}
    radius = options.get("mask_feather", 0)
    if type(radius) is not int or not 0 <= radius <= 256:
        raise ValueError("mask_feather must be an integer from 0 to 256")
    values["mask_feather"] = radius
    return values


def global_controls(base, output, q, luma_weights):
    raw = output - base
    delta = np.minimum(raw, 0) * q["darken"] + np.maximum(raw, 0) * q["brighten"]
    if q["color"] != 1 or q["luminance"] != 1:
        dy = (delta @ luma_weights)[..., None]
        delta = dy * q["luminance"] + (delta - dy) * q["color"]
    delta *= q["total"]
    anchor_delta = np.minimum(raw, 0)
    anchor = base + anchor_delta
    result = base + delta
    guard = (base >= 0) & (anchor > 0) & (delta < anchor_delta)
    result[guard] = anchor[guard] / (1 + (anchor_delta[guard] - delta[guard]) / anchor[guard])
    return result


def compose(result, options, protect_mask=None, cancel=None):
    import torch

    values = validate_options(options)
    cancel = cancel or (lambda: None)
    mask = (None if protect_mask is None else
            feather(validate_mask(protect_mask, result.source.shape, "protect_mask").numpy(),
                    values["mask_feather"]))
    q = {key: values[key] for key in QUALITY_FIELDS}
    r = {key: values[key] for key in RESIDUAL_FIELDS}
    source_only = (q["total"] == 0 or q["darken"] == q["brighten"] == 0
                   or q["color"] == q["luminance"] == 0 or values["output_mix"] == 0)
    if source_only:
        return result.source.clone()
    neutral = all(values[key] == 1 for key in (*RESIDUAL_FIELDS, *QUALITY_FIELDS, "output_mix"))
    if neutral and mask is None:
        return result.rendered.clone()
    frames = []
    for i, (source, rendered) in enumerate(zip(result.source.numpy(), result.rendered.numpy())):
        cancel()
        if neutral:
            adjusted = rendered.copy()
        else:
            base, output = (decode(source, result.color), decode(rendered, result.color)) if result.color.hdr else (source, rendered)
            fine = (hdr_fine_controls(base, output, r, weights(result.color)) if result.color.hdr
                    else fine_controls(base, output, r))
            output = global_controls(base, fine, q, weights(result.color))
            output = base + (output - base) * values["output_mix"]
            adjusted = encode(output, result.color) if result.color.hdr else np.clip(output, 0, 1)
        if mask is not None:
            protection = mask[i, ..., None]
            if result.color.hdr:
                light = decode(adjusted, result.color) * (1 - protection) + decode(source, result.color) * protection
                blended = encode(light, result.color)
            else:
                blended = adjusted * (1 - protection) + source * protection
            # Exact endpoints preserve PQ near-black and signed/over-white
            # linear values, including the all-white identity case.
            adjusted = np.where(protection == 1, source,
                                np.where(protection == 0, adjusted, blended))
        if not np.isfinite(adjusted).all():
            raise ValueError("Composition exceeded the finite colour range")
        frames.append(torch.from_numpy(np.ascontiguousarray(adjusted, dtype=np.float32)))
    return torch.stack(frames)
