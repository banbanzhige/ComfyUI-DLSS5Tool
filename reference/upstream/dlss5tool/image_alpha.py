"""Still-image alpha stays outside the opaque inference/video contracts."""
import cv2
import numpy as np


ALPHA_EXTENSIONS = {'.png', '.webp', '.tif', '.tiff'}


def read_still(path):
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None, None
    if not data.size:
        return None, None
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None:
        return None, None
    if image.ndim != 3 or image.shape[2] != 4:
        # Keep OpenCV's existing SDR conversion and EXIF orientation for opaque media.
        return cv2.imdecode(data, cv2.IMREAD_COLOR), None
    # Standalone images retain the existing SDR8 processing contract.
    if image.dtype == np.uint16:
        image = (image >> 8).astype(np.uint8)
    elif image.dtype != np.uint8:
        return cv2.imdecode(data, cv2.IMREAD_COLOR), None
    alpha = image[..., 3].copy()
    return image[..., :3].copy(), alpha if np.any(alpha != 255) else None


def inference_color(bgr, alpha):
    """Bleed nearby visible colors into hidden pixels; never feed checkerboard.

    Work in bounded row strips, so large PNGs do not need a full float RGB copy.
    Only fully transparent pixels change. Semi-transparent foreground is straight
    (not premultiplied) color, as expected by the opaque model input.
    """
    if alpha is None:
        return bgr
    result = bgr.copy()
    radius = 4
    for y in range(0, bgr.shape[0], 128):
        end = min(y + 128, bgr.shape[0])
        lo, hi = max(0, y-radius), min(bgr.shape[0], end+radius)
        weights = alpha[lo:hi].astype(np.float32) / 255
        denominator = cv2.boxFilter(weights, -1, (9, 9), normalize=False)
        hidden = alpha[y:end] == 0
        local = slice(y-lo, end-lo)
        divisor = denominator[local]
        for channel in range(3):
            colors = cv2.boxFilter(bgr[lo:hi, :, channel].astype(np.float32)*weights,
                                   -1, (9, 9), normalize=False)[local]
            filled = np.full(colors.shape, 127., np.float32)
            np.divide(colors, divisor, out=filled, where=divisor > 1e-6)
            result[y:end, :, channel][hidden] = np.clip(np.rint(filled[hidden]), 0, 255).astype(np.uint8)
    return result


def resize_alpha(alpha, size):
    if alpha.shape[::-1] == tuple(size):
        return alpha
    interpolation = cv2.INTER_AREA if size[0] < alpha.shape[1] or size[1] < alpha.shape[0] else cv2.INTER_LINEAR
    return cv2.resize(alpha, tuple(size), interpolation=interpolation)


def restore_alpha(bgr, alpha):
    if alpha is None:
        return bgr
    return np.dstack((bgr, resize_alpha(alpha, (bgr.shape[1], bgr.shape[0]))))


def checkerboard(bgr, alpha):
    """Composite only display-sized pixels; never persist the preview background."""
    h, w = bgr.shape[:2]
    weight = alpha.astype(np.float32)[..., None] / 255
    cells = ((np.arange(h)[:, None] // 12 + np.arange(w)[None, :] // 12) % 2)
    background = (cells * 32 + 160).astype(np.uint8)[..., None]
    return np.clip(np.rint(bgr * weight + background * (1-weight)), 0, 255).astype(np.uint8)


def resize_preview(bgr, alpha, size, interpolation):
    """Premultiplied resampling prevents hidden RGB from bleeding into edges."""
    alpha = resize_alpha(alpha, (bgr.shape[1], bgr.shape[0]))
    weight = cv2.resize(alpha.astype(np.float32), size, interpolation=interpolation)
    result = np.zeros((size[1], size[0], 3), np.uint8)
    for channel in range(3):
        weighted = bgr[..., channel].astype(np.float32) * alpha
        color = cv2.resize(weighted, size, interpolation=interpolation)
        np.divide(color, weight, out=color, where=weight > 1e-6)
        result[..., channel] = np.clip(np.rint(color), 0, 255).astype(np.uint8)
    return checkerboard(result, np.clip(np.rint(weight), 0, 255).astype(np.uint8))
