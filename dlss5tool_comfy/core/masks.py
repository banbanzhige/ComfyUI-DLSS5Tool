"""Arbitrary-shape MASK validation and full-image Gaussian feathering."""

import math

import numpy as np


def validate_images(value):
    import torch

    if not isinstance(value, torch.Tensor) or value.ndim != 4 or value.shape[-1] != 3:
        raise ValueError("IMAGE must be a floating RGB tensor [B,H,W,3]")
    if not value.is_floating_point() or min(value.shape[:3]) < 1:
        raise ValueError("IMAGE dimensions must be positive and dtype must be floating point")
    snapshot = value.detach().to(device="cpu", dtype=torch.float32).clone().contiguous()
    if not bool(torch.isfinite(snapshot).all()):
        raise ValueError("IMAGE contains NaN or Inf")
    return snapshot


def validate_mask(value, shape, name):
    import torch

    batch, height, width = shape[:3]
    if value is None:
        return torch.zeros((batch, height, width), dtype=torch.float32)
    if not isinstance(value, torch.Tensor) or value.ndim != 3:
        raise ValueError(f"{name} must be a tensor [B,H,W]")
    if tuple(value.shape[1:]) != (height, width) or value.shape[0] not in (1, batch):
        raise ValueError(f"{name} dimensions must match IMAGE; only B=1 may broadcast")
    mask = value.detach().to(device="cpu", dtype=torch.float32)
    if not bool(torch.isfinite(mask).all()):
        raise ValueError(f"{name} contains NaN or Inf")
    return mask.clamp(0, 1).expand(batch, height, width).clone().contiguous()


def feather(mask, radius):
    """radius px = sigma; truncate at 3 sigma, reflect at full-image edges.

    radius=0 preserves existing soft edges exactly. No per-DLSS-tile filtering.
    """
    if type(radius) is not int or not 0 <= radius <= 256:
        raise ValueError("mask_feather must be an integer from 0 to 256")
    if radius == 0:
        return mask
    from scipy.ndimage import gaussian_filter

    return np.clip(gaussian_filter(mask, sigma=(0, radius, radius),
                                   radius=(0, math.ceil(3 * radius), math.ceil(3 * radius)),
                                   mode="reflect"), 0, 1)
